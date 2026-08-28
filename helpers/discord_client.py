import asyncio
import aiohttp
import os
import time
from typing import Optional

DISCORD_API_BASE = "https://discord.com/api/v10"


def normalize_allowed_servers(servers) -> set[str]:
    """Normalize configured guild IDs for authorization comparisons."""
    return {str(server_id) for server_id in (servers or []) if server_id is not None}


def require_allowed_guild(guild_id, allowed_servers) -> None:
    """Deny guild-scoped access when a configured allowlist does not match."""
    allowed = normalize_allowed_servers(allowed_servers)
    if not allowed:
        return

    actual_guild_id = str(guild_id or "")
    if not actual_guild_id or actual_guild_id not in allowed:
        raise PermissionError("Target is not in the allowed servers list.")


async def require_allowed_target(client, target_id: str, allowed_servers) -> Optional[dict]:
    """Resolve a channel/thread's actual guild before allowing resource access."""
    allowed = normalize_allowed_servers(allowed_servers)
    if not allowed:
        return None

    channel = await client.get_channel(target_id)
    require_allowed_guild(channel.get("guild_id"), allowed)
    return channel


def get_discord_config(agent=None):
    """Load Discord config through the plugin framework with env var overrides."""
    try:
        from helpers import plugins
        config = plugins.get_plugin_config("discord", agent=agent) or {}
    except Exception:
        config = {}

    # Environment variables override file config
    if os.environ.get("DISCORD_BOT_TOKEN"):
        config.setdefault("bot", {})["token"] = os.environ["DISCORD_BOT_TOKEN"]
    if os.environ.get("DISCORD_USER_TOKEN"):
        config.setdefault("user", {})["token"] = os.environ["DISCORD_USER_TOKEN"]
    return config


class RateLimiter:
    """Respects Discord's rate limit headers."""

    def __init__(self):
        self._limits: dict[str, float] = {}

    async def wait(self, bucket: str):
        now = time.monotonic()
        if bucket in self._limits and self._limits[bucket] > now:
            await asyncio.sleep(self._limits[bucket] - now)

    def update(self, bucket: str, headers: dict):
        remaining = headers.get("X-RateLimit-Remaining")
        reset_after = headers.get("X-RateLimit-Reset-After")
        if remaining is not None and int(remaining) == 0 and reset_after:
            self._limits[bucket] = time.monotonic() + float(reset_after)


class DiscordClient:
    """Lightweight Discord REST API client supporting bot and user token modes."""

    def __init__(self, token: str, is_bot: bool = True):
        self.token = token
        self.is_bot = is_bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter()

    @classmethod
    def from_config(cls, agent=None, mode: str = "bot") -> "DiscordClient":
        config = get_discord_config(agent)
        if mode == "bot":
            token = config.get("bot", {}).get("token")
            if not token:
                raise ValueError(
                    "Bot token not configured. Set DISCORD_BOT_TOKEN env var "
                    "or configure in Discord plugin settings."
                )
            return cls(token=token, is_bot=True)
        elif mode == "user":
            token = config.get("user", {}).get("token")
            if not token:
                raise ValueError(
                    "User token not configured. Set DISCORD_USER_TOKEN env var "
                    "or configure in Discord plugin settings."
                )
            return cls(token=token, is_bot=False)
        else:
            raise ValueError(f"Unknown mode: {mode}. Use 'bot' or 'user'.")

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            auth_prefix = "Bot " if self.is_bot else ""
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"{auth_prefix}{self.token}",
                    "Content-Type": "application/json",
                    "User-Agent": "AgentZero-DiscordPlugin/1.0",
                }
            )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, endpoint: str, **kwargs) -> dict | list | None:
        await self._ensure_session()
        url = f"{DISCORD_API_BASE}{endpoint}"
        bucket = f"{method}:{endpoint.split('?')[0]}"

        await self._rate_limiter.wait(bucket)

        async with self._session.request(method, url, **kwargs) as resp:
            self._rate_limiter.update(bucket, dict(resp.headers))

            if resp.status == 204:
                return None
            if resp.status == 429:
                retry_after = (await resp.json()).get("retry_after", 1.0)
                await asyncio.sleep(retry_after)
                return await self._request(method, endpoint, **kwargs)
            if resp.status >= 400:
                body = await resp.text()
                raise DiscordAPIError(resp.status, body, endpoint)

            return await resp.json()

    def _assert_bot_only(self, action: str):
        if not self.is_bot:
            raise PermissionError(
                f"Action '{action}' is restricted to bot accounts only. "
                "User tokens are read-only to comply with Discord ToS."
            )

    # --- Guild / Server ---

    async def get_guild(self, guild_id: str) -> dict:
        return await self._request("GET", f"/guilds/{guild_id}")

    async def get_guild_channels(self, guild_id: str) -> list:
        return await self._request("GET", f"/guilds/{guild_id}/channels")

    async def get_guild_members(self, guild_id: str, limit: int = 100, after: str = "0") -> list:
        return await self._request(
            "GET", f"/guilds/{guild_id}/members?limit={min(limit, 1000)}&after={after}"
        )

    async def get_guild_member(self, guild_id: str, user_id: str) -> dict:
        return await self._request("GET", f"/guilds/{guild_id}/members/{user_id}")

    # --- Channels ---

    async def get_channel(self, channel_id: str) -> dict:
        return await self._request("GET", f"/channels/{channel_id}")

    async def get_channel_messages(
        self, channel_id: str, limit: int = 50,
        before: Optional[str] = None, after: Optional[str] = None,
    ) -> list:
        params = f"?limit={min(limit, 100)}"
        if before:
            params += f"&before={before}"
        if after:
            params += f"&after={after}"
        return await self._request("GET", f"/channels/{channel_id}/messages{params}")

    async def get_all_channel_messages(
        self, channel_id: str, limit: int = 200, after: Optional[str] = None,
    ) -> list:
        """Fetch up to `limit` messages with automatic pagination."""
        all_messages = []
        before_id = None

        while len(all_messages) < limit:
            batch_size = min(100, limit - len(all_messages))
            params = f"?limit={batch_size}"
            if before_id:
                params += f"&before={before_id}"
            if after and not before_id:
                params += f"&after={after}"

            batch = await self._request("GET", f"/channels/{channel_id}/messages{params}")
            if not batch:
                break
            all_messages.extend(batch)
            before_id = batch[-1]["id"]
            if len(batch) < batch_size:
                break

        return all_messages[:limit]

    # --- Threads ---

    async def get_active_threads(self, guild_id: str) -> dict:
        return await self._request("GET", f"/guilds/{guild_id}/threads/active")

    async def get_channel_threads(self, channel_id: str) -> dict:
        return await self._request("GET", f"/channels/{channel_id}/threads/archived/public")

    # --- Sending (bot only) ---

    async def send_message(
        self, channel_id: str, content: str, reply_to: Optional[str] = None,
    ) -> dict:
        self._assert_bot_only("send_message")
        payload = {"content": content}
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to}
        return await self._request("POST", f"/channels/{channel_id}/messages", json=payload)

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self._assert_bot_only("add_reaction")
        await self._request(
            "PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"
        )

    # --- Search ---

    async def search_messages(
        self, guild_id: str, query: str,
        channel_id: Optional[str] = None, author_id: Optional[str] = None, limit: int = 25,
    ) -> dict:
        params = f"?content={query}&limit={min(limit, 25)}"
        if channel_id:
            params += f"&channel_id={channel_id}"
        if author_id:
            params += f"&author_id={author_id}"
        return await self._request("GET", f"/guilds/{guild_id}/messages/search{params}")

    # --- Users ---

    async def get_current_user(self) -> dict:
        return await self._request("GET", "/users/@me")

    async def get_user(self, user_id: str) -> dict:
        return await self._request("GET", f"/users/{user_id}")

    async def get_current_user_guilds(self) -> list:
        return await self._request("GET", "/users/@me/guilds")


def get_modes_to_try(config, explicit_mode=None):
    """Get ordered list of auth modes to try.

    If explicit_mode is set, only that mode is returned.
    Otherwise, primary mode first, then fallback if available.
    This enables automatic bot->user fallback on 403 errors.
    """
    if explicit_mode and explicit_mode in ("bot", "user"):
        return [explicit_mode]

    has_bot = bool(config.get("bot", {}).get("token"))
    has_user = bool(config.get("user", {}).get("token"))

    if has_bot and has_user:
        return ["bot", "user"]
    elif has_bot:
        return ["bot"]
    elif has_user:
        return ["user"]
    else:
        return ["bot"]  # Will fail with clear "no token" error


class DiscordAPIError(Exception):
    def __init__(self, status: int, body: str, endpoint: str):
        self.status = status
        self.body = body
        self.endpoint = endpoint
        super().__init__(f"Discord API error {status} on {endpoint}: {body}")


def format_messages(messages: list, include_ids: bool = False) -> str:
    """Format Discord messages into readable text for LLM consumption.

    All external content (usernames, message text, embeds, filenames) is
    sanitized to neutralise prompt injection attempts.
    """
    from usr.plugins.discord.helpers.sanitize import (
        sanitize_content, sanitize_username, sanitize_embed, sanitize_filename,
    )

    lines = []
    for msg in reversed(messages):  # Chronological order
        author = msg.get("author", {})
        username = sanitize_username(
            author.get("global_name") or author.get("username", "Unknown")
        )
        timestamp = msg.get("timestamp", "")[:19].replace("T", " ")
        content = sanitize_content(msg.get("content", ""))

        embeds = msg.get("embeds", [])
        embed_text = ""
        if embeds:
            parts = []
            for embed in embeds:
                if embed.get("title"):
                    parts.append(f"[Embed: {sanitize_embed(embed['title'], max_length=256)}]")
                if embed.get("description"):
                    parts.append(sanitize_embed(embed["description"]))
            embed_text = " " + " | ".join(parts) if parts else ""

        attachments = msg.get("attachments", [])
        attach_text = ""
        if attachments:
            names = [sanitize_filename(a.get("filename", "file")) for a in attachments]
            attach_text = f" [Attachments: {', '.join(names)}]"

        reply_text = ""
        ref = msg.get("referenced_message")
        if ref:
            ref_author = sanitize_username(
                ref.get("author", {}).get("username", "Unknown")
            )
            reply_text = f" (replying to {ref_author})"

        prefix = f"[{msg['id']}] " if include_ids else ""
        lines.append(
            f"{prefix}[{timestamp}] {username}{reply_text}: {content}{embed_text}{attach_text}"
        )

    return "\n".join(lines)

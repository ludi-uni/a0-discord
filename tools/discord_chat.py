from helpers.tool import Tool, Response
from usr.plugins.discord.helpers.discord_client import (
    DiscordClient, DiscordAPIError, get_discord_config,
    normalize_allowed_servers, require_allowed_target,
)
from usr.plugins.discord.helpers.discord_bot import (
    start_chat_bridge,
    stop_chat_bridge,
    get_bot_status,
    add_chat_channel,
    remove_chat_channel,
    get_chat_channels,
)
from usr.plugins.discord.helpers.sanitize import require_auth, validate_snowflake


class DiscordChat(Tool):
    """Manage the Discord chat bridge — a persistent bot that lets users
    chat with Agent Zero through Discord channels."""

    async def execute(self, **kwargs) -> Response:
        config = get_discord_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        action = self.args.get("action", "status")

        if action == "start":
            return await self._start()
        elif action == "stop":
            return await self._stop()
        elif action == "add_channel":
            return await self._add_channel()
        elif action == "remove_channel":
            return self._remove_channel()
        elif action == "list":
            return self._list_channels()
        elif action == "status":
            return self._status()
        else:
            return Response(
                message=f"Unknown action '{action}'. Use: start, stop, add_channel, remove_channel, list, status.",
                break_loop=False,
            )

    async def _start(self) -> Response:
        """Start the chat bridge bot."""
        config = get_discord_config(self.agent)
        token = config.get("bot", {}).get("token", "")

        if not token:
            return Response(
                message="Error: Bot token not configured. Set DISCORD_BOT_TOKEN or configure in plugin settings.",
                break_loop=False,
            )

        status = get_bot_status()
        if status.get("running") and status.get("status") == "connected":
            return Response(
                message=f"Chat bridge is already running as {status.get('user', 'unknown')}.",
                break_loop=False,
            )

        self.set_progress("Starting chat bridge bot...")
        try:
            bot = await start_chat_bridge(token)
            status = get_bot_status()
            channels = get_chat_channels()
            msg = f"Chat bridge started as **{status.get('user', 'unknown')}**."
            if channels:
                msg += f"\nListening in {len(channels)} channel(s)."
            else:
                msg += "\nNo chat channels configured yet. Use action 'add_channel' to designate a channel."
            return Response(message=msg, break_loop=False)
        except TimeoutError:
            return Response(
                message="Error: Bot failed to connect within 30 seconds. Check your bot token.",
                break_loop=False,
            )
        except Exception as e:
            return Response(message=f"Error starting chat bridge: {type(e).__name__}", break_loop=False)

    async def _stop(self) -> Response:
        """Stop the chat bridge bot."""
        status = get_bot_status()
        if not status.get("running"):
            return Response(message="Chat bridge is not running.", break_loop=False)

        self.set_progress("Stopping chat bridge bot...")
        try:
            await stop_chat_bridge()
            return Response(message="Chat bridge stopped.", break_loop=False)
        except Exception as e:
            return Response(message=f"Error stopping chat bridge: {type(e).__name__}", break_loop=False)

    async def _add_channel(self) -> Response:
        """Add a channel to the chat bridge."""
        channel_id = self.args.get("channel_id", "")
        guild_id = self.args.get("guild_id", "")
        label = self.args.get("label", "")

        try:
            channel_id = validate_snowflake(channel_id, "channel_id")
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        config = get_discord_config(self.agent)
        allowed_servers = normalize_allowed_servers(config.get("servers", []))
        client = None
        try:
            if allowed_servers:
                client = DiscordClient.from_config(agent=self.agent, mode="bot")
                channel = await require_allowed_target(client, channel_id, allowed_servers)
                guild_id = str(channel.get("guild_id") or "")
        except PermissionError as e:
            return Response(message=f"Error: {e}", break_loop=False)
        except DiscordAPIError as e:
            return Response(message=f"Discord API error: {e}", break_loop=False)
        except Exception as e:
            return Response(message=f"Error adding chat channel: {type(e).__name__}", break_loop=False)
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass

        add_chat_channel(channel_id, guild_id, label)
        msg = f"Channel {channel_id} added to chat bridge"
        if label:
            msg += f" (#{label})"
        msg += ". Messages in this channel will be routed to Agent Zero's LLM."
        return Response(message=msg, break_loop=False)

    def _remove_channel(self) -> Response:
        """Remove a channel from the chat bridge."""
        channel_id = self.args.get("channel_id", "")
        try:
            channel_id = validate_snowflake(channel_id, "channel_id")
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        remove_chat_channel(channel_id)
        return Response(
            message=f"Channel {channel_id} removed from chat bridge.",
            break_loop=False,
        )

    def _list_channels(self) -> Response:
        """List all chat bridge channels."""
        channels = get_chat_channels()
        if not channels:
            return Response(
                message="No chat bridge channels configured. Use action 'add_channel' to add one.",
                break_loop=False,
            )

        lines = [f"Chat bridge channels ({len(channels)}):"]
        for ch_id, info in channels.items():
            label = info.get("label", ch_id)
            guild = info.get("guild_id", "unknown")
            added = info.get("added_at", "unknown")
            lines.append(f"  - #{label} (ID: {ch_id}, server: {guild}, added: {added})")

        status = get_bot_status()
        if status.get("running"):
            lines.append(f"\nBot status: {status.get('status')} as {status.get('user', '?')}")
        else:
            lines.append("\nBot status: not running")

        return Response(message="\n".join(lines), break_loop=False)

    def _status(self) -> Response:
        """Get chat bridge status."""
        status = get_bot_status()
        channels = get_chat_channels()

        if not status.get("running"):
            msg = f"Chat bridge is **not running** (status: {status.get('status', 'stopped')})."
            if channels:
                msg += f"\n{len(channels)} channel(s) configured but bot is offline."
            return Response(message=msg, break_loop=False)

        lines = [
            f"Chat bridge is **{status.get('status')}** as **{status.get('user', '?')}**",
            f"  User ID: {status.get('user_id', '?')}",
            f"  Guilds: {status.get('guilds', 0)}",
            f"  Chat channels: {len(channels)}",
        ]

        for ch_id, info in channels.items():
            label = info.get("label", ch_id)
            lines.append(f"    - #{label} (ID: {ch_id})")

        return Response(message="\n".join(lines), break_loop=False)

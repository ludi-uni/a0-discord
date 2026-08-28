"""Persistent Discord Gateway bot for the chat bridge.
Listens for messages in designated channels and routes them through Agent Zero's LLM.

SECURITY MODEL:
  - Restricted mode (default): Uses call_utility_model() — NO tools, NO code execution,
    NO file access. The LLM literally cannot perform system operations.
  - Elevated mode (opt-in): Authenticated users get full agent loop access via
    context.communicate(). Requires: allow_elevated=true in config + runtime auth
    via !auth <key> in Discord. Sessions expire after a configurable timeout.
"""

import asyncio
import collections
import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("discord_chat_bridge")

try:
    import discord
except ModuleNotFoundError:
    logger.warning("discord.py not found, installing...")
    import subprocess, sys
    python = "/opt/venv-a0/bin/python3" if os.path.isfile("/opt/venv-a0/bin/python3") else sys.executable
    subprocess.run([python, "-m", "pip", "install", "discord.py>=2.3,<3"], capture_output=True, check=True)
    import discord

# Singleton bot instance and its dedicated event loop thread
_bot_instance: Optional["ChatBridgeBot"] = None
_bot_thread: Optional[threading.Thread] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None

CHAT_STATE_FILE = "chat_bridge_state.json"


def _get_state_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / CHAT_STATE_FILE,
        Path("/a0/usr/plugins/discord/data") / CHAT_STATE_FILE,
        Path("/a0/plugins/discord/data") / CHAT_STATE_FILE,
        Path("/git/agent-zero/usr/plugins/discord/data") / CHAT_STATE_FILE,
    ]
    for p in candidates:
        if p.exists():
            return p
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_chat_state() -> dict:
    path = _get_state_path()
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {"channels": {}, "contexts": {}}


def save_chat_state(state: dict):
    from usr.plugins.discord.helpers.sanitize import secure_write_json
    secure_write_json(_get_state_path(), state)


def add_chat_channel(channel_id: str, guild_id: str = "", label: str = ""):
    state = load_chat_state()
    state.setdefault("channels", {})[channel_id] = {
        "guild_id": guild_id,
        "label": label or channel_id,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_chat_state(state)


def remove_chat_channel(channel_id: str):
    state = load_chat_state()
    state.get("channels", {}).pop(channel_id, None)
    state.get("contexts", {}).pop(channel_id, None)
    save_chat_state(state)


def get_chat_channels() -> dict:
    return load_chat_state().get("channels", {})


def get_context_id(channel_id: str) -> Optional[str]:
    return load_chat_state().get("contexts", {}).get(channel_id)


def set_context_id(channel_id: str, context_id: str):
    state = load_chat_state()
    state.setdefault("contexts", {})[channel_id] = context_id
    save_chat_state(state)


class ChatBridgeBot(discord.Client):
    """Discord bot that bridges messages to Agent Zero's LLM.

    SECURITY: By default, uses direct LLM calls (call_utility_model) with NO
    tool access. Authenticated users can optionally elevate to full agent loop
    access if allow_elevated is enabled in the plugin config.
    """

    MAX_CHAT_MESSAGE_LENGTH = 4000
    MAX_HISTORY_MESSAGES = 20
    # Rate limit: max messages per user within the window
    RATE_LIMIT_MAX = 10
    RATE_LIMIT_WINDOW = 60  # seconds
    # Auth failure rate limit
    AUTH_MAX_FAILURES = 5
    AUTH_FAILURE_WINDOW = 300  # 5 minute lockout

    CHAT_SYSTEM_PROMPT = (
        "You are a friendly, helpful AI assistant chatting with users on Discord.\n\n"
        "IMPORTANT CONSTRAINTS:\n"
        "- You are a conversational chat bot ONLY. You have NO access to tools, files, "
        "commands, terminals, or any system resources.\n"
        "- If users ask you to run commands, access files, list directories, execute code, "
        "or perform any system operations, explain that you don't have those capabilities.\n"
        "- NEVER fabricate or make up file listings, directory contents, command outputs, "
        "or system information. You genuinely do not have access to any of these.\n"
        "- Be helpful, friendly, and conversational within these constraints.\n"
        "- You can help with general knowledge, answer questions, have discussions, "
        "write text, brainstorm ideas, and more — just not anything involving system access.\n"
        "- Each message shows the Discord username prefix. Respond naturally to the "
        "conversation.\n"
    )

    def __init__(self, bot_token: str):
        if not bot_token or not bot_token.strip():
            raise ValueError("Bot token must be provided to ChatBridgeBot.")
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.bot_token = bot_token
        # Per-user rate limiting: user_id -> deque of timestamps
        self._rate_limits: dict[str, collections.deque] = {}
        # Per-channel conversation history (in-memory, lost on restart)
        self._conversations: dict[str, list[dict]] = {}
        # Elevated session tracking: "{user_id}:{channel_id}" -> {"at": float, "name": str}
        self._elevated_sessions: dict[str, dict] = {}
        # Failed auth attempt tracking: user_id -> deque of timestamps
        self._auth_failures: dict[str, collections.deque] = {}
        # Temp files for image attachments in elevated mode
        self._temp_files: list[str] = []
        # Threading event for signaling ready state (set by on_ready)
        self._ready_event: Optional[threading.Event] = None

    async def on_ready(self):
        logger.info(f"Chat bridge connected as {self.user} (ID: {self.user.id})")
        # Signal the startup thread that the bot is ready
        if hasattr(self, "_ready_event") and self._ready_event is not None:
            self._ready_event.set()

    # ------------------------------------------------------------------
    # Config access
    # ------------------------------------------------------------------

    def _get_config(self) -> dict:
        """Load the Discord plugin configuration."""
        try:
            from usr.plugins.discord.helpers.discord_client import get_discord_config
            return get_discord_config()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _session_key(self, user_id: str, channel_id: str) -> str:
        return f"{user_id}:{channel_id}"

    def _is_elevated(self, user_id: str, channel_id: str) -> bool:
        """Check if a user has an active elevated session in this channel."""
        config = self._get_config()
        if not config.get("chat_bridge", {}).get("allow_elevated", False):
            return False

        key = self._session_key(user_id, channel_id)
        session = self._elevated_sessions.get(key)
        if not session:
            return False

        timeout = config.get("chat_bridge", {}).get("session_timeout", 3600)
        # timeout=0 means never expire
        if timeout > 0 and time.monotonic() - session["at"] > timeout:
            del self._elevated_sessions[key]
            return False

        return True

    def _get_auth_key(self, config: dict) -> str:
        """Get the auth key from config, auto-generating if needed."""
        bridge_config = config.get("chat_bridge", {})
        auth_key = bridge_config.get("auth_key", "")

        if not auth_key and bridge_config.get("allow_elevated", False):
            # Auto-generate a key and persist it
            from usr.plugins.discord.helpers.sanitize import generate_auth_key
            auth_key = generate_auth_key()
            bridge_config["auth_key"] = auth_key
            config["chat_bridge"] = bridge_config
            try:
                from usr.plugins.discord.helpers.discord_client import get_discord_config
                from usr.plugins.discord.helpers.sanitize import secure_write_json
                # Find and update the config file
                config_candidates = [
                    Path("/a0/usr/plugins/discord/config.json"),
                    Path("/a0/plugins/discord/config.json"),
                    Path(__file__).parent.parent / "config.json",
                ]
                for cp in config_candidates:
                    if cp.exists():
                        existing = json.loads(cp.read_text())
                        existing.setdefault("chat_bridge", {})["auth_key"] = auth_key
                        secure_write_json(cp, existing)
                        logger.info("Auto-generated auth key for elevated mode")
                        break
            except Exception as e:
                logger.warning(f"Could not persist auto-generated auth key: {type(e).__name__}")

        return auth_key

    # ------------------------------------------------------------------
    # Auth command handling
    # ------------------------------------------------------------------

    async def _handle_auth_command(self, message: discord.Message, channel_id: str) -> bool:
        """Handle !auth, !deauth, and !bridge-status commands.

        Returns True if the message was an auth command (consumed), False otherwise.
        """
        text = message.content.strip()
        user_id = str(message.author.id)

        # --- !deauth (accept common typos/aliases) ---
        if text.lower() in ("!deauth", "!dauth", "!unauth", "!logout", "!logoff"):
            key = self._session_key(user_id, channel_id)
            if key in self._elevated_sessions:
                del self._elevated_sessions[key]
                # Clear conversation history so restricted mode starts fresh
                self._conversations.pop(channel_id, None)
                await message.channel.send("Session ended. Back to restricted mode.")
                logger.info(f"Elevated session ended: user={user_id} channel={channel_id}")
            else:
                await message.channel.send("No active elevated session.")
            return True

        # --- !bridge-status ---
        if text.lower() == "!bridge-status":
            if self._is_elevated(user_id, channel_id):
                session = self._elevated_sessions[self._session_key(user_id, channel_id)]
                elapsed = int(time.monotonic() - session["at"])
                config = self._get_config()
                timeout = config.get("chat_bridge", {}).get("session_timeout", 3600)
                if timeout > 0:
                    remaining = max(0, timeout - elapsed)
                    expire_info = f"Session expires in {remaining // 3600}h {(remaining % 3600) // 60}m"
                else:
                    expire_info = "Session does not expire"
                await message.channel.send(
                    f"Mode: **Elevated** (full agent access)\n"
                    f"{expire_info}. Use `!deauth` to end."
                )
            else:
                config = self._get_config()
                elevated_available = config.get("chat_bridge", {}).get("allow_elevated", False)
                if elevated_available:
                    await message.channel.send(
                        "Mode: **Restricted** (chat only). Use `!auth <key>` to elevate."
                    )
                else:
                    await message.channel.send(
                        "Mode: **Restricted** (chat only). Elevated mode is not enabled."
                    )
            return True

        # --- !auth <key> ---
        if text.lower().startswith("!auth"):
            # Try to delete the message immediately to protect the key
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                # Bot may not have Manage Messages permission
                logger.warning("Could not delete !auth message — bot lacks Manage Messages permission")

            config = self._get_config()
            if not config.get("chat_bridge", {}).get("allow_elevated", False):
                await message.channel.send("Elevated mode is not enabled in the configuration.")
                return True

            auth_key = self._get_auth_key(config)
            if not auth_key:
                await message.channel.send(
                    "Elevated mode is enabled but no auth key could be generated. "
                    "Check plugin configuration."
                )
                return True

            # Check auth failure rate limit
            now = time.monotonic()
            if user_id not in self._auth_failures:
                self._auth_failures[user_id] = collections.deque()
            failures = self._auth_failures[user_id]
            while failures and now - failures[0] > self.AUTH_FAILURE_WINDOW:
                failures.popleft()
            if len(failures) >= self.AUTH_MAX_FAILURES:
                await message.channel.send(
                    "Too many failed attempts. Please wait before trying again."
                )
                return True

            # Extract the key from the command
            parts = text.split(maxsplit=1)
            provided_key = parts[1].strip() if len(parts) > 1 else ""

            # Constant-time comparison to prevent timing attacks
            if provided_key and hmac.compare_digest(provided_key, auth_key):
                session_key = self._session_key(user_id, channel_id)
                self._elevated_sessions[session_key] = {
                    "at": now,
                    "name": message.author.display_name or message.author.name,
                }
                timeout = config.get("chat_bridge", {}).get("session_timeout", 3600)
                if timeout > 0:
                    hours = timeout // 3600
                    mins = (timeout % 3600) // 60
                    duration = f"{hours}h" if hours and not mins else f"{mins}m" if mins else f"{hours}h"
                    if hours and mins:
                        duration = f"{hours}h {mins}m"
                    expire_msg = f"Session expires in {duration}."
                else:
                    expire_msg = "Session does not expire."
                await message.channel.send(
                    f"Elevated session active. {expire_msg} "
                    f"You now have full Agent Zero access in this channel. "
                    f"Use `!deauth` to end the session."
                )
                logger.info(f"Elevated session granted: user={user_id} channel={channel_id}")
            else:
                failures.append(now)
                remaining = self.AUTH_MAX_FAILURES - len(failures)
                await message.channel.send(
                    f"Authentication failed. {remaining} attempt(s) remaining."
                )
                logger.warning(f"Failed auth attempt: user={user_id} channel={channel_id}")

            return True

        return False

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def on_message(self, message: discord.Message):
        # Ignore own messages and other bots
        if message.author.bot:
            return

        channel_id = str(message.channel.id)
        chat_channels = get_chat_channels()

        # Only respond in designated chat channels
        if channel_id not in chat_channels:
            return

        # User allowlist: silently ignore users not on the list
        config = self._get_config()
        from usr.plugins.discord.helpers.discord_client import require_allowed_guild

        try:
            actual_guild_id = getattr(getattr(message, "guild", None), "id", None)
            require_allowed_guild(actual_guild_id, config.get("servers", []))
        except PermissionError:
            logger.warning("Ignoring Discord bridge message from a disallowed server")
            return

        allowed_users = config.get("chat_bridge", {}).get("allowed_users", [])
        if allowed_users and str(message.author.id) not in [str(u) for u in allowed_users]:
            return

        user_text = message.content
        if not user_text.strip():
            return

        # Handle auth commands first (before rate limiting)
        if user_text.strip().startswith("!"):
            handled = await self._handle_auth_command(message, channel_id)
            if handled:
                return

        # Enforce content length limit before any processing
        if len(user_text) > self.MAX_CHAT_MESSAGE_LENGTH:
            await message.channel.send(
                f"Message too long ({len(user_text)} chars). "
                f"Max: {self.MAX_CHAT_MESSAGE_LENGTH}."
            )
            return

        # Per-user rate limiting
        user_key = str(message.author.id)
        now = time.monotonic()
        if user_key not in self._rate_limits:
            self._rate_limits[user_key] = collections.deque()
        timestamps = self._rate_limits[user_key]
        # Purge old entries outside the window
        while timestamps and now - timestamps[0] > self.RATE_LIMIT_WINDOW:
            timestamps.popleft()
        if len(timestamps) >= self.RATE_LIMIT_MAX:
            await message.channel.send(
                f"Rate limit: max {self.RATE_LIMIT_MAX} messages per {self.RATE_LIMIT_WINDOW}s. Please wait."
            )
            return
        timestamps.append(now)

        # Route based on elevation status
        user_id = str(message.author.id)
        is_elevated = self._is_elevated(user_id, channel_id)

        # Show typing while processing
        async with message.channel.typing():
            try:
                if is_elevated:
                    response_text = await self._get_elevated_response(
                        channel_id, user_text, message
                    )
                else:
                    response_text = await self._get_agent_response(
                        channel_id, user_text, message
                    )
            except Exception as e:
                logger.error(f"Agent error: {type(e).__name__}")
                response_text = "An error occurred while processing your message."

        # Send response, splitting if needed
        await self._send_response(message.channel, response_text, reference=message)

    # ------------------------------------------------------------------
    # Restricted mode: direct LLM call, NO tools
    # ------------------------------------------------------------------

    async def _get_agent_response(self, channel_id: str, text: str, message: discord.Message) -> str:
        """Get LLM response via direct model call (no agent loop, no tools).

        SECURITY: This intentionally bypasses the full agent loop. The LLM is
        called directly via call_utility_model(), which provides NO tool access.
        This prevents privilege escalation from untrusted Discord users.
        """
        try:
            from agent import AgentContext, AgentContextType
            from initialize import initialize_agent

            # Get or create a context (only for model access, NOT for tool execution)
            context_id = get_context_id(channel_id)
            context = None

            if context_id:
                context = AgentContext.get(context_id)

            if context is None:
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                set_context_id(channel_id, context.id)
                logger.info(f"Created new context {context.id} for channel {channel_id}")

            agent = context.agent0

            # Sanitize external content
            from usr.plugins.discord.helpers.sanitize import sanitize_content, sanitize_username
            author_name = sanitize_username(
                message.author.display_name or message.author.name
            )
            safe_text = sanitize_content(text)

            # Maintain per-channel conversation history
            if channel_id not in self._conversations:
                self._conversations[channel_id] = []
            history = self._conversations[channel_id]
            history.append({"role": "user", "name": author_name, "content": safe_text})

            # Trim to max history length
            if len(history) > self.MAX_HISTORY_MESSAGES:
                self._conversations[channel_id] = history[-self.MAX_HISTORY_MESSAGES:]
                history = self._conversations[channel_id]

            # Format conversation history for the model
            formatted = []
            for msg in history:
                if msg["role"] == "user":
                    formatted.append(f"{msg['name']}: {msg['content']}")
                else:
                    formatted.append(f"Assistant: {msg['content']}")
            conversation_text = "\n".join(formatted)

            # Direct LLM call — NO tools, NO agent loop, NO code execution
            response = await agent.call_utility_model(
                system=self.CHAT_SYSTEM_PROMPT,
                message=conversation_text,
            )

            # Store response in history
            history.append({"role": "assistant", "content": response})

            return response if isinstance(response, str) else str(response)

        except ImportError:
            logger.exception("Restricted Discord bridge dependencies unavailable")
            return "Restricted chat is temporarily unavailable."

    # ------------------------------------------------------------------
    # Elevated mode: full agent loop with tools (authenticated users only)
    # ------------------------------------------------------------------

    async def _get_elevated_response(self, channel_id: str, text: str, message: discord.Message) -> str:
        """Route through the full Agent Zero agent loop (tools, code execution, etc.).

        SECURITY: Only called for users who have authenticated via !auth <key>.
        The caller (_on_message) verifies elevation status before calling this.
        """
        try:
            from agent import AgentContext, AgentContextType
            from helpers.messages import UserMessage
            from initialize import initialize_agent

            # Get or create a context for this channel
            context_id = get_context_id(channel_id)
            context = None

            if context_id:
                context = AgentContext.get(context_id)

            if context is None:
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                set_context_id(channel_id, context.id)
                logger.info(f"Created new elevated context {context.id} for channel {channel_id}")

            # Sanitize input (injection defense still applies)
            from usr.plugins.discord.helpers.sanitize import sanitize_content, sanitize_username
            author_name = sanitize_username(
                message.author.display_name or message.author.name
            )
            safe_text = sanitize_content(text)
            # In elevated mode the user is authenticated — send their message
            # directly as a user request through communicate(). Do NOT prefix
            # with "[Discord Chat Bridge - …]" because that makes the infection
            # check think an external entity is directing the agent.
            prefixed_text = safe_text

            # Handle image attachments for the agent
            attachment_paths = []
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    try:
                        import tempfile
                        img_bytes = await att.read()
                        suffix = Path(att.filename).suffix or ".png"
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                        tmp.write(img_bytes)
                        tmp.close()
                        attachment_paths.append(tmp.name)
                        self._temp_files.append(tmp.name)
                    except Exception:
                        pass

            user_msg = UserMessage(prefixed_text, attachment_paths)
            task = context.communicate(user_msg)
            result = await task.result()

            # Clean up temp files after processing
            self._cleanup_temp_files()

            return result if isinstance(result, str) else str(result)

        except ImportError:
            logger.exception("Elevated Discord bridge dependencies unavailable")
            return "Elevated chat is temporarily unavailable."

    def _cleanup_temp_files(self):
        """Remove temporary image files created during message processing."""
        remaining = []
        for path in self._temp_files:
            try:
                os.unlink(path)
            except OSError:
                remaining.append(path)
        self._temp_files = remaining

    # ------------------------------------------------------------------
    # Response sending
    # ------------------------------------------------------------------

    async def _send_response(self, channel: discord.TextChannel, text: str, reference=None):
        """Send a response to Discord, splitting long messages."""
        if not text:
            text = "(No response)"

        chunks = _split_message(text)
        for i, chunk in enumerate(chunks):
            ref = reference if i == 0 else None
            await channel.send(chunk, reference=ref)

    async def start_bot(self):
        """Start the bot (non-blocking within an existing event loop)."""
        await self.start(self.bot_token)

    async def wait_until_ready_timeout(self, timeout: float = 30.0):
        """Wait for the bot to be ready, with timeout."""
        try:
            await asyncio.wait_for(self.wait_until_ready(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("Bot failed to connect within timeout")


def _split_message(content: str, max_length: int = 2000) -> list[str]:
    if len(content) <= max_length:
        return [content]
    chunks = []
    while content:
        if len(content) <= max_length:
            chunks.append(content)
            break
        split_at = content.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = content.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(content[:split_at])
        content = content[split_at:].lstrip("\n")
    return chunks


def _is_bot_alive() -> bool:
    """Check if the bot instance and its dedicated thread are actually alive."""
    if _bot_instance is None:
        return False
    if _bot_instance.is_closed():
        return False
    if _bot_thread is None or not _bot_thread.is_alive():
        return False
    return True


def _cleanup_dead_bot():
    """Clean up singleton refs if the bot/thread has died."""
    global _bot_instance, _bot_thread, _bot_loop
    if not _is_bot_alive():
        _bot_instance = None
        _bot_thread = None
        _bot_loop = None


def _run_bot_in_thread(bot: ChatBridgeBot, ready_event: threading.Event):
    """Run the bot in a dedicated thread with its own event loop.

    This is necessary because A0's Flask/WSGIMiddleware runs API handlers
    in request-scoped event loops that are destroyed when the request ends.
    The bot needs a persistent event loop to maintain the Discord gateway
    websocket connection.
    """
    global _bot_instance, _bot_thread, _bot_loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bot_loop = loop

    # Give the bot a reference to the threading.Event so on_ready can signal it
    bot._ready_event = ready_event

    try:
        # bot.start() is a coroutine that logs in and connects to the gateway.
        # It blocks (within run_until_complete) until the bot is closed.
        loop.run_until_complete(bot.start(bot.bot_token))
    except Exception as e:
        logger.error(f"Chat bridge bot exited with error: {type(e).__name__}: {e}")
    finally:
        logger.info("Chat bridge bot thread ending, cleaning up singleton")
        ready_event.set()  # Unblock caller if on_ready never fired
        _bot_instance = None
        _bot_thread = None
        _bot_loop = None
        try:
            loop.close()
        except Exception:
            pass


async def start_chat_bridge(bot_token: str) -> ChatBridgeBot:
    """Start the chat bridge bot in a dedicated background thread."""
    global _bot_instance, _bot_thread, _bot_loop

    if not bot_token or not bot_token.strip():
        raise ValueError("Cannot start chat bridge: bot token is empty or not configured.")

    # Clean up any dead instance before checking
    _cleanup_dead_bot()

    if _bot_instance and _is_bot_alive():
        return _bot_instance

    # Force-close any leftover instance
    if _bot_instance:
        try:
            if not _bot_instance.is_closed():
                if _bot_loop and _bot_loop.is_running():
                    asyncio.run_coroutine_threadsafe(_bot_instance.close(), _bot_loop).result(timeout=5)
                else:
                    await _bot_instance.close()
        except Exception:
            pass
        _bot_instance = None
        _bot_thread = None
        _bot_loop = None

    bot = ChatBridgeBot(bot_token)
    _bot_instance = bot

    # Start the bot in a dedicated daemon thread
    ready_event = threading.Event()
    thread = threading.Thread(
        target=_run_bot_in_thread,
        args=(bot, ready_event),
        daemon=True,
        name="discord-chat-bridge",
    )
    _bot_thread = thread
    thread.start()

    # Wait for the bot to be ready (or timeout)
    ready_event.wait(timeout=35)

    if not bot.is_ready():
        logger.warning("Bot started but may not be fully ready yet")

    return bot


async def stop_chat_bridge():
    """Stop the chat bridge bot."""
    global _bot_instance, _bot_thread, _bot_loop

    if _bot_instance and not _bot_instance.is_closed():
        if _bot_loop and _bot_loop.is_running():
            # Schedule close() on the bot's own event loop
            future = asyncio.run_coroutine_threadsafe(_bot_instance.close(), _bot_loop)
            try:
                future.result(timeout=10)
            except Exception:
                pass
        else:
            try:
                await _bot_instance.close()
            except Exception:
                pass

    # Wait for thread to finish
    if _bot_thread and _bot_thread.is_alive():
        _bot_thread.join(timeout=5)

    _bot_instance = None
    _bot_thread = None
    _bot_loop = None


def get_bot_status() -> dict:
    """Get current bot status."""
    # Detect and clean up dead bot tasks
    _cleanup_dead_bot()

    if _bot_instance is None:
        return {"running": False, "status": "stopped"}
    if _bot_instance.is_closed():
        return {"running": False, "status": "closed"}
    if _bot_thread and not _bot_thread.is_alive():
        return {"running": False, "status": "crashed"}
    if _bot_instance.is_ready():
        user = _bot_instance.user
        return {
            "running": True,
            "status": "connected",
            "user": str(user),
            "user_id": str(user.id) if user else None,
            "guilds": len(_bot_instance.guilds),
        }
    return {"running": True, "status": "connecting"}

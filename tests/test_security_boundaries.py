import builtins
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from usr.plugins.discord.helpers.discord_bot import ChatBridgeBot
import usr.plugins.discord.helpers.discord_bot as discord_bot_module
from usr.plugins.discord.tools.discord_chat import DiscordChat
import usr.plugins.discord.tools.discord_chat as discord_chat_module
from usr.plugins.discord.tools.discord_insights import DiscordInsights
import usr.plugins.discord.tools.discord_insights as discord_insights_module
from usr.plugins.discord.tools.discord_members import DiscordMembers
import usr.plugins.discord.tools.discord_members as discord_members_module
from usr.plugins.discord.tools.discord_poll import DiscordPoll
import usr.plugins.discord.tools.discord_poll as discord_poll_module
from usr.plugins.discord.tools.discord_read import DiscordRead
import usr.plugins.discord.tools.discord_read as discord_read_module
from usr.plugins.discord.tools.discord_send import DiscordSend
import usr.plugins.discord.tools.discord_send as discord_send_module
from usr.plugins.discord.tools.discord_summarize import DiscordSummarize
import usr.plugins.discord.tools.discord_summarize as discord_summarize_module


class _UserMessage:
    def __init__(self, *, message, attachments=None):
        self.message = message
        self.attachments = attachments or []


class _Task:
    async def result(self):
        return "elevated response"


class _Context:
    id = "context-id"

    def __init__(self):
        self.messages = []

    def communicate(self, message):
        self.messages.append(message)
        return _Task()


def _discord_message():
    author = SimpleNamespace(display_name="Alice", name="alice")
    return SimpleNamespace(author=author, attachments=[])


def _tool(tool_class, args, agent=None):
    instance = object.__new__(tool_class)
    instance.args = args
    instance.agent = agent
    return instance


class _FakeDiscordClient:
    def __init__(self, channel=None, messages=None, threads=None):
        self.channel = channel or {}
        self.messages = messages or []
        self.threads = threads or []
        self.get_channel_calls = []
        self.message_calls = []
        self.guild_channel_calls = []
        self.thread_calls = []
        self.closed = False
        self.channel_error = None
        self.sent_messages = []
        self.reactions = []

    async def get_channel(self, target_id):
        self.get_channel_calls.append(target_id)
        if self.channel_error:
            raise self.channel_error
        return self.channel

    async def get_all_channel_messages(self, **kwargs):
        self.message_calls.append(kwargs)
        return self.messages

    async def get_guild_channels(self, guild_id):
        self.guild_channel_calls.append(guild_id)
        return []

    async def get_active_threads(self, guild_id):
        self.thread_calls.append(guild_id)
        return {"threads": self.threads}

    async def get_channel_messages(self, channel_id, limit=50):
        self.message_calls.append({"channel_id": channel_id, "limit": limit})
        return self.messages

    async def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)
        return {"id": "55555555555555555"}

    async def add_reaction(self, channel_id, message_id, emoji):
        self.reactions.append((channel_id, message_id, emoji))

    async def close(self):
        self.closed = True


def _read_patches(client, servers):
    config = {"bot": {"token": "test-token"}, "servers": servers}
    return (
        patch.object(discord_read_module, "get_discord_config", return_value=config),
        patch.object(discord_read_module, "get_modes_to_try", return_value=["bot"]),
        patch.object(
            discord_read_module.DiscordClient,
            "from_config",
            return_value=client,
        ),
    )


class ChatBridgeFailClosedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = object.__new__(ChatBridgeBot)
        self.bot._conversations = {}
        self.bot._temp_files = []

    async def test_restricted_import_error_does_not_call_http_agent(self):
        real_import = builtins.__import__

        def fail_agent_import(name, *args, **kwargs):
            if name == "agent":
                raise ImportError("agent module unavailable")
            return real_import(name, *args, **kwargs)

        aiohttp_module = types.ModuleType("aiohttp")
        aiohttp_module.ClientSession = Mock(
            side_effect=AssertionError("HTTP agent API must not be called")
        )
        log_exception = Mock()
        with (
            patch("builtins.__import__", side_effect=fail_agent_import),
            patch.dict(sys.modules, {"aiohttp": aiohttp_module}),
            patch.object(discord_bot_module.logger, "exception", log_exception),
        ):
            response = await self.bot._get_agent_response(
                "12345678901234567",
                "raw Discord input",
                _discord_message(),
            )

        self.assertEqual(response, "Restricted chat is temporarily unavailable.")
        aiohttp_module.ClientSession.assert_not_called()
        log_exception.assert_called_once()

    async def test_elevated_uses_agent_user_message_and_communicates(self):
        context = _Context()

        class AgentContext:
            @staticmethod
            def get(_context_id):
                return context

        agent_module = types.ModuleType("agent")
        agent_module.AgentContext = AgentContext
        agent_module.AgentContextType = SimpleNamespace(USER="user")
        agent_module.UserMessage = _UserMessage

        initialize_module = types.ModuleType("initialize")
        initialize_module.initialize_agent = lambda: object()

        aiohttp_module = types.ModuleType("aiohttp")
        aiohttp_module.ClientSession = Mock(
            side_effect=AssertionError("HTTP agent API must not be called")
        )
        with (
            patch.dict(
                sys.modules,
                {
                    "agent": agent_module,
                    "initialize": initialize_module,
                    "aiohttp": aiohttp_module,
                },
            ),
            patch(
                "usr.plugins.discord.helpers.discord_bot.get_context_id",
                return_value="context-id",
            ),
        ):
            response = await self.bot._get_elevated_response(
                "12345678901234567",
                "run the authenticated request",
                _discord_message(),
            )

        self.assertEqual(response, "elevated response")
        self.assertEqual(len(context.messages), 1)
        self.assertIsInstance(context.messages[0], _UserMessage)
        self.assertEqual(context.messages[0].message, "run the authenticated request")
        aiohttp_module.ClientSession.assert_not_called()


class DiscordReadAllowlistTests(unittest.IsolatedAsyncioTestCase):
    allowed = "11111111111111111"
    disallowed = "22222222222222222"
    target = "33333333333333333"

    async def _execute(self, args, client, servers):
        tool = _tool(DiscordRead, args)
        config_patch, modes_patch, client_patch = _read_patches(client, servers)
        with config_patch, modes_patch, client_patch:
            return await tool.execute()

    async def test_disallowed_channel_is_denied_before_message_fetch(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": self.disallowed})
        response = await self._execute(
            {"action": "messages", "channel_id": self.target},
            client,
            [self.allowed],
        )

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.get_channel_calls, [self.target])
        self.assertEqual(client.message_calls, [])
        self.assertTrue(client.closed)

    async def test_allowed_channel_is_read_after_ownership_check(self):
        message = {"id": "44444444444444444", "author": {"username": "alice"}, "content": "hello"}
        client = _FakeDiscordClient(
            channel={"id": self.target, "guild_id": self.allowed},
            messages=[message],
        )
        response = await self._execute(
            {"action": "messages", "channel_id": self.target},
            client,
            [self.allowed],
        )

        self.assertIn("Retrieved 1 messages", response.message)
        self.assertEqual(client.get_channel_calls, [self.target])
        self.assertEqual(len(client.message_calls), 1)
        self.assertTrue(client.closed)

    async def test_disallowed_thread_is_denied_using_thread_ownership(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": self.disallowed})
        response = await self._execute(
            {"action": "messages", "thread_id": self.target},
            client,
            [self.allowed],
        )

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.get_channel_calls, [self.target])
        self.assertEqual(client.message_calls, [])

    async def test_guildless_resource_is_denied_when_allowlist_is_enabled(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": None})
        response = await self._execute(
            {"action": "messages", "channel_id": self.target},
            client,
            [self.allowed],
        )

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.message_calls, [])
        self.assertTrue(client.closed)

    async def test_empty_allowlist_preserves_unfiltered_message_reads(self):
        message = {"id": "44444444444444444", "author": {"username": "alice"}, "content": "hello"}
        client = _FakeDiscordClient(messages=[message])
        response = await self._execute(
            {"action": "messages", "channel_id": self.target},
            client,
            [],
        )

        self.assertIn("Retrieved 1 messages", response.message)
        self.assertEqual(len(client.message_calls), 1)
        self.assertTrue(client.closed)

    async def test_disallowed_threads_guild_is_denied_before_api_request(self):
        client = _FakeDiscordClient()
        response = await self._execute(
            {"action": "threads", "guild_id": self.disallowed},
            client,
            [self.allowed],
        )

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.thread_calls, [])
        self.assertTrue(client.closed)

    async def test_allowed_threads_guild_uses_existing_api_path(self):
        client = _FakeDiscordClient(threads=[])
        response = await self._execute(
            {"action": "threads", "guild_id": self.allowed},
            client,
            [self.allowed],
        )

        self.assertIn("No active threads found", response.message)
        self.assertEqual(client.thread_calls, [self.allowed])
        self.assertTrue(client.closed)

    async def test_numeric_allowlist_id_matches_string_guild_id(self):
        client = _FakeDiscordClient()
        response = await self._execute(
            {"action": "channels", "guild_id": self.allowed},
            client,
            [int(self.allowed)],
        )

        self.assertIn("No channels found", response.message)
        self.assertEqual(client.guild_channel_calls, [self.allowed])
        self.assertTrue(client.closed)

    async def test_client_is_closed_when_ownership_lookup_raises(self):
        client = _FakeDiscordClient()
        client.channel_error = RuntimeError("lookup failed")
        response = await self._execute(
            {"action": "messages", "channel_id": self.target},
            client,
            [self.allowed],
        )

        self.assertIn("Error reading Discord", response.message)
        self.assertTrue(client.closed)


class AdjacentAllowlistTests(unittest.IsolatedAsyncioTestCase):
    allowed = "11111111111111111"
    disallowed = "22222222222222222"
    target = "33333333333333333"

    def _config(self):
        return {"bot": {"token": "test-token"}, "servers": [self.allowed]}

    async def test_send_denies_disallowed_channel_before_write(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": self.disallowed})
        tool = _tool(
            DiscordSend,
            {"action": "send", "channel_id": self.target, "content": "hello"},
        )
        with (
            patch.object(discord_send_module, "get_discord_config", return_value=self._config()),
            patch.object(discord_send_module.DiscordClient, "from_config", return_value=client),
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.get_channel_calls, [self.target])
        self.assertEqual(client.sent_messages, [])
        self.assertTrue(client.closed)

    async def test_summarize_denies_actual_disallowed_channel_before_read(self):
        client = _FakeDiscordClient(
            channel={"id": self.target, "guild_id": self.disallowed, "name": "private"},
            messages=[{"id": "44444444444444444", "author": {}, "content": "secret"}],
        )
        agent = SimpleNamespace(call_utility_model=AsyncMock(return_value="summary"))
        tool = _tool(
            DiscordSummarize,
            {
                "channel_id": self.target,
                "guild_id": self.allowed,
                "save_to_memory": "false",
            },
            agent,
        )
        tool.set_progress = Mock()
        with (
            patch.object(discord_summarize_module, "get_discord_config", return_value=self._config()),
            patch.object(discord_summarize_module, "get_modes_to_try", return_value=["bot"]),
            patch.object(discord_summarize_module.DiscordClient, "from_config", return_value=client),
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.message_calls, [])
        agent.call_utility_model.assert_not_awaited()
        self.assertTrue(client.closed)

    async def test_insights_denies_actual_disallowed_thread_before_read(self):
        client = _FakeDiscordClient(
            channel={"id": self.target, "guild_id": self.disallowed, "name": "private-thread"},
            messages=[{"id": "44444444444444444", "author": {}, "content": "secret"}],
        )
        agent = SimpleNamespace(call_utility_model=AsyncMock(return_value="insights"))
        tool = _tool(
            DiscordInsights,
            {
                "thread_id": self.target,
                "guild_id": self.allowed,
                "save_to_memory": "false",
            },
            agent,
        )
        tool.set_progress = Mock()
        with (
            patch.object(discord_insights_module, "get_discord_config", return_value=self._config()),
            patch.object(discord_insights_module, "get_modes_to_try", return_value=["bot"]),
            patch.object(discord_insights_module.DiscordClient, "from_config", return_value=client),
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.message_calls, [])
        agent.call_utility_model.assert_not_awaited()
        self.assertTrue(client.closed)

    async def test_members_denies_disallowed_guild_before_dispatch(self):
        tool = _tool(DiscordMembers, {"action": "list", "guild_id": self.disallowed})
        list_members = AsyncMock(return_value=SimpleNamespace(message="dispatched"))
        tool._list_members = list_members
        with patch.object(
            discord_members_module, "get_discord_config", return_value=self._config()
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        list_members.assert_not_awaited()

    async def test_poll_denies_saved_disallowed_channel_before_read(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": self.disallowed})
        tool = _tool(DiscordPoll, {"action": "check"})
        tool.set_progress = Mock()
        watches = {self.target: {"guild_id": self.allowed, "label": "watched"}}
        with (
            patch.object(discord_poll_module, "get_discord_config", return_value=self._config()),
            patch.object(discord_poll_module, "get_watch_channels", return_value=watches),
            patch.object(discord_poll_module, "get_last_message_id", return_value="44444444444444444"),
            patch.object(discord_poll_module, "get_modes_to_try", return_value=["bot"]),
            patch.object(discord_poll_module.DiscordClient, "from_config", return_value=client),
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        self.assertEqual(client.message_calls, [])
        self.assertTrue(client.closed)

    async def test_poll_watch_denies_disallowed_channel_before_state_write(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": self.disallowed})
        tool = _tool(
            DiscordPoll,
            {"action": "watch", "channel_id": self.target, "guild_id": self.allowed},
        )
        state_write = Mock()
        with (
            patch.object(discord_poll_module, "get_discord_config", return_value=self._config()),
            patch.object(discord_poll_module.DiscordClient, "from_config", return_value=client),
            patch.object(discord_poll_module, "add_watch_channel", state_write),
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        state_write.assert_not_called()
        self.assertTrue(client.closed)

    async def test_chat_add_channel_denies_disallowed_channel_before_state_write(self):
        client = _FakeDiscordClient(channel={"id": self.target, "guild_id": self.disallowed})
        tool = _tool(
            DiscordChat,
            {"action": "add_channel", "channel_id": self.target, "guild_id": self.allowed},
        )
        state_write = Mock()
        client_factory = SimpleNamespace(from_config=Mock(return_value=client))
        with (
            patch.object(discord_chat_module, "get_discord_config", return_value=self._config()),
            patch.object(discord_chat_module, "DiscordClient", client_factory, create=True),
            patch.object(discord_chat_module, "add_chat_channel", state_write),
        ):
            response = await tool.execute()

        self.assertIn("not in the allowed servers list", response.message)
        state_write.assert_not_called()
        self.assertTrue(client.closed)

    async def test_bridge_ignores_registered_channel_from_disallowed_actual_guild(self):
        bot = object.__new__(ChatBridgeBot)
        bot._rate_limits = {}
        bot._get_config = Mock(return_value=self._config())
        bot._is_elevated = Mock(return_value=False)
        bot._get_agent_response = AsyncMock(return_value="response")
        bot._send_response = AsyncMock()

        class TypingContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        channel = SimpleNamespace(id=self.target, typing=lambda: TypingContext())
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id="66666666666666666"),
            channel=channel,
            guild=SimpleNamespace(id=self.disallowed),
            content="hello",
        )
        with patch.object(
            discord_bot_module,
            "get_chat_channels",
            return_value={self.target: {"guild_id": self.allowed}},
        ):
            await bot.on_message(message)

        bot._get_agent_response.assert_not_awaited()
        bot._send_response.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()

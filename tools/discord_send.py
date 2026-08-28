from helpers.tool import Tool, Response
from usr.plugins.discord.helpers.discord_client import (
    DiscordClient, DiscordAPIError, get_discord_config,
    normalize_allowed_servers, require_allowed_target,
)
from usr.plugins.discord.helpers.sanitize import require_auth, validate_snowflake


class DiscordSend(Tool):
    """Send a message or reaction to a Discord channel via bot account."""

    async def execute(self, **kwargs) -> Response:
        channel_id = self.args.get("channel_id", "")
        content = self.args.get("content", "")
        reply_to = self.args.get("reply_to", "")
        action = self.args.get("action", "send")

        try:
            channel_id = validate_snowflake(channel_id, "channel_id")
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        config = get_discord_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)
        if not (config.get("bot", {}).get("token", "") or "").strip():
            return Response(
                message="Error: Bot token not configured. Sending requires a bot account.",
                break_loop=False,
            )

        allowed_servers = normalize_allowed_servers(config.get("servers", []))
        client = None
        try:
            client = DiscordClient.from_config(agent=self.agent, mode="bot")
            await require_allowed_target(client, channel_id, allowed_servers)

            if action == "send":
                if not content:
                    return Response(message="Error: content is required for sending.", break_loop=False)

                chunks = _split_message(content)
                sent_ids = []
                for i, chunk in enumerate(chunks):
                    ref = reply_to if i == 0 and reply_to else None
                    result = await client.send_message(channel_id=channel_id, content=chunk, reply_to=ref)
                    sent_ids.append(result["id"])

                if len(sent_ids) == 1:
                    return Response(message=f"Message sent (ID: {sent_ids[0]}).", break_loop=False)
                return Response(message=f"Message sent in {len(sent_ids)} parts (IDs: {', '.join(sent_ids)}).", break_loop=False)

            elif action == "react":
                emoji = self.args.get("emoji", "")
                message_id = self.args.get("message_id", "")
                if not emoji or not message_id:
                    return Response(message="Error: emoji and message_id required for reactions.", break_loop=False)
                await client.add_reaction(channel_id, message_id, emoji)
                return Response(message=f"Reaction {emoji} added to message {message_id}.", break_loop=False)

            else:
                return Response(message=f"Unknown action '{action}'. Use 'send' or 'react'.", break_loop=False)

        except PermissionError as e:
            return Response(message=str(e), break_loop=False)
        except DiscordAPIError as e:
            return Response(message=f"Discord API error: {e}", break_loop=False)
        except Exception as e:
            return Response(message=f"Error sending to Discord: {type(e).__name__}", break_loop=False)
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass


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

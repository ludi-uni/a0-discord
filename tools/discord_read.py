from helpers.tool import Tool, Response
from usr.plugins.discord.helpers.discord_client import (
    DiscordClient, DiscordAPIError, format_messages, get_discord_config,
    get_modes_to_try, normalize_allowed_servers, require_allowed_guild,
    require_allowed_target,
)
from usr.plugins.discord.helpers.sanitize import require_auth, sanitize_channel_name


class DiscordRead(Tool):
    """Read messages, list channels, or list threads from a Discord server."""

    async def execute(self, **kwargs) -> Response:
        channel_id = self.args.get("channel_id", "")
        thread_id = self.args.get("thread_id", "")
        guild_id = self.args.get("guild_id", "")
        limit = int(self.args.get("limit", "50"))
        after_id = self.args.get("after", "")
        action = self.args.get("action", "messages")

        config = get_discord_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)
        allowed_servers = normalize_allowed_servers(config.get("servers", []))
        explicit_mode = self.args.get("mode", "")
        modes = get_modes_to_try(config, explicit_mode or None)

        last_error = None
        for mode in modes:
            client = None
            try:
                client = DiscordClient.from_config(agent=self.agent, mode=mode)

                if action == "channels":
                    if not guild_id:
                        return Response(message="Error: guild_id is required for listing channels.", break_loop=False)
                    require_allowed_guild(guild_id, allowed_servers)
                    channels = await client.get_guild_channels(guild_id)
                    return Response(message=_format_channels(channels), break_loop=False)

                elif action == "threads":
                    if not guild_id:
                        return Response(message="Error: guild_id is required for listing threads.", break_loop=False)
                    require_allowed_guild(guild_id, allowed_servers)
                    threads_data = await client.get_active_threads(guild_id)
                    threads = threads_data.get("threads", [])
                    return Response(message=_format_threads(threads), break_loop=False)

                elif action == "messages":
                    target_id = thread_id or channel_id
                    if not target_id:
                        return Response(message="Error: channel_id or thread_id is required.", break_loop=False)

                    await require_allowed_target(client, target_id, allowed_servers)
                    messages = await client.get_all_channel_messages(
                        channel_id=target_id, limit=limit, after=after_id or None,
                    )

                    if not messages:
                        return Response(message="No messages found in the specified channel/thread.", break_loop=False)

                    result = format_messages(messages, include_ids=True)
                    label = f"thread {thread_id}" if thread_id else f"channel {channel_id}"
                    return Response(message=f"Retrieved {len(messages)} messages from {label}:\n\n{result}", break_loop=False)

                else:
                    return Response(message=f"Unknown action '{action}'. Use 'messages', 'channels', or 'threads'.", break_loop=False)

            except PermissionError as e:
                return Response(message=f"Error: {e}", break_loop=False)
            except DiscordAPIError as e:
                last_error = e
                if e.status == 403 and mode != modes[-1]:
                    continue  # Try next mode (e.g., user token fallback)
                return Response(message=f"Discord API error: {e}", break_loop=False)
            except Exception as e:
                return Response(message=f"Error reading Discord: {e}", break_loop=False)
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass

        return Response(message=f"Discord API error: {last_error}", break_loop=False)


def _format_channels(channels: list) -> str:
    if not channels:
        return "No channels found."

    categories = {}
    uncategorized = []

    for ch in channels:
        if ch.get("type") == 4:
            categories[ch["id"]] = {"name": sanitize_channel_name(ch["name"]), "channels": []}

    for ch in channels:
        if ch.get("type") == 4:
            continue
        parent = ch.get("parent_id")
        ch_type = _channel_type_name(ch.get("type", 0))
        safe_name = sanitize_channel_name(ch.get("name", "unknown"))
        entry = f"  - [{ch_type}] #{safe_name} (ID: {ch['id']})"
        if parent and parent in categories:
            categories[parent]["channels"].append(entry)
        else:
            uncategorized.append(entry)

    lines = ["Channels:"]
    for cat_data in categories.values():
        lines.append(f"\n{cat_data['name'].upper()}:")
        lines.extend(cat_data["channels"])
    if uncategorized:
        lines.append("\nUNCATEGORIZED:")
        lines.extend(uncategorized)
    return "\n".join(lines)


def _format_threads(threads: list) -> str:
    if not threads:
        return "No active threads found."
    lines = ["Active Threads:"]
    for t in threads:
        safe_name = sanitize_channel_name(t.get("name", "unknown"))
        lines.append(
            f"  - {safe_name} (ID: {t['id']}) "
            f"- {t.get('message_count', '?')} messages, {t.get('member_count', '?')} members"
        )
    return "\n".join(lines)


def _channel_type_name(type_id: int) -> str:
    return {
        0: "text", 2: "voice", 4: "category", 5: "announcement",
        10: "thread", 11: "thread", 12: "thread",
        13: "stage", 15: "forum", 16: "media",
    }.get(type_id, f"type-{type_id}")

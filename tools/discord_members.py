from helpers.tool import Tool, Response
from usr.plugins.discord.helpers.discord_client import (
    DiscordClient, DiscordAPIError, get_discord_config, get_modes_to_try,
    normalize_allowed_servers, require_allowed_guild,
)
from usr.plugins.discord.helpers.persona_registry import (
    upsert_user, get_user, search_users, get_guild_users,
    format_user_profile, load_registry,
)
from usr.plugins.discord.helpers.sanitize import require_auth, sanitize_username


class DiscordMembers(Tool):
    """Query Discord server members and manage the persona registry."""

    async def execute(self, **kwargs) -> Response:
        action = self.args.get("action", "list")
        guild_id = self.args.get("guild_id", "")
        user_id = self.args.get("user_id", "")
        query = self.args.get("query", "")
        notes = self.args.get("notes", "")

        config = get_discord_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        allowed_servers = normalize_allowed_servers(config.get("servers", []))
        try:
            if guild_id:
                require_allowed_guild(guild_id, allowed_servers)
        except PermissionError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        try:
            if action == "list":
                return await self._list_members(guild_id)
            elif action == "info":
                return await self._user_info(guild_id, user_id)
            elif action == "search":
                return self._search_registry(query, guild_id)
            elif action == "note":
                return self._add_note(user_id, notes)
            elif action == "registry":
                return self._show_registry(guild_id)
            elif action == "sync":
                return await self._sync_members(guild_id)
            else:
                return Response(
                    message=f"Unknown action '{action}'. Use: list, info, search, note, registry, sync.",
                    break_loop=False,
                )
        except DiscordAPIError as e:
            return Response(message=f"Discord API error: {e}", break_loop=False)
        except Exception as e:
            return Response(message=f"Error: {e}", break_loop=False)

    async def _get_client(self, mode=None):
        config = get_discord_config(self.agent)
        explicit_mode = mode or self.args.get("mode", "")
        resolved = get_modes_to_try(config, explicit_mode or None)[0]
        return DiscordClient.from_config(agent=self.agent, mode=resolved)

    async def _list_members(self, guild_id: str) -> Response:
        if not guild_id:
            return Response(message="Error: guild_id is required.", break_loop=False)

        config = get_discord_config(self.agent)
        explicit_mode = self.args.get("mode", "")
        modes = get_modes_to_try(config, explicit_mode or None)

        for mode in modes:
            try:
                client = await self._get_client(mode=mode)
                members = await client.get_guild_members(guild_id, limit=100)
                await client.close()

                if not members:
                    return Response(message="No members found or insufficient permissions.", break_loop=False)

                lines = [f"Members of guild {guild_id} ({len(members)} shown):"]
                for m in members:
                    user = m.get("user", {})
                    username = sanitize_username(user.get("username", "Unknown"))
                    display = sanitize_username(m.get("nick") or user.get("global_name") or username)
                    bot_tag = " [BOT]" if user.get("bot") else ""
                    lines.append(f"  - {display} (@{username}, ID: {user.get('id', '?')}){bot_tag} - {len(m.get('roles', []))} roles")
                return Response(message="\n".join(lines), break_loop=False)
            except DiscordAPIError as e:
                try:
                    await client.close()
                except Exception:
                    pass
                if e.status == 403 and mode != modes[-1]:
                    continue
                return Response(message=f"Discord API error: {e}", break_loop=False)

        return Response(message="No members found or insufficient permissions.", break_loop=False)

    async def _user_info(self, guild_id: str, user_id: str) -> Response:
        if not user_id:
            return Response(message="Error: user_id is required.", break_loop=False)

        registry_data = get_user(user_id)
        registry_text = ""
        if registry_data:
            registry_text = "\n\n--- Persona Registry ---\n" + format_user_profile({"user_id": user_id, **registry_data})

        discord_text = ""
        if guild_id:
            try:
                client = await self._get_client()
                member = await client.get_guild_member(guild_id, user_id)
                await client.close()
                user = member.get("user", {})
                username = sanitize_username(user.get("username", "Unknown"))
                display = sanitize_username(member.get("nick") or user.get("global_name") or username)
                discord_text = (
                    f"Discord Profile:\n"
                    f"  Username: @{username}\n"
                    f"  Display Name: {display}\n"
                    f"  User ID: {user_id}\n"
                    f"  Joined: {member.get('joined_at', 'Unknown')[:10]}\n"
                    f"  Roles: {len(member.get('roles', []))} assigned\n"
                    f"  Bot: {'Yes' if user.get('bot') else 'No'}"
                )
                upsert_user(
                    user_id=user_id, username=username, display_name=display,
                    roles=[str(r) for r in member.get("roles", [])], guild_id=guild_id,
                )
            except DiscordAPIError:
                discord_text = f"Could not fetch Discord profile for user {user_id}."

        result = discord_text + registry_text
        return Response(message=result or f"No information found for user {user_id}.", break_loop=False)

    def _search_registry(self, query: str, guild_id: str) -> Response:
        if not query:
            return Response(message="Error: query is required for search.", break_loop=False)
        results = search_users(query, guild_id=guild_id or None)
        if not results:
            return Response(message=f"No users found matching '{query}'.", break_loop=False)
        lines = [f"Found {len(results)} users matching '{query}':"]
        for user in results:
            lines.append(f"\n{format_user_profile(user)}\n---")
        return Response(message="\n".join(lines), break_loop=False)

    def _add_note(self, user_id: str, notes: str) -> Response:
        if not user_id or not notes:
            return Response(message="Error: user_id and notes are required.", break_loop=False)
        existing = get_user(user_id)
        if not existing:
            return Response(message=f"User {user_id} not in registry. Use 'sync' or 'info' first.", break_loop=False)
        upsert_user(user_id=user_id, username=existing.get("username", "Unknown"), notes=notes)
        return Response(message=f"Note added for user {user_id}.", break_loop=False)

    def _show_registry(self, guild_id: str) -> Response:
        if guild_id:
            users = get_guild_users(guild_id)
        else:
            registry = load_registry()
            users = [{"user_id": uid, **data} for uid, data in registry.get("users", {}).items()]
        if not users:
            return Response(message="Persona registry is empty.", break_loop=False)
        lines = [f"Persona Registry ({len(users)} users):"]
        for user in users:
            lines.append(f"\n{format_user_profile(user)}\n---")
        return Response(message="\n".join(lines), break_loop=False)

    async def _sync_members(self, guild_id: str) -> Response:
        if not guild_id:
            return Response(message="Error: guild_id is required for sync.", break_loop=False)

        client = await self._get_client()
        members = await client.get_guild_members(guild_id, limit=1000)
        await client.close()

        synced = 0
        for m in members:
            user = m.get("user", {})
            if user.get("bot"):
                continue
            upsert_user(
                user_id=user.get("id", ""), username=user.get("username", "Unknown"),
                display_name=m.get("nick") or user.get("global_name"),
                roles=[str(r) for r in m.get("roles", [])], guild_id=guild_id,
            )
            synced += 1
        return Response(message=f"Synced {synced} members from guild {guild_id} to persona registry.", break_loop=False)

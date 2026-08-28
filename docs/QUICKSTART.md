# Discord Integration Plugin — Quick Start

## Prerequisites

- Agent Zero instance (Docker or local)
- Discord account

## Step 1: Create a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** > name it > **Create**
3. Go to the **Bot** tab > click **Reset Token** > copy the **entire** token
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent** (required for reading messages)
   - **Server Members Intent** (recommended for member sync)
5. Go to **Installation** tab (left sidebar):
   - Keep **Guild Install** checked (not User Install)
   - Under Default Install Settings for Guild Install, add scope `bot`
   - Add permissions: View Channels, Send Messages, Read Message History, Add Reactions, Embed Links, Manage Messages
6. Copy the install link > open in browser > invite the bot to your server

## Step 2: Install the Plugin

**Via Plugin Hub (Recommended):**
1. Open Agent Zero WebUI > Settings > Plugins
2. Find "Discord Integration" and click **Install**
3. Dependencies install automatically

**Via Script:**
```bash
docker cp a0-discord/. a0-container:/tmp/discord-plugin/
docker exec a0-container bash /tmp/discord-plugin/install.sh
```

## Step 3: Configure

1. Open Agent Zero WebUI
2. Go to Settings > External Services > Discord Integration
3. Paste your bot token in the **Bot Token** field
4. Click the **Save** button (outer framework Save)
5. Click **"Open"** to view the dashboard
6. Click **"Test Connection"** — should show green "Connected as @botname"

### Credential Mapping

| What You Need | Where to Get It | Plugin Config Field |
|---|---|---|
| Bot Token | Discord Developer Portal → Bot → Reset Token | Settings > **Bot Token** |
| Server IDs | Right-click server name → Copy Server ID (Developer Mode) | Settings > **Allowed Server IDs** |
| User Token (optional) | Browser DevTools (gray area, read-only) | Settings > **User Token** |

## Step 4: Get Your Discord IDs

Enable **Developer Mode** in Discord (User Settings > Advanced > Developer Mode), then:

| What | How |
|------|-----|
| **Server ID** | Right-click server name > **Copy Server ID** |
| **Channel ID** | Right-click channel name > **Copy Channel ID** |
| **User ID** | Right-click a username > **Copy User ID** |

Or extract IDs from a Discord URL: `https://discord.com/channels/SERVER_ID/CHANNEL_ID`

## Step 5: Start Using It

Open Agent Zero's chat and try:

| What you want | What to say |
|---------------|-------------|
| See server structure | "List channels in Discord server YOUR_SERVER_ID" |
| Read messages | "Read the last 20 messages in Discord channel YOUR_CHANNEL_ID" |
| Summarize a channel | "Summarize Discord channel YOUR_CHANNEL_ID" |
| Deep research | "Extract insights from Discord channel YOUR_CHANNEL_ID focused on [topic]" |
| Send a message | "Send 'Hello!' to Discord channel YOUR_CHANNEL_ID" |
| List members | "List members in Discord server YOUR_SERVER_ID" |
| Chat bridge | "Add channel YOUR_CHANNEL_ID to the chat bridge, then start it" |
| Monitor alerts | "Watch Discord channel YOUR_CHANNEL_ID for new messages" |

---

## Securing the Chat Bridge

The chat bridge runs in **restricted mode** by default — Discord users can only chat conversationally with the LLM and have no access to Agent Zero's tools or system.

### User Allowlist (Recommended)

Restrict which Discord users can interact with the bot:

**Via WebUI:** Settings > Chat Bridge > User Allowlist > enter Discord user IDs (one per line) > Save

**Via config file:**
```json
{
  "chat_bridge": {
    "allowed_users": ["YOUR_DISCORD_USER_ID"]
  }
}
```

### Elevated Mode (Advanced)

Grants authenticated Discord users full access to Agent Zero. Disabled by default. Before enabling, read the [full security documentation](../README.md#elevated-mode----important).

---

## Known Behaviors

1. **Chat bridge requires channel registration** — The bridge connects to Discord but only responds in channels you explicitly add. Starting the bridge alone is not enough — you must also tell the agent: "Add channel YOUR_CHANNEL_ID to the chat bridge." When the **Servers** allowlist is configured, the channel's actual Discord guild must also be allowed.
2. **First poll returns no alerts** — The initial `discord_poll check` sets the baseline message ID. Only messages posted *after* the first poll are detected.
3. **Chat bridge requires bot token** — User tokens cannot use Discord's Gateway (WebSocket). The bridge will not start without a bot token configured.
3. **Config changes require restart** — After editing config.json directly, restart Agent Zero: `supervisorctl restart run_ui`. WebUI settings changes via the Save button take effect immediately.
4. **Image analysis requires multimodal model** — Charts/screenshots in alerts are forwarded to the LLM. Text-only models receive the data but cannot interpret images.
5. **Message Content Intent required** — Without this enabled in Developer Portal > Bot > Privileged Gateway Intents, the bot receives empty message bodies.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Plugin not loading | Check symlink: `ls -la /a0/plugins/discord` should point to `/a0/usr/plugins/discord` |
| "No module named 'plugins.discord'" | Create symlink: `ln -sf /a0/usr/plugins/discord /a0/plugins/discord` |
| API returns 404 | Files must be in `/a0/` (not just `/git/agent-zero/`). Re-run the installer or copy manually. |
| "Bot token not configured" | Enter token in WebUI Settings, or write to config.json, or set `DISCORD_BOT_TOKEN` env var |
| "Discord API error 401" | Invalid token — regenerate in Developer Portal > Bot > Reset Token |
| "Discord API error 403" | Bot lacks channel permissions (View Channels, Send Messages, Read Message History) |
| Chat bridge not responding | Most common: no channels registered. Tell the agent: "Add channel ID to the chat bridge." Also check User Allowlist and Message Content Intent. |
| Chat bridge won't start | Verify bot token is valid, check network access to `gateway.discord.gg` |
| Test Connection shows "Checking..." | API endpoint may not be loaded — restart Agent Zero |
| Settings don't save | Use the **outer** framework Save button, not any button inside the plugin panel |
| Changes not taking effect | Clear cache: `find /a0 -path '*/discord*/__pycache__' -exec rm -rf {} +` then restart |

For detailed troubleshooting, see [README.md](README.md#troubleshooting) and [SETUP.md](SETUP.md#troubleshooting).

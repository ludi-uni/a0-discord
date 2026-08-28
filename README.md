# Discord Plugin for Agent Zero

A full-featured Discord integration plugin for Agent Zero that enables reading, summarizing, analyzing, and interacting with Discord servers directly through the agent.

## Verification Status

| Stage | Result |
|-------|--------|
| Regression Tests | 52/52 PASS |
| Human Verification | Completed (red-team exercise) |
| Security Assessment | Red-team pentest completed 2026-03-09 |
| Standards Conformance | v1.1.0 |

## Features

- **Read** channels, threads, and messages from any server the bot is in
- **Send** messages and reactions through the bot
- **Summarize** channel conversations with AI-generated structured summaries
- **Extract insights** for deep research analysis of Discord discussions
- **Track members** with a persistent persona registry and notes
- **Monitor channels** for new messages with automatic image analysis
- **Chat bridge** -- use Discord as a real-time chat frontend to Agent Zero's LLM

## Quick Start

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** > name it > **Create**
3. Go to the **Bot** tab > click **Reset Token** > **copy the entire token**
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent** (required for reading messages)
   - **Server Members Intent** (recommended)
5. Go to **Installation** tab (left sidebar):
   - Under **Installation Contexts**, keep **Guild Install** checked (uncheck User Install if present)
   - Under **Default Install Settings** for Guild Install, add scope `bot`
   - Add bot permissions: `View Channels`, `Send Messages`, `Read Message History`, `Add Reactions`, `Embed Links`, `Manage Messages` (required for auto-deleting `!auth` commands)
6. Copy the install link and open it in a browser to invite the bot to your server

### 2. Install the Plugin

**Docker (recommended):**

```bash
# Copy plugin into the container
docker cp discord-plugin/ <container_name>:/a0/usr/plugins/discord

# Create symlink for Python imports
docker exec <container_name> ln -sf /a0/usr/plugins/discord /a0/plugins/discord

# Install Python dependencies
docker exec <container_name> python /a0/usr/plugins/discord/initialize.py

# Enable the plugin
docker exec <container_name> touch /a0/usr/plugins/discord/.toggle-1

# Restart to load
docker exec <container_name> supervisorctl restart run_ui
```

**Using the install script (inside the container):**

```bash
# Copy the plugin source into the container first
docker cp discord-plugin/ <container_name>:/tmp/discord-plugin

# Run the installer
docker exec <container_name> bash /tmp/discord-plugin/install.sh
```

The install script auto-detects the Agent Zero root (`/a0/` or `/git/agent-zero/`), copies files, creates the symlink, installs dependencies, and enables the plugin.

### 3. Configure the Bot Token

**Option A -- Config file (most reliable):**

```bash
docker exec <container_name> bash -c 'cat > /a0/usr/plugins/discord/config.json << EOF
{
  "bot": {
    "token": "YOUR_BOT_TOKEN_HERE"
  }
}
EOF'
```

**Option B -- Environment variable:**

Add to your Docker environment or `.env` file:
```
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
```

**Option C -- WebUI:**

Open Agent Zero's web interface, navigate to the Discord plugin settings page, and enter your bot token.

### 4. Restart Agent Zero

```bash
docker exec <container_name> supervisorctl restart run_ui
```

### 5. Get Your Discord IDs

Enable **Developer Mode** in Discord (User Settings > Advanced > Developer Mode), then:

| What | How |
|------|-----|
| **Server ID** | Right-click server name > **Copy Server ID** |
| **Channel ID** | Right-click channel name > **Copy Channel ID** |
| **User ID** | Right-click a username > **Copy User ID** |

You can also get IDs from a Discord URL: `https://discord.com/channels/SERVER_ID/CHANNEL_ID`

### 6. Start Using It

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

## Documentation

| Document | Description |
|----------|-------------|
| [docs/README.md](docs/README.md) | Full reference -- all tools, configuration, examples, architecture |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | 5-minute setup guide |
| [docs/SETUP.md](docs/SETUP.md) | Credential setup guide (bot creation, permissions, IDs) |
| [docs/CHAT_BRIDGE.md](docs/CHAT_BRIDGE.md) | Chat bridge setup and configuration |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Internal API endpoints and data formats |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | How to extend and contribute |

## Tools

| Tool | Description |
|------|-------------|
| `discord_read` | Read messages, list channels, list threads |
| `discord_send` | Send messages and reactions (bot token required) |
| `discord_summarize` | AI-generated channel/thread summaries |
| `discord_insights` | Deep research analysis of discussions |
| `discord_members` | Query members, manage persona registry |
| `discord_poll` | Monitor channels for new messages with image analysis |
| `discord_chat` | Real-time Discord-to-LLM chat bridge |

## Requirements

- Agent Zero (development branch with plugin framework)
- Python 3.10+
- Discord bot application with Message Content Intent enabled
- Python packages: `aiohttp`, `pyyaml`, `discord.py` (auto-installed by `initialize.py`)

## Architecture

```
usr/plugins/discord/
├── plugin.yaml              # Plugin manifest
├── default_config.yaml      # Default settings
├── config.json              # Active config (created on first save)
├── initialize.py            # Dependency installer
├── hooks.py                 # Plugin lifecycle hooks (install/uninstall)
├── install.sh               # Automated installer
├── helpers/
│   ├── discord_client.py    # REST API wrapper with rate limiting
│   ├── discord_bot.py       # Chat bridge bot (direct LLM, no tools)
│   ├── sanitize.py          # Security: input validation, injection defense
│   ├── persona_registry.py  # Persistent user tracking
│   └── poll_state.py        # Polling state tracker
├── tools/                   # 7 tools (auto-discovered by framework)
├── prompts/                 # LLM tool descriptions
├── extensions/              # Agent lifecycle hooks
├── api/                     # WebUI API endpoints
├── webui/                   # Dashboard + settings UI
├── skills/                  # 5 skill definitions
├── data/                    # Runtime state (auto-created)
└── docs/                    # Documentation
```

## Security

This plugin has been security-hardened with multiple layers of defense. **Read this section carefully before enabling elevated mode.**

### Core Protections

- **Chat bridge privilege isolation** -- The chat bridge uses direct LLM calls (`call_utility_model`) instead of the full agent loop. In restricted mode (the default), Discord users have **zero access** to tools, code execution, file operations, or system resources. If the direct model path is unavailable, restricted execution fails closed and does not fall back to the full Agent Zero API.
- **Prompt injection defense** -- Input sanitization with Unicode homoglyph normalization (NFKC), zero-width character stripping, and pattern-based injection detection.
- **Snowflake ID validation** -- All Discord IDs are validated as 17-20 digit numbers before use in API calls.
- **SSRF protection** -- Image downloads restricted to Discord CDN hosts only.
- **Atomic file writes** -- State files written atomically with restrictive permissions (`0o600`).
- **Per-user rate limiting** -- Sliding window rate limiter (10 messages per 60 seconds) on the chat bridge.
- **Server allowlist enforcement** -- Configured server allowlists are checked against the actual guild ownership returned by Discord for channel and thread resources. Mismatched, guild-less, or unresolvable resources are denied before messages are read or written. The same check applies to polling and the chat bridge.
- **Sanitized error messages** -- Internal details (file paths, stack traces) are never exposed to users.

### User Allowlist

The **User Allowlist** restricts which Discord users can interact with the chat bridge bot. When configured, only the listed user IDs receive responses -- all other users are silently ignored (no error message, no information leakage about the bot's capabilities).

- **Empty allowlist** (default): All server members can interact with the bot.
- **Populated allowlist**: Only listed Discord user IDs can interact. Changes take effect immediately without restarting the bridge.

Configure via WebUI (Settings > Chat Bridge > User Allowlist) or in `config.json`:
```json
{
  "chat_bridge": {
    "allowed_users": ["YOUR_DISCORD_USER_ID"]
  }
}
```

Get user IDs by enabling Developer Mode in Discord (User Settings > Advanced > Developer Mode), then right-click a user > Copy User ID.

### Elevated Mode -- IMPORTANT

Elevated mode allows authenticated Discord users to access the **full Agent Zero agent loop** -- including tools, code execution, file access, and all system capabilities. This is powerful but carries significant security implications.

**How elevated mode works:**
1. An admin enables `allow_elevated: true` in config and obtains the auth key from the WebUI
2. A Discord user types `!auth <key>` in a bridge channel (the message is auto-deleted to protect the key -- requires **Manage Messages** bot permission)
3. The user's session is elevated for the configured timeout (default: 1 hour)
4. The user types `!deauth` (or `!dauth`, `!unauth`, `!logout`, `!logoff`) to end the session early
5. Session state and conversation history are cleared on deauth

**Optimal configuration for elevated mode:**

> **The recommended setup is a private Discord server with only trusted members, a defined User Allowlist, and a single bot.** Ideally, the server should have a single user and the bot -- this provides the strongest security posture by ensuring the communication channel is fully controlled.

If collaboration is required, the plugin supports multiple users, but each user must be explicitly trusted:

1. **Create a dedicated Discord server** -- Do not enable elevated mode on a public or semi-public server. The server itself is part of your security boundary.
2. **Define the User Allowlist** -- List every user ID that should have access. This is your primary access control layer.
3. **Limit server membership** -- Only invite users you deeply trust. Anyone with access to the server could potentially observe bot interactions (depending on channel permissions).
4. **Understand Discord security principles** -- Channel permissions, role hierarchies, and server verification levels all affect who can see and interact with the bot. Ensure you understand these before deploying elevated mode.
5. **Use short session timeouts** -- The default 1-hour timeout limits exposure if a session is left open.
6. **Protect the auth key** -- The `!auth` message is auto-deleted (requires **Manage Messages** permission), but share the key only through secure, out-of-band channels. Regenerate it if you suspect compromise.

**What elevated mode grants access to:**
- Agent Zero's full tool suite (code execution, file operations, web access, etc.)
- The host system's filesystem and network (within Agent Zero's container)
- All other installed Agent Zero plugins and capabilities

**Only enable elevated mode if you fully understand these implications and trust every user on the allowlist.**

For detailed configuration, see [docs/CHAT_BRIDGE.md](docs/CHAT_BRIDGE.md#security).

> **Update notice (March 2026):** If you installed this plugin prior to the security hardening commit, please reinstall to pick up these fixes. The most critical change is the chat bridge architectural isolation -- earlier versions routed Discord messages through the full agent loop, which could allow privilege escalation.

## Troubleshooting

See the [Troubleshooting section](docs/README.md#troubleshooting) in the full documentation.

**Common issues:**

- **"Bot token not configured"** -- Set the token via config file, environment variable, or WebUI
- **"Discord API error 403"** -- Bot lacks channel permissions (View Channels, Read Message History, Send Messages)
- **"Discord API error 401"** -- Invalid or expired token; regenerate in Developer Portal
- **Plugin not loading** -- Ensure the symlink exists: `ls -la /a0/plugins/discord` should point to `/a0/usr/plugins/discord`
- **Import errors** -- The symlink at `/a0/plugins/discord` -> `/a0/usr/plugins/discord` is required for `from plugins.discord.helpers...` imports

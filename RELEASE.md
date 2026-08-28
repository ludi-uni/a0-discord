---
status: published
repo: https://github.com/spinnakergit/a0-discord
index_status: in index
published_date: 2026-03-10
version: 1.1.0
---

# Release Status

## Publication
- **GitHub**: https://github.com/spinnakergit/a0-discord
- **Plugin Index**: Already in [agent0ai/a0-plugins](https://github.com/agent0ai/a0-plugins) index
- **Published**: 2026-03-10

## Verification Completed
- **Automated Tests**: 52/52 PASS
- **Human Verification**: Completed (red-team exercise, pre-framework)
- **Security Assessment**: Red-team pentest completed 2026-03-09 (report committed as redacted pentest report)

## Commit History
| Hash | Date | Description |
|------|------|-------------|
| `5a29b75` | 2026-03-10 | Add required name field and update settings store docs |
| `d2f4f9f` | 2026-03-09 | Add redacted pentest report from red team exercise |
| `c887150` | 2026-03-09 | Security hardening: CSRF protection, auth key masking, regression test suite |
| `69c18b7` | 2026-03-09 | Chat bridge security overhaul: thread-safe lifecycle, user allowlist, elevated mode |
| `f3e50bc` | 2026-03-09 | Security hardening: architectural privilege isolation and input validation |
| `7827ffc` | 2026-03-08 | Initial commit: Discord integration plugin for Agent Zero |

## Notes
- Discord was the first chat-bridge plugin built and served as the reference implementation for Signal, Telegram, and Slack.
- Verification was conducted before the formal HUMAN_VERIFICATION_FRAMEWORK was established; the red-team exercise served as both human verification and security assessment.

## Changelog

### v1.1.1 — 2026-08-28 (fork patch, unreleased)
- Restricted and elevated import failures now fail closed without an HTTP full-agent fallback.
- Elevated mode uses the current `helpers.messages.UserMessage` import path.
- Server allowlists are enforced against actual Discord channel/thread guild ownership across read, write, summary, insights, polling, and chat bridge paths.
- Added security boundary regression tests covering import failure, ownership mismatch, guild-less resources, ID normalization, and client cleanup.

### v1.1.0 — 2026-03-25
Standards conformance release.
- **config.html**: Migrated to A0's standard Alpine.js `x-model` settings framework (removes custom fetchApi/save logic)
- **discord_config_api.py**: Simplified to only handle `generate_auth_key` (config CRUD now handled by framework)
- **main.html**: Rewritten with `data-dcm=` attributes (replaces bare IDs), lazy fetchApi, `window._dcMain` namespace, inline onclick
- **hooks.py**: Rewritten with `logging.getLogger()` (replaces `print()` statements)
- **docs/SETUP.md**: New — full credential setup guide (bot creation, permissions, IDs, 3 install options, troubleshooting)
- **docs/QUICKSTART.md**: Added Plugin Hub install option, credential mapping table, Known Behaviors, expanded Troubleshooting
- **docs/DEVELOPMENT.md**: Rewritten — Alpine.js config pattern, main.html dashboard pattern, architecture decisions, testing section
- **docs/README.md**: Added SETUP link, verification badge, updated architecture tree and API descriptions
- **thumbnail.png**: Added (256x256 RGBA, transparent background)
- **.gitignore**: Added config.json, `.toggle-*`, `data/`, `tests/*.json`, sensitive file patterns
- **README.md**: Added verification badge section, SETUP link in docs table
- All `print()` in hooks.py replaced with `logging.getLogger()`

### v1.0.0 — 2026-03-10
Initial release.
- 7 tools: read, send, summarize, insights, members, poll, chat
- 3 API endpoints: test, config, bridge
- Chat bridge with dual-mode security (restricted/elevated)
- 5 skills: research, communicate, persona-mapping, alerts, chat
- Auto-start extension for chat bridge
- Full verification: 52/52 regression, red-team pentest

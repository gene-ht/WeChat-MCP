<div align="center">

# WeChat MCP Server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/wechat-mcp-server.svg)](https://pypi.org/project/wechat-mcp-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[中文](docs/README_zh.md) | English

</div>

An MCP server that automates WeChat on macOS using the Accessibility API and screen capture. It enables LLMs to interact with WeChat chats programmatically.

## Features

- 📨 Fetch recent messages from any chat (contact or group)
- ✍️ Send replies, or write drafts without sending
- 🛡️ Runtime modes (`active` / `draft_only` / `paused` / `takeover`), chat allow-list, rate limit and duplicate guard
- ✅ Post-send verification: the reply is read back from the conversation, not assumed
- 🧾 JSONL audit trail for every action that touches WeChat
- 📷 Publish text-only Moments posts, with optional draft-only mode
- 👥 Add contacts using WeChat ID with configurable privacy
- 🔍 Smart chat search with exact name matching
- 🤖 5 specialized Claude Code sub-agents for smart WeChat automation

## Quick Start

### Installation

```bash
pip install wechat-mcp-server
```

### Setup with Claude Code

```bash
# If installed via pip
claude mcp add --transport stdio wechat-mcp -- wechat-mcp

# If using uv for development
claude mcp add --transport stdio wechat-mcp -- uv --directory $(pwd) run wechat-mcp
```

<details>
<summary>Setup with Claude Desktop</summary>

```json
// If installed via pip
{
  "mcpServers": {
    "wechat-mcp": {
      "type": "stdio",
      "command": "wechat-mcp"
    }
  }
}

// If using uv for development
{
  "mcpServers": {
    "wechat-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "{path/to/wechat-mcp}",
        "run",
        "wechat-mcp"
      ],
    }
  }
}
```

</details>

<details>
<summary>Setup with Codex</summary>

```bash
# If installed via pip
codex mcp add wechat-mcp -- wechat-mcp

# If using uv for development
codex mcp add wechat-mcp -- uv --directory $(pwd) run wechat-mcp
```

</details>

### macOS Permissions

⚠️ **Important**: Grant Accessibility permissions to your terminal:

1. Open **System Settings → Privacy & Security → Accessibility**
2. Add your terminal application (Terminal.app, iTerm2, etc.)
3. Ensure WeChat is running before using the server

## Usage

### Basic Commands

```bash
# Run with default stdio transport
wechat-mcp --transport stdio

# Run with HTTP transport
wechat-mcp --transport streamable-http

# Run with SSE transport
wechat-mcp --transport sse

# Pause / resume a running server (or persist the mode for the next start)
wechat-mcp --set-mode paused
wechat-mcp --set-mode active

# Shut the running server down
wechat-mcp --stop
```

### Available MCP Tools

| Tool | Purpose |
| --- | --- |
| `get_wechat_status` | Mode, running state, open chat, allow-list, sends this hour |
| `set_wechat_mode` | Switch between `active`, `draft_only`, `paused`, `takeover` |
| `list_wechat_chats` | Names visible in the session list |
| `fetch_messages_by_chat` | Read recent messages; `sender_names=True` adds group sender names |
| `fetch_messages_since` | Collect every message after a date, with timestamps |
| `draft_reply_to_chat` | Write a reply into the composer **without sending** |
| `reply_to_messages_by_chat` | Send a reply, subject to the policy gate |
| `add_contact_by_wechat_id` | Add a contact using a WeChat ID |
| `publish_moment_without_media` | Publish a text-only Moments post (`publish=False` drafts it) |

See [detailed API documentation](docs/detailed-guide.md) for full tool specifications.

## Runtime Modes & Safety

The server refuses to send unless policy allows it. `reply_to_messages_by_chat`
returns `{"sent": false, "blocked_reason": ...}` instead of raising; do not retry
blindly — fall back to `draft_reply_to_chat` or ask the user.

| Mode | Read | Draft | Send |
| --- | --- | --- | --- |
| `active` (default) | ✅ | ✅ | ✅ |
| `draft_only` | ✅ | ✅ | ❌ |
| `paused` | ✅ | ❌ | ❌ |
| `takeover` | ❌ | ❌ | ❌ (the human owns the machine) |

Additional guards:

- **Allow-list** — set `WECHAT_MCP_ALLOWED_CHATS` to limit drafting/sending to named chats. Empty means all chats.
- **Rate limit** — chat replies are capped per rolling hour, friend requests per minute and per day (`artifacts/config/policy.toml`).
- **Duplicate guard** — the same text as your previous message is not sent twice.
- **Draft protection** — drafts are never written over text a human is typing.
- **Verification** — after sending, the newest message is read back; `verified: false` means "sent, but not confirmed".
- **Audit** — every action is appended to `<state_dir>/audit.jsonl` (text is hashed unless `WECHAT_MCP_AUDIT_TEXT=1`).
- **Other UI writes** — `add_contact_by_wechat_id` and `publish_moment_without_media` are also refused in `paused` and `takeover` modes.
- **Human activity wins** — a listen-only event tap notices real keyboard/mouse input; the agent then refuses to read, draft or send, and resumes once the machine has been idle for `WECHAT_MCP_HUMAN_IDLE_SECONDS` (default 30s). Its own synthetic events are ignored via their source PID.
- **Focus is borrowed, not taken** — the app that was frontmost before a call is restored afterwards, and a read of an already-open chat does not activate WeChat at all. Set `WECHAT_MCP_RESTORE_FOCUS=0` to disable restoration.

### Configuration

All settings are optional environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WECHAT_MCP_MODE` | `active` | `active` / `draft_only` / `paused` / `takeover` |
| `WECHAT_MCP_ALLOWED_CHATS` | *(empty = all)* | Comma-separated chat names |
| `WECHAT_MCP_MAX_SENDS_PER_HOUR` | *(policy.toml)* | Overrides `[messages].max_per_hour` |
| `WECHAT_MCP_POLICY_CONFIG` | `artifacts/config/policy.toml` | Policy file location |
| `WECHAT_MCP_MAX_REPLY_CHARS` | `2000` | Maximum reply length |
| `WECHAT_MCP_VERIFY_SENDS` | `1` | Read back after sending |
| `WECHAT_MCP_ALLOW_DUPLICATE` | `0` | Allow sending identical consecutive text |
| `WECHAT_MCP_AUDIT_TEXT` | `0` | Store plaintext bodies in the audit log |
| `WECHAT_MCP_STATE_DIR` | `~/.local/state/wechat-mcp` | State + audit directory |
| `WECHAT_MCP_HUMANIZE` | `1` | Human-like delays between UI actions |
| `WECHAT_MCP_RESTORE_FOCUS` | `1` | Return focus to the previous app after a call |
| `WECHAT_MCP_RESPECT_HUMAN` | `1` | Pause while a human is typing/clicking |
| `WECHAT_MCP_HUMAN_IDLE_SECONDS` | `30` | Idle time before the agent resumes |
| `WECHAT_MCP_OUTREACH_CONFIG` | `artifacts/outreach/outreach.toml` | Channel scripts |
| `WECHAT_MCP_LEADS_CSV` | `artifacts/outreach/leads.csv` | CSV mirror |
| `WECHAT_MCP_OUTREACH_DRY_RUN` | `1` | Preview outreach instead of sending |
| `WECHAT_MCP_MIN_DELAY` | `0.25` | Minimum pause between actions (s) |
| `WECHAT_MCP_MAX_DELAY` | `0.90` | Maximum pause between actions (s) |

The running server also listens on `<state_dir>/control.sock` (owner-only), so
`wechat-mcp --set-mode ...` switches it immediately without restarting the MCP
session. Without a running server the same command persists the mode for the
next start.

A conservative production profile:

```bash
export WECHAT_MCP_MODE=draft_only
export WECHAT_MCP_ALLOWED_CHATS="工作群,客户支持群"
export WECHAT_MCP_MAX_SENDS_PER_HOUR=10
```

## Architecture

```
src/wechat_mcp/
  mcp_server.py          # MCP tools (thin): parse args, call services, map errors
  services.py            # use cases: fetch / draft / send, policy + state + audit
  policy.py              # pure decisions: mode, allow-list, rate limit, duplicates
  state.py               # persisted mode, drafts, send history (atomic JSON)
  audit.py               # append-only JSONL audit log
  config.py              # environment-driven Settings
  composer.py            # composer text + send, focus re-check, read-back verify
  messages.py            # AX message reading, scrolling, sender attribution
  wechat_accessibility.py# WeChat-specific AX navigation (windows, chats, search)
  ax.py / ui.py          # generic AX primitives / synthetic input events
  contacts.py            # add-contact flow
  moments.py             # Moments flow
```

`ax.py` and `ui.py` know nothing about WeChat; `wechat_accessibility.py` is the
only module that encodes WeChat's AX identifiers. Anything that types or clicks
re-checks that WeChat is frontmost first — synthetic keystrokes otherwise land in
whatever window the human is using.

### Limits (`artifacts/config/policy.toml`)

```toml
[messages]
max_per_hour = 30        # chat replies; 0 = unlimited

[friend_request]
max_per_minute = 1       # one request per minute
max_per_day = 100        # at most 100 per rolling 24h

[reply_pacing]
enabled = true
thinking_min = 1.0       # seconds "reading/deciding" before sending
thinking_max = 2.8
typing_chars_per_second = 7.0
min_delay = 0.25         # jittered gap between UI actions
max_delay = 0.90
```

Friend-request timestamps live in `state.json`, so the quota survives restarts.
Dry-run never consumes quota. A refused request returns
`blocked_reason: "friend_request_rate_limited"`.

## Outreach Pipeline

A small CRM for "add by phone, greet by channel, follow up after acceptance".
Copy the example config and edit the copy:

```bash
cp config/outreach.example.toml config/outreach.toml
```

```toml
[defaults]
company = "示例科技"

[channels.douyin_job]
label = "抖音-求职"
greeting = "你好，我是{company}的{recruiter}，看到你想找{user_state}方向的工作。"
followup = ["你好呀，我是{recruiter}。", "方便发一份简历吗？"]

[channels.douyin_job.vars]
recruiter = "小王"
```

Placeholders: `{phone}`, `{name}`, `{user_state}`, `{channel}`,
`{channel_label}`, plus anything in `[defaults]` / `[channels.*.vars]`.

Everything lives in one folder, `artifacts/outreach/`:

| File | Purpose |
| --- | --- |
| `outreach.toml` | channel scripts (edit this) |
| `leads.csv` | the lead table itself — the single source of truth (gitignored) |
| `config/policy.toml` | rate limits: messages per hour, friend requests per minute/day |

Writes are atomic (temp file + rename) and the CSV is reloaded when its mtime
changes, so you can edit it in Numbers/Excel while the server runs:

| Field | Meaning |
| --- | --- |
| `phone` | phone number (primary key) |
| `channel` | which script profile to use |
| `user_state` | free-form state, e.g. 求职 / 渠道合作 |
| `stage` | `new → request_sent → accepted → greeted → replied` (+ `failed`/`skipped`) |
| timestamps | `request_sent_at`, `accepted_at`, `greeting_sent_at`, `replied_at` |

Tools: `import_leads`, `list_leads`, `send_friend_request`,
`send_lead_followup`, `mark_lead_stage`.

**Dry-run is the default.** `send_friend_request` and `send_lead_followup`
return `would_send` and change nothing until you set
`WECHAT_MCP_OUTREACH_DRY_RUN=0`. Acceptance and replies are recorded manually
for now (`mark_lead_stage`), because WeChat does not expose "this request was
accepted" through AX.

```python
import_leads([{"phone": "1300000000", "channel": "douyin_job",
               "name": "张三", "user_state": "运营"}])
send_friend_request("1300000000")           # preview, then real when dry-run off
mark_lead_stage("1300000000", "accepted")   # human confirms
send_lead_followup("1300000000")
```

## Claude Code Sub-Agents

This project includes 5 intelligent sub-agents designed specifically for WeChat automation. They enable natural language control of WeChat through Claude Code.

### Available Sub-Agents

1. **Chat-summarizer** - Summarize chat history and extract key information
2. **Auto-replier** - Auto-generate and send appropriate replies
3. **Message-searcher** - Search chat history for specific content
4. **Multi-chat-checker** - Monitor multiple chats and prioritize messages
5. **Chat-insights** - Analyze relationship dynamics and communication patterns

📖 [View complete sub-agents guide](.claude/agents/README.md)

## Development

### Local Setup with uv

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and setup
git clone https://github.com/yourusername/WeChat-MCP.git
cd WeChat-MCP
uv sync

# Run locally
uv run wechat-mcp --transport stdio

# Unit tests (no WeChat required)
uv run pytest

# Live smoke tests against the real client (opt-in)
WECHAT_MCP_LIVE=1 uv run pytest tests/test_live.py -m live -s
```

## Local Data & Privacy

Runtime data — state, audit log, leads, exported job data and the live policy
configs — lives under `artifacts/`. That directory is **private**: it is
git-ignored as a whole, never packaged, and must not be shared. See
[Artifacts.md](Artifacts.md) for the initialisation steps, layout, data formats and sharing rules.

## Documentation

- 📘 [Detailed Guide](docs/detailed-guide.md) - Complete API documentation and architecture
- 🤖 [Sub-Agents Guide](.claude/agents/README.md) - How to use Claude Code sub-agents

## Requirements

- macOS (uses Accessibility API)
- WeChat for Mac installed and running
- Python 3.12+
- Accessibility permissions for terminal

> **Known limitation (WeChat 4.1.x):** some 4.1.x builds on recent macOS
> releases expose only the window frame to the Accessibility API — no sidebar,
> message list or input field — which breaks AX-based automation. Run
> `get_wechat_status`: `"ax_tree_ok": false` means the installed client cannot
> be driven this way. WeChat 3.8.x works. See
> [dustin573/wechat-mcp#1](https://github.com/dustin573/wechat-mcp/issues/1).

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see [LICENSE](LICENSE) file for details

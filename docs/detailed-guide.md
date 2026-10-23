# WeChat MCP Server - Detailed Guide

This document describes the tools, architecture, safety model and operational
details of the WeChat MCP server.

## Overview

The server automates WeChat for macOS through the Accessibility API and screen
capture, exposing tools that an LLM client can call to:

- inspect the current runtime mode and the open conversation;
- read recent messages from a specific chat;
- write a reply draft without sending it;
- send a reply, subject to a deterministic policy gate;
- add contacts and publish text-only Moments posts.

## Tools exposed to MCP clients

| Tool | Signature | Purpose |
| --- | --- | --- |
| `get_wechat_status` | `() -> dict` | Mode, running state, open chat, allow-list, sends this hour |
| `set_wechat_mode` | `(mode: str) -> dict` | `active` / `draft_only` / `paused` / `takeover` |
| `list_wechat_chats` | `() -> list[str]` | Chat names visible in the session list |
| `fetch_messages_by_chat` | `(chat_name: str, last_n: int = 50) -> list[dict]` | Read recent messages |
| `draft_reply_to_chat` | `(chat_name: str, reply_message: str) -> dict` | Write the composer, do not send |
| `reply_to_messages_by_chat` | `(chat_name: str, reply_message: str \| None = None) -> dict` | Send, policy permitting |
| `add_contact_by_wechat_id` | `(...) -> dict` | Drive the add-contact flow |
| `publish_moment_without_media` | `(content: str, publish: bool = True) -> dict` | Text-only Moments post |

### `fetch_messages_by_chat`

Opens the chat for `chat_name` (session list first, then global search),
preferring an **exact name match** under "Contacts", then "Group Chats", and
ignoring "Chat History", "Official Accounts", "Internet search results" and
"More". If no exact match exists it returns up to 15 candidates per section
instead of guessing. Messages are returned oldest-first:

```json
{"sender": "ME" | "OTHER" | "UNKNOWN", "text": "message text"}
```

Reading scrolls through history, which requires WeChat to be frontmost; the
message text itself comes from `AXStaticText` values, not OCR. Sender
attribution is the only part that uses pixels: when WeChat is not frontmost the
screenshot is skipped and senders are reported as `UNKNOWN` rather than guessed
from the wrong window.

### `draft_reply_to_chat`

Writes `reply_message` into the chat composer and stops. The text is recorded as
a pending draft so the human can approve or edit it. If the composer already
contains different text, the tool refuses (`input_not_empty`) instead of
clobbering a draft a human may be typing.

### `reply_to_messages_by_chat`

Sends `reply_message` after the policy gate. Success shape:

```json
{
  "ok": true,
  "action": "send_reply",
  "chat_name": "群名",
  "sent": true,
  "verified": true,
  "method": "axpress",
  "duration_ms": 812,
  "mode": "active"
}
```

Refused sends return `{"ok": false, "sent": false, "blocked_reason": "..."}`
without touching WeChat. Possible reasons:

| Reason | Meaning |
| --- | --- |
| `mode_draft_only` | Server is in draft-only mode |
| `mode_paused` | Auto-reply is paused |
| `mode_takeover` | The human owns WeChat |
| `chat_not_allowed` | Chat is outside `WECHAT_MCP_ALLOWED_CHATS` |
| `rate_limited` | Hourly send budget exhausted |
| `duplicate_reply` | Identical to your previous message |
| `empty_reply` / `reply_too_long` | Text fails validation |

`verified: false` means the keystroke was posted but the reply could not be read
back from the conversation; it still counts as sent. Sending never retries
automatically.

### `add_contact_by_wechat_id`

Drives WeChat's "Search WeChat ID" → "Add Contacts" → "Send Friend Request"
flow: types the ID into global search, clicks the "Search WeChat ID" card,
presses "Add to Contacts", then optionally fills the friending message, remark
and privacy options (`all` vs `chats_only`, with the two hide-posts flags).

### `publish_moment_without_media`

Opens Moments, long-presses "Post", fills the composer with `content`, and
publishes unless `publish=False`, in which case the sheet is left open.

## Runtime modes and policy

`policy.py` is pure logic over `Settings` + `StateStore`; no AX calls happen
there, so every rule is unit-testable.

| Mode | Read | Draft | Send |
| --- | --- | --- | --- |
| `active` (default) | ✅ | ✅ | ✅ |
| `draft_only` | ✅ | ✅ | ❌ |
| `paused` | ✅ | ❌ | ❌ |
| `takeover` | ❌ | ❌ | ❌ |

The send path evaluates, in order: mode → allow-list → text validity →
duplicate → hourly rate limit. The cheap checks run before any UI interaction so
a refusal costs nothing. The duplicate check reads the newest message of the
target chat first, which is why `ensure_chat` runs before the second evaluation.

## Architecture

```
src/wechat_mcp/
  mcp_server.py           MCP tools: parse args, call services, map errors
  services.py             use cases: open / fetch / draft / send (policy, state, audit)
  policy.py               pure decisions: mode, allow-list, rate limit, duplicates
  state.py                persisted mode, drafts, send history (atomic JSON)
  audit.py                append-only JSONL audit log
  config.py               environment-driven Settings
  composer.py             composer text + send, focus re-check, read-back verify
  messages.py             AX message reading, scrolling, sender attribution
  wechat_accessibility.py WeChat-specific AX navigation (windows, chats, search)
  ax.py                   generic AX primitives and geometry
  ui.py                   synthetic input events (keys, clicks, scroll, clipboard)
  contacts.py             add-contact flow
  moments.py              Moments flow
  models.py / errors.py   shared types and typed errors
```

Call path for a reply:

```
MCP tool -> WeChatService.send_reply
          -> RuntimePolicy.check_send          (cheap refusal)
          -> ensure_chat (session list/search)
          -> messages.read_last_message       (duplicate guard)
          -> RuntimePolicy.check_send         (with last message)
          -> composer.send_message
               -> set_input_value              (refuse to clobber a draft)
               -> ensure_frontmost             (re-check before Return)
               -> AXPress send button, or Return
               -> read-back verification
          -> StateStore.record_send + AuditLog.record
```

### Focus policy

`ax.py` and `ui.py` know nothing about WeChat. `wechat_accessibility.py` is the
only module that encodes WeChat's AX identifiers (`session_item_*`,
`big_title_line_h_view`, `chat_input_field`, `search_list`, ...).

Synthetic keystrokes are posted to the HID tap, so they land in whatever window
is frontmost. Every path that types or clicks therefore calls
`ensure_frontmost()` first and treats "WeChat did not become frontmost" as an
error rather than sending input into an unknown window. Read-only helpers pass
`activate=False` so inspection does not steal focus.

## Pausing and resuming

Two independent paths:

| Who | How | Effect |
| --- | --- | --- |
| The agent | `set_wechat_mode("paused")` tool | Immediate |
| The human | `wechat-mcp --set-mode paused` | Immediate via the control socket |
| Config | `WECHAT_MCP_MODE=paused` | Applies on start when no state file exists |
| No server running | `wechat-mcp --set-mode paused` | Persists for the next start |

The control socket lives at `<state_dir>/control.sock`, is created with mode
`0600` and speaks one JSON object per line (`{"cmd": "set_mode", "mode": ...}`
or `{"cmd": "status"}`). Stop the MCP server itself by ending the client
session (stdio) or removing the server from the MCP configuration.

## State and audit files

| File | Contents |
| --- | --- |
| `<state_dir>/state.json` | Mode, pending drafts, recent send timestamps |
| `<state_dir>/audit.jsonl` | One JSON object per action, append-only |

`state.json` is written atomically (temp file + `os.replace`), so a crash cannot
leave a half-written file. Corrupt files are ignored and rebuilt from defaults.
Audit entries record the chat, mode, decision and duration; the message body is
hashed (SHA-256 prefix) unless `WECHAT_MCP_AUDIT_TEXT=1`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `WECHAT_MCP_MODE` | `active` | Runtime mode |
| `WECHAT_MCP_ALLOWED_CHATS` | *(empty = all)* | Comma-separated chat allow-list |
| `WECHAT_MCP_MAX_SENDS_PER_HOUR` | `30` | Sliding-window limit |
| `WECHAT_MCP_MAX_REPLY_CHARS` | `2000` | Maximum reply length |
| `WECHAT_MCP_VERIFY_SENDS` | `1` | Read back after sending |
| `WECHAT_MCP_ALLOW_DUPLICATE` | `0` | Allow identical consecutive text |
| `WECHAT_MCP_AUDIT_TEXT` | `0` | Plaintext bodies in the audit log |
| `WECHAT_MCP_STATE_DIR` | `~/.local/state/wechat-mcp` | State and audit directory |
| `WECHAT_MCP_LOG_DIR` | `logs` | Server log directory |
| `WECHAT_MCP_HUMANIZE` | `1` | Human-like pacing between UI actions |
| `WECHAT_MCP_MIN_DELAY` / `WECHAT_MCP_MAX_DELAY` | `0.25` / `0.90` | Jittered pause range in seconds |

## Logging

- File handler: `logs/wechat_mcp.log` (DEBUG)
- Console handler: stdout (INFO)
- `WECHAT_MCP_LOG_DIR` overrides the directory
- `--mcp-debug` raises MCP/anyio/httpx loggers to DEBUG

## macOS and Accessibility requirements

- WeChat must be running (`com.tencent.xinWeChat`).
- The hosting process (terminal, IDE or DSH) needs **Accessibility** permission
  in System Settings → Privacy & Security → Accessibility.
- **Screen Recording** is needed only for sender attribution.

## Dependencies

```
pyobjc >= 12.1
pyobjc-framework-applicationservices >= 12.1
pillow >= 10.0.0
mcp[cli] >= 1.0.0, < 2.0    # FastMCP lives in mcp.server.fastmcp on 1.x
```

## Supported transports

- **stdio** (default)
- **streamable-http**
- **sse**

```bash
wechat-mcp --transport stdio
wechat-mcp --transport streamable-http
wechat-mcp --transport sse
wechat-mcp --set-mode draft_only
```

## Development

```bash
uv sync
uv run wechat-mcp --transport stdio

# unit tests, no WeChat needed
uv run pytest

# live smoke tests, opt-in
WECHAT_MCP_LIVE=1 WECHAT_MCP_LIVE_CHAT="文件传输助手" uv run pytest tests/test_live.py -m live -s
```

Tests never touch the real Accessibility API unless marked `live`;
`tests/test_composer.py` patches the AX layer, and `tests/test_services.py`
patches the WeChat navigator.

## Troubleshooting

### Accessibility permissions

1. System Settings → Privacy & Security → Accessibility.
2. Add the terminal application.
3. Enable its checkbox and restart the terminal.

### WeChat not found

Start WeChat before the server. Bundle id: `com.tencent.xinWeChat`.

### "WeChat did not become frontmost"

Another app grabbed focus between activation and the keystroke. The send is
aborted by design; retry once the machine is idle, or switch to `draft_only` and
send manually.

### `ax_tree_ok: false` / "only exposes its window frame"

WeChat 4.1.x builds on recent macOS releases can run as a Qt client whose chat
UI is not published to the Accessibility API. `get_wechat_status` reports the
element count and the client version; a healthy client exposes dozens of
elements, a broken one exposes about a dozen (window frame + traffic lights).

There is no code fix on this side: the UI tree is simply absent. Options are to
use WeChat 3.8.x, or to drive the 4.x client through its keyboard shortcuts /
screenshots instead of AX. The server refuses to guess rather than clicking
blind.

### Sends return `blocked_reason`

Check `get_wechat_status` for the mode and allow-list, and the audit log for the
decision. Do not retry the same call in a loop.

## TODO

- [x] Detect and switch to contact by clicking
- [x] Scroll to get full/more history messages
- [x] Prefer exact match in Contacts/Group Chats search results
- [x] Add contact using WeChat ID
- [x] Refactor the accessibility codebase
- [x] Runtime modes, allow-list, duplicate/rate guards and audit log
- [x] Post-send read-back verification
- [ ] Edit contact/group chat info
- [x] Publish moment w/o media
- [ ] Fetch moments by chat name
- [ ] Support WeChat with Chinese language
- [ ] Identify OTHER with explicit name

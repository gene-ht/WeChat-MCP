# Repository Guidelines

## Project Structure & Modules
- Core code lives in `src/wechat_mcp`, layered from top to bottom:
  - `mcp_server.py` — MCP tools only (parse args, call services, map errors).
  - `services.py` — use cases; the only place where policy, state and audit meet.
  - `policy.py` / `state.py` / `audit.py` / `config.py` — pure runtime rules and persistence.
  - `composer.py` / `messages.py` — reply and message read paths.
  - `outreach/` — lead table (SQLite), channel scripts (TOML), outreach workflow.
  - `wechat_accessibility.py` — WeChat-specific AX navigation (windows, chats, search).
  - `history_export.py` — one-way daily history export + merged-forward collection + txt renderer.
  - `ax.py` / `ui.py` — generic AX primitives and synthetic input; no WeChat knowledge.
- Entry point is `wechat_mcp.mcp_server:main`, exposed as the `wechat-mcp` script.
- Logs are written under `logs/` by default (configurable via `WECHAT_MCP_LOG_DIR`).
- State and audit live under `WECHAT_MCP_STATE_DIR` (default `~/.local/state/wechat-mcp`).

## Build, Run & Development
- Install dependencies: `uv sync` from the repository root.
- Run the MCP server (stdio): `uv run wechat-mcp --transport stdio`.
- Run over HTTP/SSE: `uv run wechat-mcp --transport streamable-http` or `--transport sse`.
- Enable protocol debugging: `uv run wechat-mcp --mcp-debug --transport stdio`.
- Change the persisted runtime mode: `uv run wechat-mcp --set-mode draft_only`.
- Export a chat's daily history into `artifacts/chat-history/<chat>/`:
  `uv run python -m wechat_mcp.history_export "会话名" --days 1`
  (rules: `artifacts/chat-history/README.md`, knobs: `artifacts/config/history.toml`).

## Coding Style & Naming
- Python 3.12+, PEP 8 style, 4-space indentation, type hints where practical.
- Use `snake_case` for functions/variables, `PascalCase` for classes, and relative imports within `wechat_mcp`.
- Prefer structured logging via `logging_config.logger`; avoid `print` for runtime behavior.
- Keep functions small, with clear docstrings explaining interaction with macOS Accessibility APIs.

## Testing Guidelines
- Tests use `pytest` under `tests/` mirroring `src/wechat_mcp`.
- Name test files `test_*.py` and test functions `test_*`.
- Run tests with `uv run pytest`.
- Unit tests must not touch the real Accessibility API: patch `wechat_mcp.composer`,
  `wechat_mcp.messages` or the navigator functions (see `tests/test_services.py`).
- Real-client checks belong in `tests/test_live.py` behind `WECHAT_MCP_LIVE=1` and
  the `live` marker.

## Commit & Pull Request Guidelines
- Follow Conventional Commit style seen in history (e.g., `feat:`, `docs:`, `refactor(scope):`).
- Keep commits focused and descriptive; explain behavior changes, not just code moves.
- PRs should include: summary, motivation, how to reproduce/verify, and any macOS/WeChat version specifics.
- Attach relevant log snippets from `logs/wechat_mcp.log` when debugging Accessibility issues.

## Agent-Specific Instructions
- Keep diffs minimal and localized; avoid large refactors unless explicitly requested.
- Preserve existing logging, error handling, and transport behavior when extending features.
- Update `README.md` and this file when adding new tools, transports, or configuration knobs.
- New write actions must go through `services.py` + `policy.py`; never call
  `composer`/`wechat_accessibility` write paths directly from a tool.
- Never post synthetic keystrokes without checking that WeChat is frontmost.
- Sends must be verified by reading the conversation back, not assumed.
- `artifacts/` holds private runtime and business data. It is git-ignored as a
  whole: never commit, package, or share it. See `Artifacts.md`.

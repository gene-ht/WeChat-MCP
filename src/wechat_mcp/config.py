"""Runtime configuration for the WeChat MCP server.

All settings are optional and default to the historical behaviour of the
server: no chat allow-list and full read/write access. Production setups
should set ``WECHAT_MCP_MODE=draft_only`` and ``WECHAT_MCP_ALLOWED_CHATS``.

Environment variables
---------------------
WECHAT_MCP_STATE_DIR       Directory for state/audit files (default: XDG state).
WECHAT_MCP_MODE            active | draft_only | paused | takeover
WECHAT_MCP_ALLOWED_CHATS   Comma separated chat names; empty means all chats.
WECHAT_MCP_MAX_SENDS_PER_HOUR  Sliding-window send limit (default: 30).
WECHAT_MCP_MAX_REPLY_CHARS Maximum reply length (default: 2000).
WECHAT_MCP_VERIFY_SENDS    "0" disables post-send read-back verification.
WECHAT_MCP_ALLOW_DUPLICATE "1" allows sending the same text twice in a row.
WECHAT_MCP_AUDIT_TEXT      "1" stores plaintext message bodies in the audit log.
WECHAT_MCP_HUMANIZE        "0" disables human-like delays between UI actions.
WECHAT_MCP_MIN_DELAY       Minimum pause between UI actions, in seconds.
WECHAT_MCP_MAX_DELAY       Maximum pause between UI actions, in seconds.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import RuntimeMode, parse_mode

APP_DIR_NAME = "wechat-mcp"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    items = [item.strip() for item in raw.replace("，", ",").split(",")]
    return tuple(item for item in items if item)


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def default_state_dir() -> Path:
    """Return a writable state directory.

    Honours ``WECHAT_MCP_STATE_DIR`` first. Otherwise uses the XDG location, and
    falls back to ``<cwd>/artifacts/state`` when that is not writable — which is
    the case inside sandboxes that only allow the workspace (e.g. DSH).
    """
    override = os.getenv("WECHAT_MCP_STATE_DIR")
    if override:
        return Path(override).expanduser()

    xdg_state = os.getenv("XDG_STATE_HOME")
    base = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    candidate = base / APP_DIR_NAME
    if _is_writable(candidate):
        return candidate

    fallback = Path.cwd() / "artifacts" / "state"
    logging.getLogger("wechat_mcp").warning(
        "State directory %s is not writable; falling back to %s", candidate, fallback
    )
    return fallback


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the server configuration."""

    state_dir: Path = field(default_factory=default_state_dir)
    mode: RuntimeMode = RuntimeMode.ACTIVE
    allowed_chats: tuple[str, ...] = ()
    max_sends_per_hour: int | None = None
    max_reply_chars: int = 2000
    verify_sends: bool = True
    allow_duplicate_reply: bool = False
    audit_include_text: bool = False
    humanize: bool = True
    restore_focus: bool = True
    respect_human: bool = True
    human_idle_seconds: float = 30.0
    outreach_config_path: Path | None = None
    leads_csv_path: Path | None = None
    policy_config_path: Path | None = None
    outreach_dry_run: bool = True
    min_action_delay: float | None = None
    max_action_delay: float | None = None

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "audit.jsonl"

    @property
    def resolved_policy_config(self) -> Path:
        return (
            self.policy_config_path or Path("artifacts") / "config" / "policy.toml"
        ).expanduser()

    @property
    def resolved_outreach_config(self) -> Path:
        return (
            self.outreach_config_path
            or Path("artifacts") / "outreach" / "outreach.toml"
        ).expanduser()

    @property
    def resolved_leads_csv(self) -> Path:
        return (
            self.leads_csv_path or Path("artifacts") / "outreach" / "leads.csv"
        ).expanduser()

    def is_chat_allowed(self, chat_name: str) -> bool:
        """An empty allow-list means "all chats"; matching is case-insensitive."""
        if not self.allowed_chats:
            return True
        target = chat_name.strip().casefold()
        return any(target == name.strip().casefold() for name in self.allowed_chats)


def load_settings() -> Settings:
    """Build :class:`Settings` from the current environment."""
    return Settings(
        state_dir=default_state_dir(),
        mode=parse_mode(os.getenv("WECHAT_MCP_MODE"), default=RuntimeMode.ACTIVE),
        allowed_chats=_env_list("WECHAT_MCP_ALLOWED_CHATS"),
        max_sends_per_hour=(
            _env_int("WECHAT_MCP_MAX_SENDS_PER_HOUR", 30)
            if os.getenv("WECHAT_MCP_MAX_SENDS_PER_HOUR") is not None
            else None
        ),
        max_reply_chars=_env_int("WECHAT_MCP_MAX_REPLY_CHARS", 2000, minimum=1),
        verify_sends=_env_bool("WECHAT_MCP_VERIFY_SENDS", True),
        allow_duplicate_reply=_env_bool("WECHAT_MCP_ALLOW_DUPLICATE", False),
        audit_include_text=_env_bool("WECHAT_MCP_AUDIT_TEXT", False),
        humanize=_env_bool("WECHAT_MCP_HUMANIZE", True),
        restore_focus=_env_bool("WECHAT_MCP_RESTORE_FOCUS", True),
        respect_human=_env_bool("WECHAT_MCP_RESPECT_HUMAN", True),
        human_idle_seconds=_env_float("WECHAT_MCP_HUMAN_IDLE_SECONDS", 30.0, minimum=1.0),
        min_action_delay=(
            _env_float("WECHAT_MCP_MIN_DELAY", 0.25)
            if os.getenv("WECHAT_MCP_MIN_DELAY")
            else None
        ),
        max_action_delay=(
            _env_float("WECHAT_MCP_MAX_DELAY", 0.90, minimum=0.01)
            if os.getenv("WECHAT_MCP_MAX_DELAY")
            else None
        ),
        outreach_config_path=(
            Path(os.environ["WECHAT_MCP_OUTREACH_CONFIG"]).expanduser()
            if os.getenv("WECHAT_MCP_OUTREACH_CONFIG")
            else None
        ),
        leads_csv_path=(
            Path(os.environ["WECHAT_MCP_LEADS_CSV"]).expanduser()
            if os.getenv("WECHAT_MCP_LEADS_CSV")
            else None
        ),
        outreach_dry_run=_env_bool("WECHAT_MCP_OUTREACH_DRY_RUN", True),
        policy_config_path=(
            Path(os.environ["WECHAT_MCP_POLICY_CONFIG"]).expanduser()
            if os.getenv("WECHAT_MCP_POLICY_CONFIG")
            else None
        ),
    )

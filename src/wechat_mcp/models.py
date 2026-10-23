"""Domain models shared by the AX layer, services and MCP tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

SenderLabel = Literal["ME", "OTHER", "UNKNOWN"]


class RuntimeMode(StrEnum):
    """How much autonomy the server currently has.

    ACTIVE      read + draft + send
    DRAFT_ONLY  read + draft, never send
    PAUSED      read only; the agent is idle
    TAKEOVER    the human owns WeChat; the server performs no UI actions
    """

    ACTIVE = "active"
    DRAFT_ONLY = "draft_only"
    PAUSED = "paused"
    TAKEOVER = "takeover"

    @property
    def allows_draft(self) -> bool:
        return self in (RuntimeMode.ACTIVE, RuntimeMode.DRAFT_ONLY)

    @property
    def allows_send(self) -> bool:
        return self is RuntimeMode.ACTIVE

    @property
    def allows_read(self) -> bool:
        return self is not RuntimeMode.TAKEOVER


_MODE_ALIASES = {
    "active": RuntimeMode.ACTIVE,
    "auto": RuntimeMode.ACTIVE,
    "on": RuntimeMode.ACTIVE,
    "draft": RuntimeMode.DRAFT_ONLY,
    "draft_only": RuntimeMode.DRAFT_ONLY,
    "draft-only": RuntimeMode.DRAFT_ONLY,
    "paused": RuntimeMode.PAUSED,
    "pause": RuntimeMode.PAUSED,
    "takeover": RuntimeMode.TAKEOVER,
    "human": RuntimeMode.TAKEOVER,
    "handover": RuntimeMode.TAKEOVER,
}


def try_parse_mode(value: str | RuntimeMode | None) -> RuntimeMode | None:
    """Parse a user supplied mode, returning ``None`` when it is unknown."""
    if value is None:
        return None
    if isinstance(value, RuntimeMode):
        return value
    return _MODE_ALIASES.get(value.strip().lower())


def parse_mode(value: str | RuntimeMode | None, *, default: RuntimeMode) -> RuntimeMode:
    """Parse a user supplied mode string, falling back to ``default``."""
    return try_parse_mode(value) or default


@dataclass(frozen=True)
class ChatMessage:
    sender: SenderLabel
    text: str
    sender_name: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        if self.sender_name is None:
            payload.pop("sender_name", None)
        return payload

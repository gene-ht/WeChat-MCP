"""Lead model and workflow stages for outreach (add-friend + follow-up)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LeadStage(StrEnum):
    NEW = "new"
    REQUEST_SENT = "request_sent"
    ACCEPTED = "accepted"
    GREETED = "greeted"
    REPLIED = "replied"
    FAILED = "failed"
    SKIPPED = "skipped"


#: Allowed forward moves; a stage may always be set to itself.
TRANSITIONS: dict[LeadStage, set[LeadStage]] = {
    LeadStage.NEW: {LeadStage.REQUEST_SENT, LeadStage.SKIPPED, LeadStage.FAILED},
    LeadStage.REQUEST_SENT: {
        LeadStage.ACCEPTED,
        LeadStage.FAILED,
        LeadStage.SKIPPED,
    },
    LeadStage.ACCEPTED: {LeadStage.GREETED, LeadStage.FAILED},
    LeadStage.GREETED: {LeadStage.REPLIED, LeadStage.FAILED},
    LeadStage.REPLIED: set(),
    LeadStage.FAILED: {LeadStage.NEW, LeadStage.SKIPPED},
    LeadStage.SKIPPED: {LeadStage.NEW},
}

#: Flags that flip on when a lead reaches a stage.
STAGE_FLAGS: dict[LeadStage, str] = {
    LeadStage.REQUEST_SENT: "greeting_sent",
    LeadStage.ACCEPTED: "accepted",
    LeadStage.GREETED: "followup_sent",
    LeadStage.REPLIED: "replied",
}


class StageTransitionError(ValueError):
    """Raised when a lead is moved to a stage that is not reachable."""


def can_transition(current: LeadStage, target: LeadStage) -> bool:
    return current == target or target in TRANSITIONS.get(current, set())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


@dataclass
class Lead:
    phone: str
    channel: str
    name: str | None = None
    wechat_name: str | None = None
    user_state: str | None = None
    stage: LeadStage = LeadStage.NEW
    greeting_sent: bool = False
    accepted: bool = False
    followup_sent: bool = False
    replied: bool = False
    note: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["stage"] = self.stage.value
        return payload

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Lead:
        """Build a lead from a CSV row, tolerating the old ``identifier`` key."""
        phone = str(row.get("phone") or row.get("identifier") or "").strip()
        if not phone:
            raise ValueError("lead row is missing phone")
        stage_raw = str(row.get("stage") or LeadStage.NEW.value).strip() or "new"
        return cls(
            phone=phone,
            channel=str(row.get("channel") or "").strip(),
            name=(str(row["name"]).strip() or None) if row.get("name") else None,
            wechat_name=(str(row["wechat_name"]).strip() or None)
            if row.get("wechat_name")
            else None,
            user_state=(str(row["user_state"]).strip() or None)
            if row.get("user_state")
            else None,
            stage=LeadStage(stage_raw),
            greeting_sent=_as_bool(row.get("greeting_sent")),
            accepted=_as_bool(row.get("accepted")),
            followup_sent=_as_bool(row.get("followup_sent")),
            replied=_as_bool(row.get("replied")),
            note=(str(row["note"]).strip() or None) if row.get("note") else None,
            created_at=str(row.get("created_at") or utc_now()),
            updated_at=str(row.get("updated_at") or utc_now()),
        )

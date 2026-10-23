"""CSV-backed lead table.

The CSV is the single source of truth: one row per outreach target, easy to
open in Numbers/Excel or edit by hand. Writes are atomic (temp file + rename)
and the file is reloaded whenever its mtime changes, so an edit made while the
server is running is picked up before the next operation.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .models import (
    STAGE_FLAGS,
    Lead,
    LeadStage,
    StageTransitionError,
    can_transition,
    utc_now,
)

logger = logging.getLogger("wechat_mcp.outreach")

CSV_COLUMNS = (
    "phone",
    "channel",
    "name",
    "wechat_name",
    "user_state",
    "stage",
    "greeting_sent",
    "accepted",
    "followup_sent",
    "replied",
    "note",
    "created_at",
    "updated_at",
)

_BOOL_COLUMNS = {"greeting_sent", "accepted", "followup_sent", "replied"}


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


class LeadStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._mtime: int | None = None
        self._leads: dict[str, Lead] = {}
        self._reload(force=True)

    # --------------------------------------------------------------- storage
    def _reload(self, *, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._leads = {}
            self._mtime = None
            return
        if not force and mtime == self._mtime:
            return

        leads: dict[str, Lead] = {}
        try:
            with self.path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    try:
                        lead = Lead.from_row(row)
                    except (KeyError, ValueError) as exc:
                        logger.warning("Skipping malformed lead row: %s", exc)
                        continue
                    leads[lead.phone] = lead
        except OSError as exc:
            logger.warning("Could not read leads from %s: %s", self.path, exc)
            return

        self._leads = leads
        self._mtime = mtime

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        ordered = sorted(
            self._leads.values(), key=lambda lead: lead.updated_at, reverse=True
        )
        try:
            with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                for lead in ordered:
                    data = lead.to_dict()
                    writer.writerow(
                        {key: _csv_value(data.get(key)) for key in CSV_COLUMNS}
                    )
            os.replace(tmp, self.path)
            self._mtime = self.path.stat().st_mtime_ns
        except OSError as exc:
            logger.warning("Could not write leads to %s: %s", self.path, exc)

    def close(self) -> None:
        """Kept for API compatibility; nothing to close for a CSV."""

    # ------------------------------------------------------------------ crud
    def upsert(
        self,
        phone: str,
        channel: str,
        *,
        name: str | None = None,
        wechat_name: str | None = None,
        user_state: str | None = None,
        note: str | None = None,
    ) -> Lead:
        """Insert a lead if new; otherwise refresh the descriptive fields."""
        phone = phone.strip()
        if not phone:
            raise ValueError("phone is required")

        with self._lock:
            self._reload()
            now = utc_now()
            existing = self._leads.get(phone)
            if existing is None:
                lead = Lead(
                    phone=phone,
                    channel=channel,
                    name=name,
                    wechat_name=wechat_name,
                    user_state=user_state,
                    note=note,
                    stage=LeadStage.NEW,
                    created_at=now,
                    updated_at=now,
                )
            else:
                lead = Lead(
                    phone=phone,
                    channel=channel or existing.channel,
                    name=name if name is not None else existing.name,
                    wechat_name=(
                        wechat_name
                        if wechat_name is not None
                        else existing.wechat_name
                    ),
                    user_state=(
                        user_state if user_state is not None else existing.user_state
                    ),
                    stage=existing.stage,
                    greeting_sent=existing.greeting_sent,
                    accepted=existing.accepted,
                    followup_sent=existing.followup_sent,
                    replied=existing.replied,
                    note=note if note is not None else existing.note,
                    created_at=existing.created_at,
                    updated_at=now,
                )
            self._leads[phone] = lead
            self._save()
            return lead

    def get(self, phone: str) -> Lead:
        with self._lock:
            self._reload()
            lead = self._leads.get(phone)
        if lead is None:
            raise KeyError(f"No lead for {phone!r}")
        return lead

    def exists(self, phone: str) -> bool:
        with self._lock:
            self._reload()
            return phone in self._leads

    def list(
        self,
        *,
        stage: LeadStage | None = None,
        channel: str | None = None,
        limit: int = 100,
    ) -> list[Lead]:
        with self._lock:
            self._reload()
            leads = list(self._leads.values())
        if stage is not None:
            leads = [lead for lead in leads if lead.stage is stage]
        if channel:
            leads = [lead for lead in leads if lead.channel == channel]
        leads.sort(key=lambda lead: lead.updated_at, reverse=True)
        return leads[: max(1, min(limit, 10000))]

    def update_stage(
        self, phone: str, stage: LeadStage, *, note: str | None = None
    ) -> Lead:
        with self._lock:
            self._reload()
            lead = self._leads.get(phone)
            if lead is None:
                raise KeyError(f"No lead for {phone!r}")
            if not can_transition(lead.stage, stage):
                raise StageTransitionError(
                    f"Cannot move {phone!r} from {lead.stage.value} to {stage.value}"
                )
            lead.stage = stage
            lead.updated_at = utc_now()
            flag = STAGE_FLAGS.get(stage)
            if flag:
                setattr(lead, flag, True)
            if note is not None:
                lead.note = note
            self._leads[phone] = lead
            self._save()
            return lead

    def update_fields(self, phone: str, **fields: Any) -> Lead:
        allowed = {"name", "wechat_name", "user_state", "note", "channel"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported fields: {sorted(unknown)}")
        with self._lock:
            self._reload()
            lead = self._leads.get(phone)
            if lead is None:
                raise KeyError(f"No lead for {phone!r}")
            for key, value in fields.items():
                setattr(lead, key, value)
            lead.updated_at = utc_now()
            self._leads[phone] = lead
            self._save()
            return lead

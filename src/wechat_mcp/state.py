"""Persisted runtime state: mode, drafts and send history.

State lives in ``<state_dir>/state.json`` and is written atomically so a crash
cannot leave a half-written file behind. The store is intentionally small: it
backs the policy layer (rate limiting, duplicate detection) and lets the human
see what the agent is about to do.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import logger
from .models import RuntimeMode, parse_mode

STATE_VERSION = 1
SEND_HISTORY_WINDOW_SECONDS = 24 * 60 * 60
FRIEND_REQUEST_WINDOW_SECONDS = 24 * 60 * 60


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Draft:
    """A reply composed but not yet sent."""

    text: str
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Draft:
        return cls(
            text=str(raw.get("text", "")),
            updated_at=str(raw.get("updated_at") or _utc_now()),
        )


class StateStore:
    """Thread-safe, file-backed state store."""

    def __init__(self, path: Path, *, default_mode: RuntimeMode = RuntimeMode.ACTIVE):
        self._path = Path(path)
        self._default_mode = default_mode
        self._lock = threading.RLock()
        self._mode = default_mode
        self._drafts: dict[str, Draft] = {}
        self._sends: list[float] = []
        self._friend_requests: list[float] = []
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable state file %s: %s", self._path, exc)
            return

        if not isinstance(raw, dict):
            logger.warning("Ignoring malformed state file %s", self._path)
            return

        self._mode = parse_mode(raw.get("mode"), default=self._default_mode)
        drafts = raw.get("drafts")
        if isinstance(drafts, dict):
            self._drafts = {
                str(chat): Draft.from_dict(value)
                for chat, value in drafts.items()
                if isinstance(value, dict)
            }
        sends = raw.get("sends")
        if isinstance(sends, list):
            now = time.time()
            self._sends = [
                float(ts)
                for ts in sends
                if isinstance(ts, (int, float)) and now - float(ts) < SEND_HISTORY_WINDOW_SECONDS
            ]
        requests = raw.get("friend_requests")
        if isinstance(requests, list):
            now = time.time()
            self._friend_requests = [
                float(ts)
                for ts in requests
                if isinstance(ts, (int, float))
                and now - float(ts) < FRIEND_REQUEST_WINDOW_SECONDS
            ]

    def _save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "mode": self._mode.value,
            "drafts": {chat: draft.to_dict() for chat, draft in self._drafts.items()},
            "sends": self._sends,
            "friend_requests": self._friend_requests,
            "updated_at": _utc_now(),
        }
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._path)
        except OSError as exc:
            # Persistence is best-effort: a read-only state directory must not
            # break drafting or sending. In-memory state stays authoritative for
            # this process; the next restart simply starts from defaults.
            logger.warning(
                "Could not persist state to %s (%s); continuing with in-memory "
                "state. Set WECHAT_MCP_STATE_DIR to a writable directory.",
                self._path,
                exc,
            )

    # ---------------------------------------------------------------- mode
    @property
    def mode(self) -> RuntimeMode:
        with self._lock:
            return self._mode

    def set_mode(self, mode: RuntimeMode) -> RuntimeMode:
        with self._lock:
            self._mode = mode
            self._save()
            logger.info("Runtime mode set to %s", mode.value)
            return self._mode

    # -------------------------------------------------------------- drafts
    def get_draft(self, chat_name: str) -> Draft | None:
        with self._lock:
            return self._drafts.get(chat_name)

    def set_draft(self, chat_name: str, text: str) -> Draft:
        with self._lock:
            draft = Draft(text=text)
            self._drafts[chat_name] = draft
            self._save()
            return draft

    def clear_draft(self, chat_name: str) -> None:
        with self._lock:
            if self._drafts.pop(chat_name, None) is not None:
                self._save()

    def pending_drafts(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {chat: draft.to_dict() for chat, draft in self._drafts.items()}

    # --------------------------------------------------------------- sends
    def record_send(self, timestamp: float | None = None) -> None:
        with self._lock:
            self._sends.append(time.time() if timestamp is None else float(timestamp))
            cutoff = time.time() - SEND_HISTORY_WINDOW_SECONDS
            self._sends = [ts for ts in self._sends if ts >= cutoff]
            self._save()

    def record_friend_request(self, timestamp: float | None = None) -> None:
        with self._lock:
            now = time.time() if timestamp is None else float(timestamp)
            self._friend_requests.append(now)
            cutoff = time.time() - FRIEND_REQUEST_WINDOW_SECONDS
            self._friend_requests = [
                ts for ts in self._friend_requests if ts >= cutoff
            ]
            self._save()

    def friend_requests_since(self, cutoff: float) -> int:
        with self._lock:
            return sum(1 for ts in self._friend_requests if ts >= cutoff)

    def sends_since(self, cutoff: float) -> int:
        with self._lock:
            return sum(1 for ts in self._sends if ts >= cutoff)

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode.value,
                "pending_drafts": len(self._drafts),
                "drafts": {
                    chat: draft.to_dict() for chat, draft in self._drafts.items()
                },
                "sends_last_hour": self.sends_since(time.time() - 3600),
                "friend_requests_last_day": self.friend_requests_since(
                    time.time() - 24 * 3600
                ),
            }

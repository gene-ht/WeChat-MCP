"""Append-only audit trail for every action that touches WeChat.

One JSON object per line in ``<state_dir>/audit.jsonl``. Message bodies are
reduced to a length plus a SHA-256 prefix unless ``WECHAT_MCP_AUDIT_TEXT=1``
is set, because chat content is sensitive and audit files live on disk for a
long time.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import logger


def _digest(text: str) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class AuditLog:
    """Best-effort JSONL writer; auditing must never break an action."""

    def __init__(self, path: Path, *, include_text: bool = False) -> None:
        self._path = Path(path)
        self._include_text = include_text
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, action: str, **fields: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "action": action,
        }
        text = fields.pop("text", None)
        if isinstance(text, str):
            entry["text_len"] = len(text)
            entry["text_sha256"] = _digest(text)
            if self._include_text:
                entry["text"] = text

        entry.update({key: value for key, value in fields.items() if value is not None})

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False)
            with self._lock, self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            logger.warning("Failed to write audit entry to %s: %s", self._path, exc)
        return entry


class NullAuditLog(AuditLog):
    """Audit sink used in tests; keeps entries in memory."""

    def __init__(self) -> None:
        self._path = Path("/dev/null")
        self._include_text = False
        self._lock = threading.Lock()
        self.entries: list[dict[str, Any]] = []

    def record(self, action: str, **fields: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"ts": time.time(), "action": action}
        entry.update(fields)
        self.entries.append(entry)
        return entry

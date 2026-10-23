"""Policy knobs loaded from ``artifacts/config/policy.toml``.

Keeping limits in a file (rather than only environment variables) makes them
visible and editable next to the rest of the business config:

    [messages]
    max_per_hour = 30

    [friend_request]
    max_per_minute = 1
    max_per_day = 100

Missing file or malformed values fall back to conservative defaults.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PolicyConfig:
    messages_max_per_hour: int = 30
    friend_request_max_per_minute: int = 1
    friend_request_max_per_day: int = 100
    pacing_enabled: bool = True
    thinking_min: float = 1.0
    thinking_max: float = 2.8
    typing_chars_per_second: float = 7.0
    min_delay: float = 0.25
    max_delay: float = 0.90

    @classmethod
    def load(cls, path: Path | str | None) -> PolicyConfig:
        if path is None:
            return cls()
        file_path = Path(path)
        if not file_path.exists():
            return cls()
        try:
            with file_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return cls()

        messages = raw.get("messages") or {}
        friend = raw.get("friend_request") or {}
        pacing = raw.get("reply_pacing") or {}
        return cls(
            messages_max_per_hour=_positive_int(
                messages.get("max_per_hour"), cls.messages_max_per_hour
            ),
            friend_request_max_per_minute=_positive_int(
                friend.get("max_per_minute"), cls.friend_request_max_per_minute
            ),
            friend_request_max_per_day=_positive_int(
                friend.get("max_per_day"), cls.friend_request_max_per_day
            ),
            pacing_enabled=_bool(pacing.get("enabled"), cls.pacing_enabled),
            thinking_min=_positive_float(
                pacing.get("thinking_min"), cls.thinking_min
            ),
            thinking_max=_positive_float(
                pacing.get("thinking_max"), cls.thinking_max
            ),
            typing_chars_per_second=_positive_float(
                pacing.get("typing_chars_per_second"),
                cls.typing_chars_per_second,
            ),
            min_delay=_positive_float(pacing.get("min_delay"), cls.min_delay),
            max_delay=_positive_float(pacing.get("max_delay"), cls.max_delay),
        )


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(value: object, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_int(value: object, default: int) -> int:
    """Parse a limit; ``0`` means unlimited and is preserved."""
    if value is None:
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default

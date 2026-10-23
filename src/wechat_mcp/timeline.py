"""Timeline helpers for date-bounded message exports.

WeChat does not put timestamps on individual bubbles; it inserts separator
rows ("Yesterday 17:01", "3/9 10:56", "2025年3月9日 10:56"). Those markers are
the only way to know when a message was sent, so this module parses them,
merges overlapping screenfuls while scrolling up, and cuts the timeline at a
requested date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

CN_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_TIME = r"(\d{1,2}):(\d{2})"
_AM_PM = r"(?:(上午|下午|凌晨|晚上)\s*)?"


@dataclass
class TimelineRow:
    text: str
    sender: str
    at: datetime | None = None
    is_marker: bool = field(default=False)
    sender_name: str | None = None

    def key(self) -> tuple[str, str]:
        return self.text, self.sender


def _build(base: datetime, hour: int, minute: int, meridiem: str | None) -> datetime:
    if meridiem in ("下午", "晚上") and hour < 12:
        hour += 12
    if meridiem == "凌晨" and hour == 12:
        hour = 0
    return base.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)


def parse_time_label(text: str, now: datetime | None = None) -> datetime | None:
    """Parse a WeChat timestamp separator into a local ``datetime``."""
    now = now or datetime.now()
    label = text.strip()
    if not label or len(label) > 40:
        return None

    match = re.fullmatch(rf"{_AM_PM}{_TIME}", label)
    if match:
        meridiem, hour, minute = match.group(1), int(match.group(2)), int(match.group(3))
        return _build(now, hour, minute, meridiem)

    match = re.fullmatch(rf"(?:昨天|Yesterday)\s*{_AM_PM}{_TIME}", label, re.IGNORECASE)
    if match:
        meridiem, hour, minute = match.group(1), int(match.group(2)), int(match.group(3))
        return _build(now - timedelta(days=1), hour, minute, meridiem)

    match = re.fullmatch(rf"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*{_TIME}", label, re.IGNORECASE)
    if match:
        target = WEEKDAYS[match.group(1).lower()]
        delta = (now.weekday() - target) % 7 or 7
        return _build(now - timedelta(days=delta), int(match.group(2)), int(match.group(3)), None)

    match = re.fullmatch(rf"星期([一二三四五六日天])\s*{_TIME}", label)
    if match:
        delta = (now.weekday() - CN_WEEKDAYS[match.group(1)]) % 7 or 7
        return _build(now - timedelta(days=delta), int(match.group(2)), int(match.group(3)), None)

    match = re.fullmatch(rf"(\d{{4}})年(\d{{1,2}})月(\d{{1,2}})日\s*{_AM_PM}{_TIME}", label)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return _build(datetime(year, month, day), int(match.group(5)), int(match.group(6)), match.group(4))

    match = re.fullmatch(rf"(\d{{1,2}})月(\d{{1,2}})日\s*{_AM_PM}{_TIME}", label)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        return _build(now.replace(month=month, day=day), int(match.group(4)), int(match.group(5)), match.group(3))

    match = re.fullmatch(rf"(\d{{1,2}})/(\d{{1,2}})/(\d{{2,4}})\s*{_TIME}", label)
    if match:
        month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        return _build(datetime(year, month, day), int(match.group(4)), int(match.group(5)), None)

    match = re.fullmatch(rf"(\d{{1,2}})/(\d{{1,2}})\s*{_TIME}", label)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        return _build(now.replace(month=month, day=day), int(match.group(3)), int(match.group(4)), None)

    return None


def rows_from_messages(messages, now: datetime | None = None) -> list[TimelineRow]:
    """Convert visible messages into rows, flagging timestamp separators."""
    now = now or datetime.now()
    rows: list[TimelineRow] = []
    for message in messages:
        at = parse_time_label(message.text, now)
        rows.append(
            TimelineRow(
                text=message.text,
                sender=message.sender,
                sender_name=getattr(message, "sender_name", None),
                at=at,
                is_marker=at is not None,
            )
        )
    return rows


def merge_views(collected: list[TimelineRow], view: list[TimelineRow]) -> list[TimelineRow]:
    """Merge an older screenful above ``collected``, trimming the overlap."""
    if not collected:
        return list(view)
    if not view:
        return collected

    limit = min(len(collected), len(view), 12)
    for size in range(limit, 0, -1):
        if [row.text for row in view[-size:]] == [row.text for row in collected[:size]]:
            return _drop_repeats(view[:-size] + collected)
    return _drop_repeats(view + collected)


def _drop_repeats(rows: list[TimelineRow]) -> list[TimelineRow]:
    """Collapse identical consecutive rows produced by overlapping views."""
    result: list[TimelineRow] = []
    for row in rows:
        if result and result[-1].text == row.text and result[-1].sender == row.sender:
            continue
        result.append(row)
    return result


def backfill_times(rows: list[TimelineRow]) -> list[TimelineRow]:
    """Attach each message to the nearest marker above it."""
    current: datetime | None = None
    result: list[TimelineRow] = []
    for row in rows:
        if row.is_marker:
            current = row.at
            continue
        if row.at is None:
            row.at = current
        result.append(row)
    return result


def cut_after_since(
    rows: list[TimelineRow], since: datetime
) -> list[TimelineRow] | None:
    """Return rows after the last marker older than ``since``.

    ``None`` means the boundary has not been reached yet, so the caller should
    keep scrolling. Timestamps are backfilled from the preceding marker.
    """
    boundary: int | None = None
    for index, row in enumerate(rows):
        if row.is_marker and row.at is not None and row.at < since:
            boundary = index
    if boundary is None:
        return None

    # Messages inherit the timestamp of the marker above them, starting with
    # the boundary marker itself.
    current: datetime | None = rows[boundary].at
    result: list[TimelineRow] = []
    for row in rows[boundary + 1 :]:
        if row.is_marker:
            current = row.at
            continue
        if row.at is None:
            row.at = current
        if row.at is not None and row.at < since:
            continue
        result.append(row)
    return result

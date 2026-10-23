from __future__ import annotations

from datetime import datetime

from wechat_mcp.timeline import (
    TimelineRow,
    cut_after_since,
    merge_views,
    parse_time_label,
)

NOW = datetime(2026, 10, 23, 12, 0)


def test_parse_today_time():
    assert parse_time_label("14:05", NOW) == datetime(2026, 10, 23, 14, 5)


def test_parse_yesterday():
    assert parse_time_label("Yesterday 09:30", NOW) == datetime(2026, 10, 22, 9, 30)
    assert parse_time_label("昨天 17:01", NOW) == datetime(2026, 10, 22, 17, 1)


def test_parse_weekday_rolls_back():
    result = parse_time_label("Tuesday 11:48", NOW)
    assert result is not None
    assert result.weekday() == 1
    assert result < NOW
    assert (NOW - result).days <= 7


def test_parse_chinese_weekday():
    result = parse_time_label("星期二 11:48", NOW)
    assert result is not None and result.weekday() == 1


def test_parse_month_day_uses_current_year():
    assert parse_time_label("3/9 10:56", NOW) == datetime(2026, 3, 9, 10, 56)
    assert parse_time_label("3月9日 10:56", NOW) == datetime(2026, 3, 9, 10, 56)


def test_parse_full_date():
    assert parse_time_label("2025年3月9日 10:56", NOW) == datetime(2025, 3, 9, 10, 56)
    assert parse_time_label("3/9/2025 10:56", NOW) == datetime(2025, 3, 9, 10, 56)


def test_parse_meridiem():
    assert parse_time_label("下午 3:05", NOW) == datetime(2026, 10, 23, 15, 5)


def test_plain_messages_are_not_markers():
    assert parse_time_label("在吗", NOW) is None
    assert parse_time_label("Meeting at 10:30 tomorrow", NOW) is None


def test_merge_views_trims_overlap():
    def row(text: str) -> TimelineRow:
        return TimelineRow(text=text, sender="OTHER")

    collected = [row("A"), row("B"), row("C")]
    view = [row("X"), row("A"), row("B")]
    merged = merge_views(collected, view)
    assert [item.text for item in merged] == ["X", "A", "B", "C"]


def test_merge_views_without_overlap_concatenates():
    collected = [TimelineRow("A", "OTHER")]
    view = [TimelineRow("X", "OTHER")]
    assert [item.text for item in merge_views(collected, view)] == ["X", "A"]


def test_cut_after_since_backfills_timestamps():
    rows = [
        TimelineRow("10/20 09:00", "UNKNOWN", datetime(2026, 10, 20, 9, 0), True),
        TimelineRow("old", "OTHER"),
        TimelineRow("昨天 17:01", "UNKNOWN", datetime(2026, 10, 22, 17, 1), True),
        TimelineRow("new", "ME"),
        TimelineRow("newer", "OTHER"),
    ]
    since = datetime(2026, 10, 21)
    cut = cut_after_since(rows, since)

    assert cut is not None
    assert [item.text for item in cut] == ["new", "newer"]
    assert cut[0].at == datetime(2026, 10, 22, 17, 1)
    assert all(item.at >= since for item in cut)


def test_cut_after_since_returns_none_before_boundary():
    rows = [
        TimelineRow("昨天 17:01", "UNKNOWN", datetime(2026, 10, 22, 17, 1), True),
        TimelineRow("new", "ME"),
    ]
    assert cut_after_since(rows, datetime(2026, 10, 1)) is None

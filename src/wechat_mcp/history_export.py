"""One-way history export for a chat, with human-presence pausing.

Rules implemented here:

* start at the newest message, walk upwards only;
* stop when a date separator older than the target day appears, or when the
  previously collected watermark is reached;
* merged-forward cards are opened, scrolled from top to bottom unconditionally
  (their content is static), then closed;
* while a human is using the keyboard/mouse the export pauses; it resumes after
  ``human_idle_seconds`` of quiet.

Usage:
    python -m wechat_mcp.history_export "聊天名" [--days 1]
"""

from __future__ import annotations

import argparse
import json
import re
import time
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ApplicationServices import (
    AXUIElementCopyElementAtPosition,
    AXUIElementCreateSystemWide,
    AXUIElementPerformAction,
    kAXChildrenAttribute,
    kAXPressAction,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXWindowsAttribute,
)

from .activity import HumanActivityMonitor, Presence
from .ax import ax_get, element_frame
from .config import load_settings
from .logging_config import logger
from .messages import get_messages_list, read_visible_messages
from .services import get_service
from .timeline import TimelineRow, backfill_times, parse_time_label
from .ui import click_at, post_scroll
from .wechat_accessibility import get_list_center, get_wechat_ax_app

CARD_TITLE = "群聊的聊天记录"
TZ8 = timezone(timedelta(hours=8))
DEFAULT_CONFIG = Path("artifacts/config/history.toml")


def _iso8(value: datetime) -> str:
    """Render a local wall-clock time as ISO 8601 with a +08:00 offset."""
    return value.replace(tzinfo=TZ8).isoformat(timespec="minutes")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _human_pause(presence: Presence) -> None:
    """Block while a human is interacting; resume after the idle window."""
    if not presence.human_active():
        return
    logger.info("Human input detected; pausing collection")
    while presence.human_active():
        time.sleep(0.5)
    logger.info("Human idle again; resuming")


class Exporter:
    def __init__(self, chat: str, config: dict[str, Any], presence: Presence) -> None:
        self.chat = chat
        self.presence = presence
        scroll_cfg = config.get("scroll", {})
        card_cfg = config.get("merged_card", {})
        self.step = int(scroll_cfg.get("step", 12))
        self.settle = float(scroll_cfg.get("settle_seconds", 0.8))
        self.retries = int(scroll_cfg.get("retries", 2))
        self.max_pages = int(scroll_cfg.get("max_pages", 40))
        self.card_wait = float(card_cfg.get("wait_seconds", 3.0))
        self.card_steps = int(card_cfg.get("max_steps", 80))
        self.app = get_wechat_ax_app(activate=False)
        self.list = get_messages_list(self.app)
        self.list_frame = element_frame(self.list)
        self.center = get_list_center(self.list)

    # ---------------------------------------------------------------- helpers
    def _rows(self) -> list[Any]:
        _human_pause(self.presence)
        return read_visible_messages(self.app)

    def _texts(self) -> list[str]:
        return [row.text for row in self._rows()]

    def scroll(self, delta: int) -> bool:
        before = self._texts()
        for _ in range(self.retries + 1):
            _human_pause(self.presence)
            post_scroll(self.center, delta)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                time.sleep(0.15)
                if self._texts() != before:
                    return True
            _human_pause(self.presence)
            click_at(self.center)
            time.sleep(0.3)
        return False

    # ------------------------------------------------------------- card logic
    def _read_card(self) -> list[str] | None:
        card = None
        for row in ax_get(self.list, kAXChildrenAttribute) or []:
            title = ax_get(row, kAXTitleAttribute) or ax_get(row, kAXValueAttribute)
            if isinstance(title, str) and "Chat History" in title:
                card = row
                break
        if card is None:
            return None

        x, y, w, h = element_frame(card)
        lx, ly, lw, lh = self.list_frame
        top, bottom = max(y, ly), min(y + h, ly + lh)
        if bottom - top < 40:
            return None

        point = (x + 80, (top + bottom) / 2)
        system = AXUIElementCreateSystemWide()
        err, hit = AXUIElementCopyElementAtPosition(system, point[0], point[1], None)
        hit_text = ""
        if hit is not None:
            hit_text = str(
                ax_get(hit, kAXTitleAttribute) or ax_get(hit, kAXValueAttribute) or ""
            )
        if err != 0 or "Chat History" not in hit_text:
            return None

        _human_pause(self.presence)
        click_at(point)
        time.sleep(0.12)
        click_at(point)
        time.sleep(self.card_wait)

        win = None
        for window in ax_get(self.app, kAXWindowsAttribute) or []:
            if ax_get(window, kAXTitleAttribute) == CARD_TITLE:
                win = window
                break
        if win is None:
            return None

        def read_window() -> list[str]:
            out: list[str] = []

            def walk(element: Any, depth: int = 0) -> None:
                if depth > 18:
                    return
                role = ax_get(element, kAXRoleAttribute)
                if role in ("AXStaticText", "AXButton"):
                    value = ax_get(element, kAXValueAttribute) or ax_get(
                        element, kAXTitleAttribute
                    )
                    if value:
                        out.append(str(value))
                for child in ax_get(element, kAXChildrenAttribute) or []:
                    walk(child, depth + 1)

            walk(win)
            return out

        wx, wy, ww, wh = element_frame(win)
        popup_center = (wx + ww / 2, wy + wh / 2)
        _human_pause(self.presence)
        click_at(popup_center)
        time.sleep(0.4)

        collected = read_window()
        seen = set(collected)
        stagnant = 0
        for _ in range(self.card_steps):
            _human_pause(self.presence)
            post_scroll(popup_center, -8)
            time.sleep(1.0)
            current = read_window()
            added = 0
            for text in current:
                if text in seen:
                    continue
                seen.add(text)
                collected.append(text)
                added += 1
            # Five consecutive scrolls that add nothing mean we reached the
            # bottom of this static popup; the step cap above is only a guard
            # against a runaway loop, not the normal stop condition.
            if added == 0:
                stagnant += 1
                if stagnant >= 5:
                    break
            else:
                stagnant = 0

        close = ax_get(win, "AXCloseButton")
        if close is not None:
            AXUIElementPerformAction(close, kAXPressAction)
        time.sleep(0.4)
        return collected

    # ---------------------------------------------------------------- export
    def run(
        self, since: date, anchors: list[str] | None = None
    ) -> dict[str, Any]:
        self._go_to_newest()

        chrono: list[Any] = []          # oldest -> newest
        cards: list[dict[str, Any]] = []
        boundary: dict[str, Any] | None = None
        seen_cards: set[str] = set()
        new_part: list[Any] | None = None
        norm_anchors = [_norm(a) for a in (anchors or []) if a]

        for _ in range(self.max_pages):
            rows = self._rows()
            if not rows:
                break

            # merged-forward cards are always collected in full
            card_row = next((r for r in rows if "Chat History" in r.text), None)
            if card_row is not None:
                key = _norm(card_row.text)[:200]
                if key not in seen_cards:
                    seen_cards.add(key)
                    content = self._read_card()
                    if content:
                        # walking upwards meets newer cards first; keep the
                        # stored list in chronological order (oldest first)
                        cards.insert(
                            0,
                            {
                                "preview": card_row.text,
                                "title": CARD_TITLE,
                                "texts": content,
                            },
                        )
                    rows = self._rows()

            # stop once the boundary date is on screen
            cut = None
            for i, row in enumerate(rows):
                at = parse_time_label(row.text)
                if at and at.date() < since:
                    cut = i
            if cut is not None:
                rows = rows[cut + 1 :]
                boundary = {"type": "date", "value": since.isoformat()}

            # merge upwards: this screen is older than what we already have
            texts = [r.text for r in rows]
            overlap = 0
            for size in range(min(len(chrono), len(rows), 12), 0, -1):
                if texts[-size:] == [r.text for r in chrono[:size]]:
                    overlap = size
                    break
            chrono = list(rows[: len(rows) - overlap]) + chrono

            if boundary:
                break

            # stop when the last three stored messages appear as a block;
            # matching all three avoids a single duplicate message causing
            # an early stop that would lose data.
            if len(norm_anchors) == 3 and len(chrono) >= 3:
                for i in range(len(chrono) - 2):
                    if [_norm(r.text) for r in chrono[i : i + 3]] == norm_anchors:
                        new_part = chrono[i + 3 :]
                        boundary = {
                            "type": "watermark",
                            "value": chrono[i + 2].text,
                            "values": [r.text for r in chrono[i : i + 3]],
                        }
                        break
            if boundary:
                break

            if not self.scroll(self.step):
                boundary = boundary or {"type": "top"}
                break

        merged = new_part if new_part is not None else chrono

        timeline = [
            TimelineRow(
                text=r.text,
                sender=r.sender,
                at=parse_time_label(r.text),
                is_marker=bool(parse_time_label(r.text)),
            )
            for r in merged
        ]
        messages = [m for m in backfill_times(timeline) if not m.is_marker]
        deduped: list[Any] = []
        for message in messages:
            if (
                deduped
                and deduped[-1].text == message.text
                and deduped[-1].sender == message.sender
            ):
                continue
            deduped.append(message)

        return {
            "chat": self.chat,
            "date": since.isoformat(),
            "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "boundary": boundary or {"type": "top"},
            "complete": bool(boundary),
            "messages": [
                {
                    "sender": m.sender,
                    "text": m.text,
                    "at": _iso8(m.at) if m.at else None,
                }
                for m in deduped
            ],
            "cards": cards,
            "watermark": (boundary or {}).get("values", []),
        }

    def _go_to_newest(self) -> None:
        # Content hints are unreliable: the old newest messages stay on screen
        # until we actually scroll, so decide only by whether scrolling still
        # changes what we see.
        for _ in range(self.max_pages + 20):
            if not self.scroll(-self.step):
                return

REGION_ORDER = [
    "海淀", "朝阳", "丰台", "西城", "东城", "大兴", "昌平", "房山", "顺义",
    "门头沟", "通州", "平谷", "亦庄", "天津", "张家口", "怀柔", "密云",
    "延庆", "石景山",
]


def _is_job(text: str) -> bool:
    return ("🦄" in text or "[礼物][礼物]" in text) and "Chat History" not in text


def _region(text: str) -> str:
    for region in REGION_ORDER:
        if region in text:
            return region
    return "其他"


def _title(text: str) -> str:
    for line in text.split("\n"):
        line = line.strip().replace("🦄", "").replace("❗", "").replace("[礼物][礼物]", "")
        if line:
            return line[:44]
    return ""


def render_document(json_path: Path) -> Path:
    """Render the stored JSON into a human-readable txt (same directory)."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    messages = data.get("messages", [])
    cards = data.get("cards", [])

    jobs_main: list[str] = []
    for message in messages:
        text = message.get("text", "")
        if _is_job(text) and text not in jobs_main:
            jobs_main.append(text)

    card_jobs: list[str] = []
    main_norm = {_norm(j) for j in jobs_main}
    for card in cards:
        for text in card.get("texts", []):
            if text.strip() == CARD_TITLE or text in card_jobs:
                continue
            if _norm(text) in main_norm:
                continue
            card_jobs.append(text)

    notices: list[str] = []
    for message in messages:
        text = message.get("text", "")
        if (
            any(k in text for k in ("更新完毕", "接人时间", "合作共赢", "没发的"))
            and text not in notices
        ):
            notices.append(text)

    groups: dict[str, list[str]] = {}
    for text in jobs_main + card_jobs:
        groups.setdefault(_region(text), []).append(text)
    order = [r for r in REGION_ORDER if r in groups]
    if "其他" in groups:
        order.append("其他")

    lines = [
        "岗位信息整理",
        "=" * 50,
        f"会话：{data.get('chat', '')}",
        f"日期：{data.get('date', '')}",
        f"采集时间：{data.get('collected_at', '')}",
        f"消息数：{len(messages)}；合并转发：{len(cards)} 条",
        f"岗位数：{len(jobs_main) + len(card_jobs)}"
        f"（主聊天 {len(jobs_main)}，转发 {len(card_jobs)}）",
        "",
        "说明：按区域分组，原文收录；未改写薪资与要求。",
    ]
    for region in order:
        lines.append("")
        lines.append(f"◆ {region}")
        lines.append("-" * 40)
        for index, text in enumerate(groups[region], 1):
            lines.append(f"{index}. {_title(text)}")
            lines.append("\n".join("   " + ln for ln in text.split("\n") if ln.strip()))
            lines.append("")
    if notices:
        lines.append("")
        lines.append("◆ 通知")
        lines.append("-" * 40)
        for text in notices:
            lines.append("\n".join("  " + ln for ln in text.split("\n") if ln.strip()))

    target = json_path.with_suffix(".txt")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def export_chat(chat: str, days: int = 1) -> Path:
    settings = load_settings()
    config = _load_config(settings.state_dir.parent / "config" / "history.toml")
    today = date.today()
    since = today - timedelta(days=days - 1)

    presence = Presence(settings.human_idle_seconds)
    monitor = HumanActivityMonitor(presence)
    monitor.start()

    out_dir = Path(config.get("output", {}).get("dir", "artifacts/chat-history")) / chat
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{since.isoformat()}.json"

    previous: dict[str, Any] | None = None
    anchors: list[str] = []
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        messages = previous.get("messages") or []
        # Prefer the stored watermark; fall back to the newest stored rows.
        anchors = list(previous.get("watermark") or [])
        if not anchors:
            anchors = [m.get("text", "") for m in messages[-3:]]

    get_service().open_chat(chat)
    exporter = Exporter(chat, config, presence)
    payload = exporter.run(since, anchors=anchors)

    if previous is not None and anchors:
        seen: set[tuple[str, str]] = set()
        merged_messages: list[dict[str, Any]] = []
        for message in previous.get("messages", []) + payload["messages"]:
            key = (message.get("sender", ""), message.get("text", ""))
            if key in seen:
                continue
            seen.add(key)
            merged_messages.append(message)
        payload["messages"] = merged_messages

        seen_cards: set[str] = set()
        merged_cards: list[dict[str, Any]] = []
        for card in previous.get("cards", []) + payload["cards"]:
            key = "".join(card.get("texts", []))[:200]
            if key in seen_cards:
                continue
            seen_cards.add(key)
            merged_cards.append(card)
        payload["cards"] = merged_cards

    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    document = render_document(target)
    monitor.stop()
    logger.info(
        "Exported %d messages / %d cards to %s (doc: %s)",
        len(payload["messages"]),
        len(payload["cards"]),
        target,
        document,
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chat", help="chat display name")
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    print(export_chat(args.chat, days=args.days))


if __name__ == "__main__":
    main()

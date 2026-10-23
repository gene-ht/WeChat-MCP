"""Reading messages from the currently open WeChat chat.

Message text comes from the Accessibility tree (``AXStaticText`` values).
Sender attribution still requires a screenshot heuristic, because WeChat does
not expose a stable "is this mine" attribute; when WeChat is not frontmost we
skip the screenshot and report ``UNKNOWN`` instead of misclassifying pixels
from whatever window is actually on screen.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ApplicationServices import (
    kAXChildrenAttribute,
    kAXListRole,
    kAXPositionAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
)
from PIL import Image, ImageChops, ImageGrab
from Quartz import CGPreflightScreenCaptureAccess

from . import ocr
from .ax import ax_get, axvalue_to_point, axvalue_to_size, dfs
from .logging_config import logger
from .models import ChatMessage, SenderLabel
from .pacing import pause
from .timeline import (
    TimelineRow,
    backfill_times,
    cut_after_since,
    merge_views,
    rows_from_messages,
)
from .ui import post_scroll
from .wechat_accessibility import (
    ensure_frontmost,
    get_list_center,
    get_wechat_ax_app,
    is_wechat_frontmost,
)

MESSAGES_LIST_TITLE = "Messages"

_SCREEN_CAPTURE_HINT_LOGGED = False


def has_screen_capture_permission() -> bool:
    """Return whether the host may capture screen content.

    Checked instead of calling ``CGRequestScreenCaptureAccess`` so the
    server never triggers a permission prompt on its own.
    """
    try:
        return bool(CGPreflightScreenCaptureAccess())
    except Exception:  # pragma: no cover - older macOS
        return False


def get_messages_list(ax_app: Any) -> Any:
    """Find the AX list that contains chat messages in the current window."""

    def is_message_list(el, role, title, identifier):
        return role == kAXListRole and (title or "") == MESSAGES_LIST_TITLE

    msg_list = dfs(ax_app, is_message_list)
    if msg_list is None:
        raise RuntimeError("Could not find WeChat 'Messages' list in AX tree")
    return msg_list


def capture_message_area(msg_list: Any):
    """Capture the visible message area together with its origin and size."""
    origin = axvalue_to_point(ax_get(msg_list, kAXPositionAttribute))
    size = axvalue_to_size(ax_get(msg_list, kAXSizeAttribute))
    if origin is None or size is None:
        raise RuntimeError("Failed to get bounds for WeChat messages list")

    x, y = origin
    w, h = size
    image = ImageGrab.grab(bbox=(int(x), int(y), int(x + w), int(y + h)))
    return image, origin, size


def scroll_to_bottom(msg_list: Any, center: tuple[float, float]) -> None:
    """Scroll the message list to the newest messages."""
    last_text: str | None = None
    stable = 0

    for _ in range(40):
        post_scroll(center, -1000)
        pause(0.3, minimum=0.15)

        texts = [str(text) for text in _child_texts(msg_list)]
        if not texts:
            continue

        new_last = texts[-1]
        if new_last == last_text:
            stable += 1
            if stable >= 3:
                break
        else:
            last_text = new_last
            stable = 0

    time.sleep(0.2)


def scroll_up_small(center: tuple[float, float]) -> None:
    """Scroll slightly towards older messages."""
    # Small steps keep consecutive screenfuls overlapping, which is what makes
    # the merge deterministic; a full-screen jump can skip rows entirely.
    post_scroll(center, 12)
    # Qt re-renders the virtual list asynchronously; give it time before the
    # next read, otherwise the same rows are read twice and scrolling looks
    # stalled.
    pause(0.85, minimum=0.5)


def _content_margins(crop: Image.Image) -> tuple[int, int] | None:
    """Return the left/right margins of non-background content in a row.

    WeChat draws both themes with a flat background, so comparing pixels
    against the row's most common colour works for light and dark themes
    alike. Measuring margins (not the midpoint) keeps wide bubbles from
    flipping sides: they are anchored by an avatar on one edge.
    """
    crop = crop.convert("RGB")
    if crop.width < 20 or crop.height < 5:
        return None

    # The message list draws its scrollbar over the right edge; that strip is
    # not part of the message and would otherwise pin the right margin to zero.
    gutter = min(28, crop.width // 10)
    if gutter:
        crop = crop.crop((0, 0, crop.width - gutter, crop.height))

    small = crop.resize((max(1, crop.width // 2), max(1, crop.height // 2)))
    colors = small.getcolors(maxcolors=1_000_000)
    if not colors:
        return None
    background = max(colors)[1]

    diff = ImageChops.difference(crop, Image.new("RGB", crop.size, background))
    mask = diff.convert("L").point(lambda value: 255 if value > 30 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return None

    x0, _, x1, _ = bbox
    return x0, crop.width - x1


def classify_sender_for_message(
    image, list_origin, message_pos, message_size
) -> SenderLabel:
    """Classify a row as ME / OTHER by which edge its content hugs."""
    if image is None:
        return "UNKNOWN"

    list_x, list_y = list_origin
    msg_x, msg_y = message_pos
    msg_w, msg_h = message_size

    left = int(msg_x - list_x)
    top = int(msg_y - list_y)
    right = left + int(msg_w)
    bottom = top + int(msg_h)

    if left < 0 or top < 0 or right > image.width or bottom > image.height:
        return "UNKNOWN"

    margins = _content_margins(image.crop((left, top, right, bottom)))
    if margins is None:
        return "UNKNOWN"

    left_margin, right_margin = margins
    if left_margin + 40 < right_margin:
        return "OTHER"
    if right_margin + 40 < left_margin:
        return "ME"
    return "UNKNOWN"


def _child_texts(msg_list: Any) -> list[str]:
    texts: list[str] = []
    for child in ax_get(msg_list, kAXChildrenAttribute) or []:
        text = ax_get(child, kAXValueAttribute) or ax_get(child, kAXTitleAttribute)
        if text:
            texts.append(str(text))
    return texts


def _sender_name_for_row(image, list_origin, pos, size) -> str | None:
    """OCR the small name label drawn above an incoming group bubble."""
    if image is None or not ocr.available():
        return None

    list_x, list_y = list_origin
    left = int(pos[0] - list_x)
    top = int(pos[1] - list_y)
    width = min(int(size[0]), image.width - max(left, 0))
    if left < 0 or top < 0 or width < 40:
        return None

    # The label sits ~6 px below the row's top edge, just above the bubble.
    band_top = top + 6
    band_bottom = min(band_top + 32, top + int(size[1]))
    band = image.crop((left, band_top, left + min(width, 340), band_bottom))
    if band.width < 20 or band.height < 8:
        return None
    band = band.resize((band.width * 2, band.height * 2))
    name = ocr.recognize_text(band)
    if not name:
        return None

    name = name.strip().strip('"“”')
    if not name or len(name) > 24:
        return None
    return name


def read_visible_messages(
    ax_app: Any | None = None,
    *,
    classify_senders: bool = True,
    sender_names: bool = False,
) -> list[ChatMessage]:
    """Read the messages currently rendered in the open chat."""
    ax_app = ax_app or get_wechat_ax_app(activate=False)
    msg_list = get_messages_list(ax_app)

    image = None
    list_origin = None
    if classify_senders and is_wechat_frontmost():
        global _SCREEN_CAPTURE_HINT_LOGGED
        if not has_screen_capture_permission():
            if not _SCREEN_CAPTURE_HINT_LOGGED:
                logger.warning(
                    "Screen Recording is not granted; sender attribution will "
                    "be UNKNOWN. Grant it in System Settings > Privacy & "
                    "Security > Screen & System Audio Recording to enable it."
                )
                _SCREEN_CAPTURE_HINT_LOGGED = True
        else:
            try:
                image, list_origin, _ = capture_message_area(msg_list)
            except Exception as exc:  # occlusion or capture failure
                logger.warning(
                    "Sender classification skipped (screenshot failed): %s", exc
                )

    visible: list[ChatMessage] = []
    for child in ax_get(msg_list, kAXChildrenAttribute) or []:
        text = ax_get(child, kAXValueAttribute) or ax_get(child, kAXTitleAttribute)
        if not text:
            continue

        sender: SenderLabel = "UNKNOWN"
        name: str | None = None
        if image is not None and list_origin is not None:
            point = axvalue_to_point(ax_get(child, kAXPositionAttribute))
            size = axvalue_to_size(ax_get(child, kAXSizeAttribute))
            if point is not None and size is not None:
                sender = classify_sender_for_message(image, list_origin, point, size)
                if sender_names and sender == "OTHER":
                    name = _sender_name_for_row(image, list_origin, point, size)

        visible.append(ChatMessage(sender=sender, text=str(text), sender_name=name))
    return visible


def read_last_message(*, classify_sender: bool = True) -> ChatMessage | None:
    """Return the newest visible message without scrolling or activating."""
    try:
        messages = read_visible_messages(classify_senders=classify_sender)
    except Exception as exc:
        logger.debug("Could not read last message: %s", exc)
        return None
    return messages[-1] if messages else None


def last_message_text() -> str | None:
    message = read_last_message(classify_sender=False)
    return message.text if message is not None else None


def fetch_recent_messages(
    last_n: int = 100,
    max_scrolls: int | None = None,
    *,
    activate: bool = True,
    sender_names: bool = False,
) -> list[ChatMessage]:
    """Fetch up to ``last_n`` messages from the currently open chat.

    Args:
        last_n: maximum number of messages to return.
        max_scrolls: optional hard cap on upward scroll steps.
        activate: bring WeChat to the front and scroll through history. When
            ``False`` and WeChat is not frontmost, only the visible messages
            are returned.
    """
    ax_app = get_wechat_ax_app(activate=activate)
    msg_list = get_messages_list(ax_app)

    if not activate and not is_wechat_frontmost():
        # Fast, focus-free path: read what is already on screen. Only steal
        # focus when the request cannot be satisfied without scrolling.
        visible = read_visible_messages(ax_app, classify_senders=False)
        if len(visible) >= last_n:
            logger.info(
                "WeChat not frontmost; returning %d visible messages without scrolling",
                len(visible),
            )
            pause(0.4)
            return visible[-last_n:] if last_n > 0 else []
        logger.info("Not enough visible messages; bringing WeChat forward to scroll")
        ensure_frontmost()
        ax_app = get_wechat_ax_app(activate=False)
        msg_list = get_messages_list(ax_app)

    ensure_frontmost()
    center = get_list_center(msg_list)
    scroll_to_bottom(msg_list, center)

    messages: list[ChatMessage] = []
    scrolls = 0
    no_new_counter = 0

    while True:
        visible = read_visible_messages(ax_app, sender_names=sender_names)
        if not visible:
            break

        if not messages:
            messages = visible
        else:
            # Align on the oldest already-known message using its text as anchor.
            anchor_text = messages[0].text
            idx: int | None = None
            for i, msg in enumerate(visible):
                if msg.text == anchor_text:
                    idx = i
                    break

            new_older = visible if idx is None else visible[:idx]
            if new_older:
                messages = new_older + messages
                no_new_counter = 0
            else:
                no_new_counter += 1
                if no_new_counter >= 5:
                    break

        if len(messages) >= last_n:
            break

        scroll_up_small(center)
        pause(0.3)
        scrolls += 1
        if max_scrolls is not None and scrolls >= max_scrolls:
            break

    if len(messages) > last_n:
        messages = messages[-last_n:]

    logger.info(
        "Fetched %d messages from current chat (requested last_n=%d)",
        len(messages),
        last_n,
    )
    # A human scans what they just read before acting on it.
    pause(0.8)
    return messages


def fetch_messages_since(
    since: datetime,
    *,
    max_messages: int = 500,
    max_scrolls: int = 150,
    sender_names: bool = False,
) -> tuple[list[TimelineRow], bool]:
    """Collect every message newer than ``since`` from the open chat.

    Scrolls upwards until a timestamp separator older than ``since`` is found.
    Returns ``(rows, complete)`` where ``complete`` is False when the top of the
    loaded history was reached before the boundary.
    """
    ax_app = get_wechat_ax_app(activate=True)
    msg_list = get_messages_list(ax_app)
    ensure_frontmost()

    center = get_list_center(msg_list)
    scroll_to_bottom(msg_list, center)

    collected: list[TimelineRow] = []
    last_size = 0
    stalled = 0
    for _ in range(max_scrolls):
        visible = read_visible_messages(ax_app, sender_names=sender_names)
        if not visible:
            break

        collected = merge_views(collected, rows_from_messages(visible))
        if len(collected) == last_size:
            stalled += 1
            if stalled >= 4:
                logger.info(
                    "History stopped growing at %d rows; assuming the top is reached",
                    len(collected),
                )
                break
        else:
            stalled = 0
            last_size = len(collected)

        cut = cut_after_since(collected, since)
        if cut is not None:
            logger.info(
                "Collected %d messages since %s (scrolled %d screens)",
                len(cut),
                since.date(),
                _ + 1,
            )
            return cut[-max_messages:], True

        scroll_up_small(center)
        pause(0.3)

    cut = cut_after_since(collected, since)
    if cut is not None:
        return cut[-max_messages:], True

    logger.warning(
        "Reached the top of the loaded history before finding a marker older "
        "than %s; returning what was collected.",
        since.date(),
    )
    messages_only = backfill_times(collected)
    return messages_only[-max_messages:], False

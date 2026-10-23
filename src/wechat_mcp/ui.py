"""Synthetic input events (keyboard, mouse, scroll, clipboard).

These APIs post events to the HID tap, so they act on whatever window is
frontmost. Callers must make WeChat frontmost first and re-check before
pressing keys; helpers here never steal focus themselves.
"""

from __future__ import annotations

import time
from typing import Any

import AppKit
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventCreateMouseEvent,
    CGEventCreateScrollWheelEvent,
    CGEventPost,
    CGEventSetFlags,
    CGEventSetIntegerValueField,
    CGEventSetLocation,
    CGPoint,
    CGWarpMouseCursorPosition,
    kCGEventFlagMaskCommand,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGHIDEventTap,
)

from .ax import element_center
from .logging_config import logger

KEYCODE_A = 0
KEYCODE_V = 9
KEYCODE_RETURN = 36


def send_key_with_modifiers(keycode: int, flags: int) -> None:
    event_down = CGEventCreateKeyboardEvent(None, keycode, True)
    CGEventSetFlags(event_down, flags)
    event_up = CGEventCreateKeyboardEvent(None, keycode, False)
    CGEventSetFlags(event_up, flags)
    CGEventPost(kCGHIDEventTap, event_down)
    CGEventPost(kCGHIDEventTap, event_up)


def press_return() -> None:
    event_down = CGEventCreateKeyboardEvent(None, KEYCODE_RETURN, True)
    CGEventSetFlags(event_down, 0)
    event_up = CGEventCreateKeyboardEvent(None, KEYCODE_RETURN, False)
    CGEventSetFlags(event_up, 0)
    CGEventPost(kCGHIDEventTap, event_down)
    CGEventPost(kCGHIDEventTap, event_up)


def press_select_all() -> None:
    send_key_with_modifiers(KEYCODE_A, kCGEventFlagMaskCommand)


def press_paste() -> None:
    send_key_with_modifiers(KEYCODE_V, kCGEventFlagMaskCommand)


def set_clipboard_text(text: str) -> None:
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)


def click_element_center(element: Any) -> None:
    """Click the visual centre of an AX element (requires it to be visible)."""
    center = element_center(element)
    if center is None:
        raise RuntimeError("Failed to get bounds for element to click")
    click_at(center)


def click_at(point: tuple[float, float]) -> None:
    event_down = CGEventCreateMouseEvent(
        None, kCGEventLeftMouseDown, CGPoint(*point), 0
    )
    event_up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, CGPoint(*point), 0)
    CGEventPost(kCGHIDEventTap, event_down)
    CGEventPost(kCGHIDEventTap, event_up)


def long_press_element_center(element: Any, hold_seconds: float = 2.2) -> None:
    """Long-press the visual centre of an AX element."""
    center = element_center(element)
    if center is None:
        raise RuntimeError("Failed to get bounds for element to long-press")

    event_down = CGEventCreateMouseEvent(
        None, kCGEventLeftMouseDown, CGPoint(*center), 0
    )
    CGEventPost(kCGHIDEventTap, event_down)
    try:
        time.sleep(max(0.0, hold_seconds))
    finally:
        event_up = CGEventCreateMouseEvent(
            None, kCGEventLeftMouseUp, CGPoint(*center), 0
        )
        CGEventPost(kCGHIDEventTap, event_up)


def post_scroll(center: tuple[float, float], delta_lines: int) -> None:
    """Post a scroll-wheel event at a screen position.

    On a standard macOS configuration:
    - positive ``delta_lines`` scrolls towards older content (upwards);
    - negative ``delta_lines`` scrolls towards newer content (downwards).
    """
    cx, cy = center
    # Qt routes wheel events to the widget the pointer is over, not to the
    # coordinates attached to the event. Park the cursor on the list first.
    CGWarpMouseCursorPosition(CGPoint(cx, cy))
    time.sleep(0.02)

    # Discrete line-based ticks are frequently ignored by WeChat's Qt list.
    # A short burst of pixel-based, "continuous" events behaves like a real
    # trackpad gesture and is accepted reliably.
    total_pixels = int(delta_lines * 13)
    direction = 1 if total_pixels >= 0 else -1
    steps = max(1, min(40, abs(total_pixels) // 40))
    step = direction * 40
    for _ in range(steps):
        event = CGEventCreateScrollWheelEvent(
            None, 0, 1, step  # kCGScrollEventUnitPixel = 0
        )
        CGEventSetLocation(event, CGPoint(cx, cy))
        # kCGScrollWheelEventIsContinuous = 88 (not exported by pyobjc)
        CGEventSetIntegerValueField(event, 88, 1)
        CGEventPost(kCGHIDEventTap, event)
        time.sleep(0.012)


def type_via_clipboard(text: str) -> None:
    """Paste ``text`` through the clipboard (IME-safe for CJK input)."""
    set_clipboard_text(text)
    time.sleep(0.1)
    press_select_all()
    time.sleep(0.05)
    press_paste()
    logger.debug("Pasted %d characters via clipboard", len(text))

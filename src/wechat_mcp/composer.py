"""Composing and sending a reply in the currently open WeChat chat.

Safety rules enforced here:

1. Never type into a chat the caller did not open (the caller resolves the
   target through :mod:`wechat_accessibility` first).
2. Never clobber an existing draft: if the composer already holds text that is
   not ours, abort with :class:`InputNotEmpty`.
3. Never post a bare Return while another app is frontmost: the focus is
   re-checked immediately before the key event.
4. Confirm the outcome by reading the conversation back, instead of assuming a
   keystroke worked.
"""

from __future__ import annotations

import time
from typing import Any

from ApplicationServices import (
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    kAXButtonRole,
    kAXChildrenAttribute,
    kAXParentAttribute,
    kAXPressAction,
    kAXRaiseAction,
    kAXRoleAttribute,
    kAXTextAreaRole,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXWindowRole,
)

from .ax import ax_get, dfs
from .errors import InputFieldNotFound, InputNotEmpty, WeChatMCPError
from .logging_config import logger
from .pacing import pause, thinking_pause, typing_pause
from .ui import press_return
from .wechat_accessibility import (
    ensure_frontmost,
    get_wechat_ax_app,
    is_wechat_frontmost,
)

INPUT_FIELD_IDENTIFIER = "chat_input_field"
SEND_BUTTON_TITLES = ("Send", "发送")


def find_input_field(ax_app: Any):
    """Locate the chat composer text area."""

    def is_input(el, role, title, identifier):
        return role == kAXTextAreaRole and identifier == INPUT_FIELD_IDENTIFIER

    input_field = dfs(ax_app, is_input)
    if input_field is None:
        raise InputFieldNotFound(
            "Could not find the WeChat chat input field via Accessibility API"
        )
    return input_field


def read_input_value(input_field: Any | None = None) -> str:
    """Return the current composer text, or an empty string if unreadable."""
    if input_field is None:
        input_field = find_input_field(get_wechat_ax_app(activate=False))
    value = ax_get(input_field, kAXValueAttribute)
    return value if isinstance(value, str) else ""


def _write_input(input_field: Any, text: str) -> None:
    err = AXUIElementSetAttributeValue(input_field, kAXValueAttribute, text)
    if err != 0:
        raise WeChatMCPError(f"Failed to set composer text (AX error {err})")


def set_input_value(text: str, *, replace_existing: bool = False) -> str:
    """Write ``text`` into the composer and verify it landed.

    Returns the value read back from AX. Raises :class:`InputNotEmpty` when a
    different draft is already present and ``replace_existing`` is False.
    """
    input_field = find_input_field(get_wechat_ax_app(activate=False))
    current = read_input_value(input_field)
    normalized_current = current.strip()
    normalized_target = text.strip()

    if normalized_current and normalized_current != normalized_target:
        if not replace_existing:
            raise InputNotEmpty(
                "The WeChat composer already contains different text; refusing "
                "to overwrite a draft that may belong to the human.",
                current_preview=normalized_current[:80],
            )
        logger.info("Replacing existing composer draft (%d chars)", len(current))

    if current != text:
        _write_input(input_field, text)
        pause(0.4, minimum=0.2)

    readback = read_input_value(input_field)
    if readback.strip() != normalized_target:
        # Fall back to clipboard paste for clients that reject AX writes.
        logger.info("AX write did not stick; falling back to clipboard paste")
        AXUIElementPerformAction(input_field, kAXRaiseAction)
        from .ui import type_via_clipboard

        type_via_clipboard(text)
        time.sleep(0.15)
        readback = read_input_value(input_field)

    typing_pause(text)
    if readback.strip() != normalized_target:
        raise WeChatMCPError(
            "Composer text did not match the requested reply after writing it.",
            expected_len=len(normalized_target),
            actual_len=len(readback),
        )
    return readback


def _enclosing_window(element: Any) -> Any | None:
    """Walk up the AX parent chain until an ``AXWindow`` is reached."""
    current = element
    for _ in range(20):
        parent = ax_get(current, kAXParentAttribute)
        if parent is None:
            return None
        role = ax_get(parent, kAXRoleAttribute)
        if role == kAXWindowRole:
            return parent
        current = parent
    return None


def find_send_button(ax_app: Any, input_field: Any | None = None):
    """Return the chat send button if it can be attributed to this chat window.

    Only buttons that live in the same window as the composer are considered;
    otherwise a "Send" button from another sheet could be pressed by mistake.
    """
    candidates: list[Any] = []
    stack = [ax_app]
    while stack:
        element = stack.pop()
        if ax_get(element, kAXRoleAttribute) == kAXButtonRole:
            title = ax_get(element, kAXTitleAttribute)
            if isinstance(title, str) and title in SEND_BUTTON_TITLES:
                candidates.append(element)
        stack.extend(ax_get(element, kAXChildrenAttribute) or [])
        if len(candidates) > 5:
            break

    if not candidates:
        return None

    if input_field is not None:
        window = _enclosing_window(input_field)
        if window is not None:
            for candidate in candidates:
                if _enclosing_window(candidate) is window:
                    return candidate
            return None

    return candidates[0]


def _verify_sent(text: str, *, timeout: float) -> bool:
    """Poll the conversation until the reply appears as the newest message."""
    from .messages import last_message_text

    deadline = time.time() + timeout
    while time.time() < deadline:
        latest = last_message_text()
        if latest is not None and latest.strip() == text.strip():
            return True
        time.sleep(0.25)
    return False


def send_message(
    text: str,
    *,
    verify: bool = True,
    replace_existing: bool = False,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Send ``text`` in the currently open chat.

    Returns a dict with ``sent``, ``verified`` and ``method``. ``sent`` means
    the UI action was performed; ``verified`` means the reply was read back
    from the conversation.
    """
    ensure_frontmost()
    ax_app = get_wechat_ax_app(activate=False)

    input_field = find_input_field(ax_app)
    set_input_value(text, replace_existing=replace_existing)

    # A human reads what they wrote before sending; keep a visible beat.
    thinking_pause()

    # Re-check focus immediately before the keystroke: activation can race with
    # the human switching windows.
    ensure_frontmost()
    pause(0.5, minimum=0.2)

    method = "axpress"
    button = find_send_button(ax_app, input_field)
    if button is not None:
        AXUIElementPerformAction(button, kAXPressAction)
    else:
        method = "return"
        if not is_wechat_frontmost():
            raise WeChatMCPError(
                "Refusing to press Return: WeChat is not frontmost and the "
                "keystroke would land in another application."
            )
        AXUIElementPerformAction(input_field, kAXRaiseAction)
        press_return()

    logger.info("Sent message (%d chars) via %s", len(text), method)

    verified = _verify_sent(text, timeout=timeout) if verify else False
    if verify and not verified:
        logger.warning(
            "Could not verify that the reply appears in the conversation; "
            "the message may still have been sent."
        )

    return {
        "sent": True,
        "verified": verified,
        "method": method,
        "text_length": len(text),
    }

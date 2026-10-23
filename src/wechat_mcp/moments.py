from __future__ import annotations

import time
from typing import Any

from ApplicationServices import (
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    kAXButtonRole,
    kAXRaiseAction,
    kAXSheetRole,
    kAXTextAreaRole,
    kAXValueAttribute,
)

from .logging_config import logger
from .ui import long_press_element_center
from .wechat_accessibility import (
    _find_window_by_title,
    _wait_for_window,
    click_element_center,
    dfs,
    get_wechat_ax_app,
)


def _open_moments_window(ax_app: Any, timeout: float = 5.0) -> Any:
    """
    Ensure the WeChat Moments window is open by clicking the Moments
    button in the main WeChat window and waiting for the Moments window
    to appear.
    """
    main_window = _find_window_by_title(ax_app, "WeChat")
    if main_window is None:
        raise RuntimeError("Could not find main WeChat window with title 'WeChat'")

    def is_moments_button(el, role, title, identifier):
        return role == kAXButtonRole and isinstance(title, str) and title == "Moments"

    button = dfs(main_window, is_moments_button)
    if button is None:
        raise RuntimeError("Could not find 'Moments' button in WeChat main window")

    logger.info("Clicking 'Moments' button in main window")
    click_element_center(button)
    time.sleep(0.4)

    moments_window = _wait_for_window(ax_app, "Moments", timeout=timeout)
    if moments_window is None:
        raise RuntimeError("The 'Moments' window did not appear after clicking")

    return moments_window


def _open_moment_composer(moments_window: Any) -> None:
    """
    Open the Moments composer sheet by long-pressing the Post button in
    the Moments window.
    """

    def is_post_button(el, role, title, identifier):
        return role == kAXButtonRole and isinstance(title, str) and title == "Post"

    button = dfs(moments_window, is_post_button)
    if button is None:
        raise RuntimeError("Could not find 'Post' button in Moments window")

    logger.info("Long-pressing 'Post' button to open composer sheet")
    long_press_element_center(button, hold_seconds=1.4)
    time.sleep(0.3)


def _find_moments_sheet(moments_window: Any, timeout: float = 5.0) -> Any | None:
    """
    Wait for the Moments composer sheet to appear inside the Moments
    window, returning the sheet element or None if the timeout expires.
    """

    def is_sheet(el, role, title, identifier):
        return role == kAXSheetRole

    end = time.time() + timeout
    while time.time() < end:
        sheet = dfs(moments_window, is_sheet)
        if sheet is not None:
            logger.info("Found Moments composer sheet")
            return sheet
        time.sleep(0.1)

    logger.warning("Timed out waiting for Moments composer sheet")
    return None


def _find_editor_root(moments_window: Any, timeout: float = 5.0) -> Any | None:
    """
    Return the root element that contains the Moments composer controls.

    Prefer the dedicated AXSheet element; fall back to the Moments
    window itself if the sheet cannot be located.
    """
    sheet = _find_moments_sheet(moments_window, timeout=timeout)
    if sheet is not None:
        return sheet

    logger.warning(
        "Falling back to using the Moments window as editor root "
        "because composer sheet was not found"
    )
    return moments_window


def _find_moment_text_area(root: Any) -> Any | None:
    """
    Locate the text entry area used to compose a Moments post.
    """

    def is_text_area(el, role, title, identifier):
        return role == kAXTextAreaRole

    return dfs(root, is_text_area)


def _find_post_button_in_editor(root: Any) -> Any | None:
    """
    Locate the Post button within the composer editor root (sheet or
    Moments window).
    """

    def is_post_button(el, role, title, identifier):
        return role == kAXButtonRole and isinstance(title, str) and title == "Post"

    return dfs(root, is_post_button)


def publish_moment_without_media(content: str, publish: bool = True) -> dict[str, Any]:
    """
    Publish a Moments post containing only text (no media).

    High-level flow:
    - Click the "Moments" button in the main WeChat window to open the
      Moments window.
    - Long-press the "Post" button in the Moments window to reveal the
      composer sheet.
    - In the composer sheet, set the text entry area's value to the
      provided content.
    - If `publish` is True (default), click the "Post" button in the
      sheet to publish the moment; if False, leave the composer open
      without sending, so that the user can modify the draft in the
      composer.
    """
    if not isinstance(content, str) or not content.strip():
        return {
            "error": "content must be a non-empty string",
            "content": content,
            "stage": "validate_input",
        }

    logger.info(
        "Starting publish_moment_without_media (content_length=%d, publish=%s)",
        len(content),
        publish,
    )

    try:
        ax_app = get_wechat_ax_app()
        moments_window = _open_moments_window(ax_app)
        _open_moment_composer(moments_window)

        editor_root = _find_editor_root(moments_window, timeout=5.0)
        if editor_root is None:
            error_msg = "Could not locate Moments composer editor root"
            logger.warning(error_msg)
            return {
                "error": error_msg,
                "content": content,
                "stage": "editor_root",
            }

        text_area = _find_moment_text_area(editor_root)
        if text_area is None:
            error_msg = "Could not find text entry area in Moments composer"
            logger.warning(error_msg)
            return {
                "error": error_msg,
                "content": content,
                "stage": "text_area",
            }

        AXUIElementPerformAction(text_area, kAXRaiseAction)
        err = AXUIElementSetAttributeValue(text_area, kAXValueAttribute, content)
        if err != 0:
            error_msg = f"Failed to set composer text, AX error {err}"
            logger.warning(error_msg)
            return {
                "error": error_msg,
                "content": content,
                "stage": "set_text",
            }

        if not publish:
            logger.info(
                "Moments composer text updated; publish=False so skipping Post click"
            )
            return {
                "content": content,
                "posted": False,
            }

        logger.info("Moments composer text updated; clicking Post in sheet")
        post_button = _find_post_button_in_editor(editor_root)
        if post_button is None:
            error_msg = "Could not find 'Post' button in Moments composer"
            logger.warning(error_msg)
            return {
                "error": error_msg,
                "content": content,
                "stage": "post_button",
            }

        click_element_center(post_button)
        time.sleep(0.5)

        logger.info("Moments post submitted successfully")
        return {
            "content": content,
            "posted": True,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error while publishing moment without media: %s", exc)
        return {
            "error": str(exc),
            "content": content,
            "stage": "unexpected_error",
        }

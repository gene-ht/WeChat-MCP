"""WeChat for macOS: process discovery, windows, chat navigation and search.

This module is the only place that knows WeChat's Accessibility structure
(session list identifiers, search sections, window titles). Low-level AX
primitives live in :mod:`wechat_mcp.ax`; synthetic input lives in
:mod:`wechat_mcp.ui`.

Focus policy
------------
Reading attributes does not require WeChat to be frontmost, so read-only
helpers accept ``activate=False``. Anything that clicks, scrolls or types is
posted to the HID tap and therefore *does* require WeChat to be frontmost;
those paths call :func:`ensure_frontmost` explicitly and fail loudly instead of
acting on whatever window happens to be on top.
"""

from __future__ import annotations

import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import AppKit
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    kAXChildrenAttribute,
    kAXIdentifierAttribute,
    kAXListRole,
    kAXPositionAttribute,
    kAXRaiseAction,
    kAXRoleAttribute,
    kAXStaticTextRole,
    kAXTextAreaRole,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXWindowRole,
    kAXWindowsAttribute,
)

from .ax import (
    ax_get,
    axvalue_to_point,
    dfs,
    element_center,
    walk,
)
from .errors import (
    ChatOpenFailed,
    WeChatMCPError,
    WeChatNotRunning,
    WeChatUIUnavailable,
)
from .logging_config import logger
from .pacing import pause, wait_until
from .ui import (
    click_element_center,
    post_scroll,
    press_paste,
    press_select_all,
)

WECHAT_BUNDLE_ID = "com.tencent.xinWeChat"

# Re-exported for callers that used to import these from this module.
__all__ = [
    "WECHAT_BUNDLE_ID",
    "SearchEntry",
    "ax_get",
    "axvalue_to_point",
    "click_element_center",
    "collect_chat_elements",
    "dfs",
    "ensure_frontmost",
    "find_chat_element_by_name",
    "find_search_field",
    "focus_and_type_search",
    "get_current_chat_name",
    "get_list_center",
    "get_search_list",
    "get_wechat_ax_app",
    "is_wechat_frontmost",
    "is_wechat_running",
    "open_chat_for_contact",
    "post_scroll",
]


# --------------------------------------------------------------------- app
def _running_wechat_app() -> Any | None:
    apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
        WECHAT_BUNDLE_ID
    )
    return apps[0] if apps else None


def is_wechat_running() -> bool:
    return _running_wechat_app() is not None


def is_wechat_frontmost() -> bool:
    """Return ``True`` if WeChat is the active application."""
    frontmost = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if frontmost is None:
        return False
    return frontmost.bundleIdentifier() == WECHAT_BUNDLE_ID


def _activate_via_applescript(timeout: float = 3.0) -> bool:
    """Activate WeChat through AppleScript.

    macOS throttles ``NSRunningApplication.activate`` for background callers
    (measured: no focus change even after 30 s). Sending an Apple Event to the
    app itself is not throttled, so this is the reliable fallback.
    """
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application id "{WECHAT_BUNDLE_ID}" to activate',
            ],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("AppleScript activation failed: %s", exc)
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_wechat_frontmost():
            return True
        time.sleep(0.15)
    return False


def frontmost_app() -> Any | None:
    """Return the currently active application, if any."""
    return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()


@contextmanager
def preserve_focus(enabled: bool = True):
    """Restore the app that was active before a tool call.

    A tool call may need WeChat in front to click or type. Without this guard
    the user is left staring at WeChat after every call; with it, focus returns
    to wherever they were. Restoring is skipped when WeChat was already active.
    """
    previous = frontmost_app() if enabled else None
    try:
        yield
    finally:
        should_restore = (
            previous is not None
            and previous.bundleIdentifier() != WECHAT_BUNDLE_ID
            and is_wechat_frontmost()
        )
        if should_restore:
            try:
                previous.activateWithOptions_(
                    AppKit.NSApplicationActivateIgnoringOtherApps
                )
                time.sleep(0.15)
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("Could not restore focus to %s: %s", previous, exc)


def ensure_frontmost(timeout: float = 8.0) -> None:
    """Activate WeChat and wait until it owns the keyboard focus.

    Qt-based WeChat 4.x can take several seconds to come forward, and macOS
    throttles repeated activation requests, so activation is retried instead of
    being issued once. ``open -b`` (LaunchServices) is used as a fallback when
    the first two seconds pass without focus.

    Raises:
        WeChatNotRunning: WeChat is not running.
        ChatOpenFailed: WeChat did not become frontmost within ``timeout``.
    """
    app = _running_wechat_app()
    if app is None:
        raise WeChatNotRunning("WeChat is not running")

    if is_wechat_frontmost():
        return

    started = time.time()
    options = (
        AppKit.NSApplicationActivateAllWindows
        | AppKit.NSApplicationActivateIgnoringOtherApps
    )
    used_applescript = False

    while time.time() - started < timeout:
        if not used_applescript:
            app.activateWithOptions_(options)
        else:
            time.sleep(0.2)

        for _ in range(6):
            time.sleep(0.1)
            if is_wechat_frontmost():
                logger.debug(
                    "WeChat became frontmost after %.2fs", time.time() - started
                )
                return

        if not used_applescript and time.time() - started >= 1.2:
            used_applescript = True
            logger.info("Direct activation did not take; using AppleScript")
            if _activate_via_applescript(timeout=max(1.0, timeout - 2.0)):
                return

    raise ChatOpenFailed(
        "WeChat did not become the frontmost application; refusing to send "
        "synthetic input that could land in another window.",
        waited_seconds=round(time.time() - started, 1),
    )


def get_wechat_ax_app(*, activate: bool = True) -> Any:
    """Return the AX root element for WeChat.

    Args:
        activate: bring WeChat to the foreground first. Keep ``True`` for any
            path that clicks, scrolls or types; use ``False`` for read-only
            inspection so the agent does not steal focus.
    """
    app = _running_wechat_app()
    if app is None:
        raise WeChatNotRunning("WeChat is not running")

    if activate:
        was_frontmost = is_wechat_frontmost()
        ensure_frontmost()
        if not was_frontmost:
            # Qt needs a moment to build its AX tree after coming forward.
            pause(1.0, minimum=0.4)

    pid = app.processIdentifier()
    logger.debug("Using WeChat AX app (pid=%s, activate=%s)", pid, activate)
    return AXUIElementCreateApplication(pid)


# ------------------------------------------------------------- diagnostics
AX_TREE_MIN_ELEMENTS = 15


def wechat_app_version() -> str | None:
    """Return WeChat's bundle version, e.g. ``"4.1.4"``."""
    app = _running_wechat_app()
    if app is None:
        return None
    bundle = AppKit.NSBundle.bundleWithURL_(app.bundleURL())
    if bundle is None:
        return None
    version = bundle.infoDictionary().get("CFBundleShortVersionString")
    return str(version) if version else None


def ax_tree_stats(ax_app: Any | None = None) -> dict[str, Any]:
    """Count the AX elements WeChat currently exposes.

    WeChat 4.1.x on recent macOS releases sometimes exposes only the window
    frame (three traffic-light buttons) and no chat UI. Detecting that early
    produces a clear error instead of "could not find the search field".
    """
    ax_app = ax_app or get_wechat_ax_app(activate=False)
    windows = ax_get(ax_app, kAXWindowsAttribute) or []

    count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal count
        if node is None or count > 250 or depth > 6:
            return
        count += 1
        for child in ax_get(node, kAXChildrenAttribute) or []:
            visit(child, depth + 1)

    for window in windows:
        visit(window, 0)

    return {
        "window_count": len(windows),
        "element_count": count,
        "ok": count >= AX_TREE_MIN_ELEMENTS,
        "version": wechat_app_version(),
    }


def ensure_ax_tree_available(ax_app: Any | None = None) -> dict[str, Any]:
    """Raise :class:`WeChatUIUnavailable` when WeChat exposes no chat UI."""
    stats = ax_tree_stats(ax_app)
    if not stats["ok"]:
        raise WeChatUIUnavailable(
            "WeChat is running but only exposes its window frame through the "
            "Accessibility API (no sidebar, message list or input field). "
            "WeChat 4.1.x on recent macOS releases is known to break AX-based "
            "automation; use WeChat 3.8.x, or drive the client with the "
            "keyboard/OCR fallback until a compatible client is available.",
            element_count=stats["element_count"],
            window_count=stats["window_count"],
            wechat_version=stats["version"],
        )
    return stats


# ------------------------------------------------------------------ windows
def _find_window_by_title(ax_app: Any, title: str):
    """Locate a top-level WeChat window with the given title."""

    def is_window(el, role, current_title, identifier):
        return (
            role == kAXWindowRole
            and isinstance(current_title, str)
            and current_title == title
        )

    return dfs(ax_app, is_window)


def _wait_for_window(ax_app: Any, title: str, timeout: float = 5.0):
    """Wait until a window with ``title`` appears, returning it or ``None``."""
    end = time.time() + timeout
    while time.time() < end:
        window = _find_window_by_title(ax_app, title)
        if window is not None:
            logger.info("Found window %r", title)
            return window
        time.sleep(0.1)
    logger.warning("Timed out waiting for window %r", title)
    return None


# ------------------------------------------------------------------- chats
def _normalize_chat_title(name: str) -> str:
    """Strip the trailing ``"(<member count>)"`` suffix WeChat adds to groups."""
    name = name.strip()
    name = re.sub(r"\(\d+\)$", "", name).strip()
    return name


def get_current_chat_name() -> str | None:
    """Return the display name of the currently open chat, if available."""
    ax_app = get_wechat_ax_app(activate=False)

    def is_chat_title(el, role, title, identifier):
        return role == kAXStaticTextRole and identifier == "big_title_line_h_view"

    title_el = dfs(ax_app, is_chat_title)
    if title_el is None:
        logger.warning("Could not locate current chat title element via AX")
        return None

    value = ax_get(title_el, kAXValueAttribute)
    if isinstance(value, str) and value.strip():
        return _normalize_chat_title(value)

    title = ax_get(title_el, kAXTitleAttribute)
    if isinstance(title, str) and title.strip():
        return _normalize_chat_title(title)

    return None


def collect_chat_elements(ax_app) -> dict[str, Any]:
    """Collect chat elements from the left session list keyed by display name."""
    results: dict[str, Any] = {}

    def visit(element):
        role = ax_get(element, kAXRoleAttribute)
        identifier = ax_get(element, kAXIdentifierAttribute)
        if (
            isinstance(role, str)
            and role == kAXStaticTextRole
            and isinstance(identifier, str)
            and identifier.startswith("session_item_")
        ):
            chat_name = identifier[len("session_item_") :]
            if chat_name:
                results[chat_name] = element

    walk(ax_app, visit, max_depth=12)
    logger.debug("Collected %d chat elements from session list", len(results))
    return results


def list_chat_names() -> list[str]:
    """Return the chat names currently visible in the session list."""
    ax_app = get_wechat_ax_app(activate=False)
    return sorted(collect_chat_elements(ax_app))


def find_chat_element_by_name(ax_app, chat_name: str):
    """Find a session-list element by exact name (case-insensitive fallback)."""
    chat_elements = collect_chat_elements(ax_app)
    if chat_name in chat_elements:
        return chat_elements[chat_name]

    lowered = {name.lower(): el for name, el in chat_elements.items()}
    return lowered.get(chat_name.lower())


# ------------------------------------------------------------------ search
def find_search_field(ax_app):
    def is_search(el, role, title, identifier):
        return role == kAXTextAreaRole and title == "Search"

    search = dfs(ax_app, is_search)
    if search is None:
        raise RuntimeError(
            "Could not find WeChat search text field via Accessibility API"
        )
    return search


def focus_and_type_search(ax_app, text: str) -> None:
    """Focus the sidebar search field and replace its contents with ``text``."""
    search = find_search_field(ax_app)
    AXUIElementPerformAction(search, kAXRaiseAction)
    pause(0.4, minimum=0.2)

    # Clear any existing value via AX (best effort).
    err = AXUIElementSetAttributeValue(search, kAXValueAttribute, "")
    if err != 0:
        logger.debug("Failed to clear search field via AX (err=%s)", err)

    # Clipboard paste is IME-safe for Chinese input.
    from .ui import set_clipboard_text

    set_clipboard_text(text)
    pause(0.3, minimum=0.15)
    press_select_all()
    pause(0.2, minimum=0.1)
    press_paste()
    pause(0.5, minimum=0.25)


def _wait_for_chat_title(chat_name: str, timeout: float = 3.0) -> bool:
    """Wait until ``chat_name`` is the active conversation."""

    def is_target() -> bool:
        current = get_current_chat_name()
        return bool(current) and current.strip().casefold() == chat_name.strip().casefold()

    matched = wait_until(is_target, timeout=timeout, interval=0.3)
    if not matched:
        logger.warning("Chat %r did not become the active conversation in time", chat_name)
    return matched


def open_chat_for_contact(chat_name: str) -> dict[str, Any] | None:
    """Open a chat for a given name (contact or group).

    First, search the left sidebar session list. If found, click it. If not,
    type the name into the global search field and inspect the results:

    - Prefer an exact match under "Contacts".
    - Otherwise, prefer an exact match under "Group Chats".
    - If no exact match is visible, expand "View All" for Contacts and Group
      Chats (if present) and scroll through results, ignoring "Chat History",
      "Official Accounts", "Internet search results" and "More".

    Returns ``None`` on success, or a dict with ``error`` and ``candidates``
    when no exact match exists. Callers may surface the candidates to the LLM.
    """
    logger.info("Opening chat for name: %s", chat_name)
    ax_app = get_wechat_ax_app(activate=True)
    ensure_ax_tree_available(ax_app)

    # The session list is virtualised and can still be empty right after the
    # window comes forward; give the client a chance to render it.
    if not wait_until(lambda: bool(collect_chat_elements(ax_app)), timeout=4.0):
        logger.warning("Session list is still empty after waiting; falling back to search")

    element = find_chat_element_by_name(ax_app, chat_name)
    if element is not None:
        logger.info("Found chat in session list, clicking center")
        click_element_center(element)
        pause(1.0, minimum=0.4)
        _wait_for_chat_title(chat_name)
        return None

    logger.info("Chat not in session list, using global search")
    focus_and_type_search(ax_app, chat_name)
    pause(0.8, minimum=0.4)

    found, candidates = _select_contact_from_search_results(ax_app, chat_name)
    if found:
        logger.info("Opened chat for %s via search results", chat_name)
        pause(1.0, minimum=0.4)
        _wait_for_chat_title(chat_name)
        return None

    logger.info(
        "Exact match for %s not found in Contacts/Group Chats search results; "
        "returning candidate names",
        chat_name,
    )
    error_msg = (
        "Could not find an exact match for the requested chat name in "
        "WeChat's Contacts or Group Chats search results. Returning related "
        "contact and group names so the LLM can choose a more specific chat "
        "to open."
    )
    return {"error": error_msg, "chat_name": chat_name, "candidates": candidates}


def get_search_list(ax_app):
    """Return the AX list containing global search results (id: 'search_list')."""

    def is_search_list(el, role, title, identifier):
        return role == kAXListRole and identifier == "search_list"

    search_list = dfs(ax_app, is_search_list)
    if search_list is None:
        raise WeChatMCPError(
            "Could not find WeChat search results list via Accessibility API"
        )
    return search_list


@dataclass
class SearchEntry:
    element: Any
    text: str
    y: float


SEARCH_SECTIONS = (
    "Contacts",
    "Group Chats",
    "Chat History",
    "Official Accounts",
    "Internet search results",
    "More",
)


def _collect_search_entries(search_list) -> list[SearchEntry]:
    """Collect visible static-text entries from the search results list."""
    entries: list[SearchEntry] = []

    def walk(el):
        role = ax_get(el, kAXRoleAttribute)
        if role == kAXStaticTextRole:
            title = ax_get(el, kAXTitleAttribute)
            value = ax_get(el, kAXValueAttribute)
            text_obj = title if isinstance(title, str) and title else value
            if isinstance(text_obj, str):
                point = axvalue_to_point(ax_get(el, kAXPositionAttribute))
                y = point[1] if point is not None else 0.0
                entries.append(
                    SearchEntry(element=el, text=text_obj.strip(), y=float(y))
                )

        for child in ax_get(el, kAXChildrenAttribute) or []:
            walk(child)

    walk(search_list)
    entries.sort(key=lambda entry: entry.y)
    return entries


def _build_section_headers(entries: list[SearchEntry]) -> dict[str, float]:
    headers: dict[str, float] = {}
    for entry in entries:
        if entry.text in SEARCH_SECTIONS:
            headers[entry.text] = entry.y
    return headers


def _classify_section(entry: SearchEntry, headers: dict[str, float]) -> str | None:
    """Return the last section header above ``entry``, if any."""
    section: str | None = None
    best_y = float("-inf")
    for title, header_y in headers.items():
        if header_y <= entry.y and header_y > best_y:
            section = title
            best_y = header_y
    return section


def _find_exact_match_in_entries(entries: list[SearchEntry], contact_name: str):
    """Look for an exact match, preferring Contacts over Group Chats."""
    target = contact_name.strip()
    headers = _build_section_headers(entries)

    contact_element = None
    group_element = None

    for entry in entries:
        if entry.text != target:
            continue
        section = _classify_section(entry, headers)
        if section == "Contacts" and contact_element is None:
            contact_element = entry.element
        elif section == "Group Chats" and group_element is None:
            group_element = entry.element

    return contact_element if contact_element is not None else group_element


def _summarize_search_candidates(entries: list[SearchEntry]) -> dict[str, list[str]]:
    """Summarize up to 15 candidate names from Contacts and Group Chats."""
    headers = _build_section_headers(entries)
    contacts: list[str] = []
    group_chats: list[str] = []

    for entry in entries:
        if entry.text in SEARCH_SECTIONS:
            continue
        section = _classify_section(entry, headers)
        if section == "Contacts" and entry.text not in contacts:
            contacts.append(entry.text)
        elif section == "Group Chats" and entry.text not in group_chats:
            group_chats.append(entry.text)

    return {"contacts": contacts[:15], "group_chats": group_chats[:15]}


def _expand_section_if_needed(search_list, section_title: str) -> None:
    """Click a "View All(...)" row for ``section_title`` if present."""
    entries = _collect_search_entries(search_list)
    headers = _build_section_headers(entries)
    if section_title not in headers:
        return

    for entry in entries:
        if not entry.text.startswith("View All"):
            continue
        if _classify_section(entry, headers) == section_title:
            logger.info("Expanding %s section via %r", section_title, entry.text)
            click_element_center(entry.element)
            time.sleep(0.3)
            return


def _select_contact_from_search_results(
    ax_app, contact_name: str
) -> tuple[bool, dict[str, list[str]]]:
    """Open an exact match from search results, ignoring unsafe sections."""
    search_list = get_search_list(ax_app)

    aggregated_contacts: set[str] = set()
    aggregated_groups: set[str] = set()

    def update_candidates(entries: list[SearchEntry]) -> None:
        partial = _summarize_search_candidates(entries)
        aggregated_contacts.update(partial["contacts"])
        aggregated_groups.update(partial["group_chats"])

    def current_candidates() -> dict[str, list[str]]:
        return {
            "contacts": list(aggregated_contacts)[:15],
            "group_chats": list(aggregated_groups)[:15],
        }

    # Inspect the compact search popover first, without scrolling.
    entries = _collect_search_entries(search_list)
    update_candidates(entries)
    element = _find_exact_match_in_entries(entries, contact_name)
    if element is not None:
        logger.info("Found exact match for %s in initial search results", contact_name)
        click_element_center(element)
        return True, current_candidates()

    _expand_section_if_needed(search_list, "Contacts")
    _expand_section_if_needed(search_list, "Group Chats")

    center = get_list_center(search_list)
    last_bottom_text = None
    stable = 0

    for _ in range(80):
        entries = _collect_search_entries(search_list)
        update_candidates(entries)

        element = _find_exact_match_in_entries(entries, contact_name)
        if element is not None:
            logger.info(
                "Found exact match for %s while scrolling search results",
                contact_name,
            )
            click_element_center(element)
            return True, current_candidates()

        texts: list[str] = []
        for child in ax_get(search_list, kAXChildrenAttribute) or []:
            txt = ax_get(child, kAXValueAttribute) or ax_get(child, kAXTitleAttribute)
            if isinstance(txt, str) and txt.strip():
                texts.append(txt)

        if not texts:
            break

        new_last = texts[-1]
        if new_last == last_bottom_text:
            stable += 1
            if stable >= 3:
                break
        else:
            last_bottom_text = new_last
            stable = 0

        post_scroll(center, -80)
        time.sleep(0.1)

    return False, current_candidates()


def get_list_center(ax_list) -> tuple[float, float]:
    """Compute the on-screen centre of a list, used for scroll events."""
    center = element_center(ax_list)
    if center is None:
        raise WeChatMCPError("Failed to get bounds for list element")
    return center

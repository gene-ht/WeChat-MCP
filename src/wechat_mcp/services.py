"""Use-case orchestration shared by the MCP tools and library callers.

The service layer is where focus handling, policy, state and auditing meet:

    fetch_messages -> policy(read) -> ensure_chat -> messages.fetch
    draft_reply    -> policy(draft) -> ensure_chat -> composer.set_input
    send_reply     -> policy(send) -> ensure_chat -> composer.send -> verify

macOS interaction stays in :mod:`wechat_accessibility`, :mod:`messages` and
:mod:`composer`; this module contains no AX calls of its own so it can be
tested with those collaborators patched.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from . import composer, messages
from .activity import Presence
from .audit import AuditLog
from .config import Settings, load_settings
from .errors import ChatNotFound, ChatOpenFailed, PolicyBlocked
from .logging_config import logger
from .messages import has_screen_capture_permission
from .models import ChatMessage, parse_mode
from .pacing import Pacing, configure_pacing
from .policy import RuntimePolicy
from .policy_config import PolicyConfig
from .state import StateStore
from .wechat_accessibility import (
    ax_tree_stats,
    get_current_chat_name,
    get_wechat_ax_app,
    is_wechat_frontmost,
    is_wechat_running,
    list_chat_names,
    open_chat_for_contact,
    preserve_focus,
)


def _same_chat(left: str | None, right: str) -> bool:
    if not left:
        return False
    return left.strip().casefold() == right.strip().casefold()


class WeChatService:
    def __init__(
        self,
        settings: Settings | None = None,
        state: StateStore | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.state = state or StateStore(
            self.settings.state_path, default_mode=self.settings.mode
        )
        self.audit = audit or AuditLog(
            self.settings.audit_path,
            include_text=self.settings.audit_include_text,
        )
        self.presence = Presence(self.settings.human_idle_seconds)
        self.policy_config = PolicyConfig.load(
            self.settings.resolved_policy_config
        )
        self.policy = RuntimePolicy(
            self.settings,
            self.state,
            self.presence,
            self.policy_config,
        )
        configure_pacing(
            Pacing(
                enabled=self.settings.humanize and self.policy_config.pacing_enabled,
                min_delay=(
                    self.settings.min_action_delay
                    if self.settings.min_action_delay is not None
                    else self.policy_config.min_delay
                ),
                max_delay=(
                    self.settings.max_action_delay
                    if self.settings.max_action_delay is not None
                    else self.policy_config.max_delay
                ),
                thinking_min=self.policy_config.thinking_min,
                thinking_max=self.policy_config.thinking_max,
                typing_chars_per_second=self.policy_config.typing_chars_per_second,
            )
        )
        # Serialise UI actions: two overlapping sends would race on the composer.
        self._ui_lock = threading.RLock()

    # ------------------------------------------------------------- runtime
    def status(self) -> dict[str, Any]:
        running = is_wechat_running()
        current_chat = None
        if running:
            try:
                current_chat = get_current_chat_name()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Could not read current chat: %s", exc)

        ax_stats = ax_tree_stats() if running else {"ok": False, "element_count": 0}
        snapshot = self.state.snapshot()
        return {
            "mode": snapshot["mode"],
            "wechat_running": running,
            "wechat_version": ax_stats.get("version"),
            "ax_tree_ok": ax_stats.get("ok", False),
            "ax_element_count": ax_stats.get("element_count", 0),
            "current_chat": current_chat,
            "allowed_chats": list(self.settings.allowed_chats),
            "sends_last_hour": snapshot["sends_last_hour"],
            "friend_requests_last_day": snapshot["friend_requests_last_day"],
            "max_sends_per_hour": self.policy.messages_per_hour,
            "pending_drafts": snapshot["pending_drafts"],
            "verify_sends": self.settings.verify_sends,
            "human_active": self.presence.human_active(),
            "seconds_since_input": round(self.presence.seconds_since_input(), 1),
            "human_idle_seconds": self.settings.human_idle_seconds,
            "screen_recording": has_screen_capture_permission(),
        }

    def set_mode(self, mode: str) -> dict[str, Any]:
        parsed = parse_mode(mode, default=self.state.mode)
        self.state.set_mode(parsed)
        self.audit.record("set_mode", mode=parsed.value)
        return self.status()

    def list_chats(self) -> list[str]:
        return list_chat_names()

    def ensure_ui_writable(self) -> None:
        """Raise when the runtime mode forbids any UI write."""
        decision = self.policy.check_ui_write()
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.detail)

    # ------------------------------------------------------------- helpers
    def ensure_chat(self, chat_name: str) -> None:
        """Open ``chat_name`` if it is not already the active conversation."""
        current = get_current_chat_name()
        if _same_chat(current, chat_name):
            return

        logger.info("Opening chat %r (current=%r)", chat_name, current)
        result = open_chat_for_contact(chat_name)

        if isinstance(result, dict) and result.get("error"):
            candidates = result.get("candidates") or {}
            raise ChatNotFound(
                str(result["error"]),
                chat_name=chat_name,
                contacts=candidates.get("contacts"),
                group_chats=candidates.get("group_chats"),
            )

        current = get_current_chat_name()
        if current and not _same_chat(current, chat_name):
            raise ChatOpenFailed(
                f"Opened a chat but the title is {current!r}, not {chat_name!r}.",
                expected=chat_name,
                actual=current,
            )

    # ------------------------------------------------------------ use cases
    def open_chat(self, chat_name: str) -> None:
        """Open a chat (read-only navigation), honouring the takeover mode."""
        with self._ui_lock, preserve_focus(self.settings.restore_focus):
            self._open_chat(chat_name)

    def _open_chat(self, chat_name: str) -> None:
        decision = self.policy.check_read(chat_name)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.detail, chat_name=chat_name)
        self.ensure_chat(chat_name)

    def fetch_messages(
        self, chat_name: str, last_n: int = 50, sender_names: bool = False
    ) -> list[ChatMessage]:
        with self._ui_lock, preserve_focus(self.settings.restore_focus):
            return self._fetch_messages(chat_name, last_n, sender_names)

    def _fetch_messages(
        self, chat_name: str, last_n: int, sender_names: bool = False
    ) -> list[ChatMessage]:
        decision = self.policy.check_read(chat_name)
        if not decision.allowed:
            raise PolicyBlocked(decision.reason, decision.detail, chat_name=chat_name)

        self.ensure_chat(chat_name)
        return messages.fetch_recent_messages(
            last_n=last_n,
            activate=is_wechat_frontmost(),
            sender_names=sender_names,
        )

    def fetch_since(
        self,
        chat_name: str,
        since: datetime,
        max_messages: int = 500,
        sender_names: bool = False,
    ) -> dict[str, Any]:
        """Collect all messages newer than ``since`` from one chat."""
        with self._ui_lock, preserve_focus(self.settings.restore_focus):
            decision = self.policy.check_read(chat_name)
            if not decision.allowed:
                raise PolicyBlocked(
                    decision.reason, decision.detail, chat_name=chat_name
                )
            self.ensure_chat(chat_name)
            rows, complete = messages.fetch_messages_since(
                since, max_messages=max_messages, sender_names=sender_names
            )

        self.audit.record(
            "fetch_messages_since",
            chat=chat_name,
            since=since.date().isoformat(),
            count=len(rows),
            complete=complete,
            mode=self.state.mode.value,
        )
        return {
            "ok": True,
            "action": "fetch_messages_since",
            "chat_name": chat_name,
            "since": since.date().isoformat(),
            "complete": complete,
            "count": len(rows),
            "messages": [
                {
                    "sender": row.sender,
                    "text": row.text,
                    "at": row.at.isoformat(timespec="minutes") if row.at else None,
                    **({"sender_name": row.sender_name} if row.sender_name else {}),
                }
                for row in rows
            ],
        }

    def visible_messages(self, limit: int = 50) -> dict[str, Any]:
        """Read what is on screen right now — no scrolling, no activation."""
        current = get_current_chat_name()
        with self._ui_lock:
            rows = messages.read_visible_messages(
                get_wechat_ax_app(activate=False), classify_senders=True
            )
        return {
            "ok": True,
            "current_chat": current,
            "count": len(rows[-limit:]),
            "messages": [row.to_dict() for row in rows[-limit:]],
        }

    def draft_reply(self, chat_name: str, text: str) -> dict[str, Any]:
        with self._ui_lock, preserve_focus(self.settings.restore_focus):
            return self._draft_reply(chat_name, text)

    def _draft_reply(self, chat_name: str, text: str) -> dict[str, Any]:
        decision = self.policy.check_draft(chat_name, text)
        if not decision.allowed:
            self.audit.record(
                "draft_reply",
                chat=chat_name,
                text=text,
                allowed=False,
                reason=decision.reason,
                mode=self.state.mode.value,
            )
            raise PolicyBlocked(decision.reason, decision.detail, chat_name=chat_name)

        self.ensure_chat(chat_name)
        composer.set_input_value(text)

        self.state.set_draft(chat_name, text)
        self.audit.record(
            "draft_reply",
            chat=chat_name,
            text=text,
            allowed=True,
            mode=self.state.mode.value,
        )
        return {
            "ok": True,
            "action": "draft_reply",
            "chat_name": chat_name,
            "drafted": True,
            "sent": False,
            "text_length": len(text),
            "mode": self.state.mode.value,
        }

    def send_reply(
        self,
        chat_name: str,
        text: str,
        *,
        verify: bool | None = None,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        with self._ui_lock, preserve_focus(self.settings.restore_focus):
            return self._send_reply(
                chat_name,
                text,
                verify=verify,
                replace_existing=replace_existing,
            )

    def _send_reply(
        self,
        chat_name: str,
        text: str,
        *,
        verify: bool | None,
        replace_existing: bool,
    ) -> dict[str, Any]:
        # Cheap checks first so an allow-list refusal costs no UI interaction.
        decision = self.policy.check_send(chat_name, text)
        if not decision.allowed:
            self._audit_blocked(chat_name, text, decision.reason)
            return self._blocked_payload(chat_name, decision.reason, decision.detail)

        self.ensure_chat(chat_name)

        # Duplicate guard needs the newest message of the target chat.
        last_message = messages.read_last_message()
        decision = self.policy.check_send(chat_name, text, last_message=last_message)
        if not decision.allowed:
            self._audit_blocked(chat_name, text, decision.reason)
            return self._blocked_payload(chat_name, decision.reason, decision.detail)

        started = time.time()
        outcome = composer.send_message(
            text,
            verify=self.settings.verify_sends if verify is None else verify,
            replace_existing=replace_existing,
        )
        duration_ms = int((time.time() - started) * 1000)

        self.state.record_send()
        self.state.clear_draft(chat_name)
        self.audit.record(
            "send_reply",
            chat=chat_name,
            text=text,
            allowed=True,
            mode=self.state.mode.value,
            verified=outcome.get("verified"),
            method=outcome.get("method"),
            duration_ms=duration_ms,
        )
        return {
            "ok": True,
            "action": "send_reply",
            "chat_name": chat_name,
            "sent": True,
            "verified": outcome.get("verified"),
            "method": outcome.get("method"),
            "text_length": len(text),
            "duration_ms": duration_ms,
            "mode": self.state.mode.value,
        }

    # -------------------------------------------------------------- helpers
    def _blocked_payload(
        self, chat_name: str, reason: str, detail: str
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "action": "send_reply",
            "chat_name": chat_name,
            "sent": False,
            "blocked_reason": reason,
            "detail": detail,
            "mode": self.state.mode.value,
        }

    def _audit_blocked(self, chat_name: str, text: str, reason: str) -> None:
        self.audit.record(
            "send_reply",
            chat=chat_name,
            text=text,
            allowed=False,
            reason=reason,
            mode=self.state.mode.value,
        )
        logger.info("Send blocked for %r: %s", chat_name, reason)


_service: WeChatService | None = None


def get_service() -> WeChatService:
    """Return the process-wide service, creating it on first use."""
    global _service
    if _service is None:
        _service = WeChatService()
    return _service


def configure_service(service: WeChatService | None) -> None:
    """Replace the process-wide service (used by tests)."""
    global _service
    _service = service

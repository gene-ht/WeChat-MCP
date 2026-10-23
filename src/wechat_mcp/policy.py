"""Deterministic policy gate for reading, drafting and sending.

The LLM proposes; this module decides. Everything here is pure logic over
:class:`Settings` and :class:`StateStore`, so it can be unit-tested without
touching macOS APIs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .activity import Presence
from .config import Settings
from .models import ChatMessage, RuntimeMode
from .policy_config import PolicyConfig
from .state import StateStore

RATE_WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = "ok"
    detail: str = ""

    @classmethod
    def allow(cls) -> Decision:
        return cls(True)

    @classmethod
    def deny(cls, reason: str, detail: str) -> Decision:
        return cls(False, reason, detail)


class RuntimePolicy:
    def __init__(
        self,
        settings: Settings,
        state: StateStore,
        presence: Presence | None = None,
        config: PolicyConfig | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.presence = presence
        self.config = config or PolicyConfig()

    @property
    def messages_per_hour(self) -> int:
        if self.settings.max_sends_per_hour is not None:
            return self.settings.max_sends_per_hour
        return self.config.messages_max_per_hour

    # --------------------------------------------------------------- checks
    def _human_yield(self) -> Decision | None:
        """Return a denial while a human is actively using the machine."""
        if not self.settings.respect_human or self.presence is None:
            return None
        if not self.presence.human_active():
            return None
        idle = self.settings.human_idle_seconds
        since = self.presence.seconds_since_input()
        return Decision.deny(
            "human_active",
            f"Human input {since:.0f}s ago; the agent yields until the keyboard "
            f"and mouse have been idle for {idle:.0f}s.",
        )

    def check_read(self, chat_name: str) -> Decision:
        mode = self.state.mode
        if mode is RuntimeMode.TAKEOVER:
            return Decision.deny(
                "mode_takeover",
                "WeChat is in human takeover; the agent will not read or screenshot.",
            )
        human = self._human_yield()
        return human if human is not None else Decision.allow()

    def check_ui_write(self) -> Decision:
        """Gate any UI write that is not a chat reply (contacts, Moments)."""
        mode = self.state.mode
        if mode is RuntimeMode.TAKEOVER:
            return Decision.deny(
                "mode_takeover", "WeChat is in human takeover; no UI actions allowed."
            )
        if mode is RuntimeMode.PAUSED:
            return Decision.deny(
                "mode_paused", "Auto-reply is paused; no UI actions allowed."
            )
        human = self._human_yield()
        return human if human is not None else Decision.allow()

    def check_draft(self, chat_name: str, text: str) -> Decision:
        mode = self.state.mode
        if mode is RuntimeMode.TAKEOVER:
            return Decision.deny(
                "mode_takeover", "WeChat is in human takeover; drafting is disabled."
            )
        if mode is RuntimeMode.PAUSED:
            return Decision.deny(
                "mode_paused", "Auto-reply is paused; no drafts are written."
            )
        human = self._human_yield()
        if human is not None:
            return human
        if not self.settings.is_chat_allowed(chat_name):
            return Decision.deny(
                "chat_not_allowed",
                f"Chat {chat_name!r} is not in WECHAT_MCP_ALLOWED_CHATS.",
            )
        return self._check_text(text)

    def check_send(
        self,
        chat_name: str,
        text: str,
        *,
        last_message: ChatMessage | None = None,
        now: float | None = None,
    ) -> Decision:
        mode = self.state.mode
        if mode is RuntimeMode.TAKEOVER:
            return Decision.deny(
                "mode_takeover", "WeChat is in human takeover; sending is disabled."
            )
        if mode is RuntimeMode.PAUSED:
            return Decision.deny(
                "mode_paused", "Auto-reply is paused; no messages will be sent."
            )
        human = self._human_yield()
        if human is not None:
            return human
        if mode is RuntimeMode.DRAFT_ONLY:
            return Decision.deny(
                "mode_draft_only",
                "Server is in draft_only mode. Use draft_reply_to_chat and let a "
                "human send it, or switch to active mode explicitly.",
            )
        if not self.settings.is_chat_allowed(chat_name):
            return Decision.deny(
                "chat_not_allowed",
                f"Chat {chat_name!r} is not in WECHAT_MCP_ALLOWED_CHATS.",
            )

        text_decision = self._check_text(text)
        if not text_decision.allowed:
            return text_decision

        if not self.settings.allow_duplicate_reply and self._is_duplicate(
            text, last_message
        ):
            return Decision.deny(
                "duplicate_reply",
                "The last message from you already has exactly this text; "
                "refusing to send it twice. Set WECHAT_MCP_ALLOW_DUPLICATE=1 to override.",
            )

        threshold = now if now is not None else time.time()
        recent = self.state.sends_since(threshold - RATE_WINDOW_SECONDS)
        limit = self.messages_per_hour
        if limit > 0 and recent >= limit:
            return Decision.deny(
                "rate_limited",
                f"{recent} messages were sent in the last hour; the limit is "
                f"{limit}. Set WECHAT_MCP_MAX_SENDS_PER_HOUR=0 to disable.",
            )
        return Decision.allow()

    def check_friend_request(self, *, now: float | None = None) -> Decision:
        """Gate friend requests: mode/human first, then per-minute/day quotas."""
        base = self.check_ui_write()
        if not base.allowed:
            return base

        moment = now if now is not None else time.time()
        per_minute = self.config.friend_request_max_per_minute
        per_day = self.config.friend_request_max_per_day

        if per_minute > 0:
            recent = self.state.friend_requests_since(moment - 60)
            if recent >= per_minute:
                return Decision.deny(
                    "friend_request_rate_limited",
                    f"{recent} friend requests in the last minute; the limit is "
                    f"{per_minute}/minute (see artifacts/config/policy.toml).",
                )

        if per_day > 0:
            recent = self.state.friend_requests_since(moment - 24 * 3600)
            if recent >= per_day:
                return Decision.deny(
                    "friend_request_rate_limited",
                    f"{recent} friend requests in the last 24h; the limit is "
                    f"{per_day}/day (see artifacts/config/policy.toml).",
                )
        return Decision.allow()

    # --------------------------------------------------------------- helpers
    def _check_text(self, text: str) -> Decision:
        if not text or not text.strip():
            return Decision.deny("empty_reply", "Reply text is empty.")
        if len(text) > self.settings.max_reply_chars:
            return Decision.deny(
                "reply_too_long",
                f"Reply is {len(text)} chars; the limit is "
                f"{self.settings.max_reply_chars}.",
            )
        return Decision.allow()

    @staticmethod
    def _is_duplicate(text: str, last_message: ChatMessage | None) -> bool:
        if last_message is None or last_message.sender != "ME":
            return False
        return last_message.text.strip() == text.strip()

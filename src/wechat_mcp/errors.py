"""Typed errors for the WeChat automation stack.

The MCP tool layer converts these into structured results; library callers can
catch them individually.
"""

from __future__ import annotations

from typing import Any


class WeChatMCPError(RuntimeError):
    """Base class for all recoverable WeChat automation failures."""

    code = "wechat_error"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": str(self), "code": self.code}
        payload.update(self.context)
        return payload


class WeChatNotRunning(WeChatMCPError):
    code = "wechat_not_running"


class ChatNotFound(WeChatMCPError):
    """No exact session/contact match; carries candidate names for the LLM."""

    code = "chat_not_found"

    def __init__(
        self,
        message: str,
        *,
        chat_name: str,
        contacts: list[str] | None = None,
        group_chats: list[str] | None = None,
    ) -> None:
        super().__init__(
            message,
            chat_name=chat_name,
            candidates={
                "contacts": list(contacts or []),
                "group_chats": list(group_chats or []),
            },
        )


class WeChatUIUnavailable(WeChatMCPError):
    """WeChat is running but exposes no usable Accessibility tree."""

    code = "ax_tree_unavailable"


class ChatOpenFailed(WeChatMCPError):
    code = "chat_open_failed"


class InputFieldNotFound(WeChatMCPError):
    code = "input_field_not_found"


class InputNotEmpty(WeChatMCPError):
    """The composer already holds text (likely a human draft)."""

    code = "input_not_empty"


class SendVerificationFailed(WeChatMCPError):
    """The message could not be confirmed in the conversation after sending."""

    code = "send_verification_failed"


class PolicyBlocked(WeChatMCPError):
    """A runtime policy refused the action before any UI interaction."""

    code = "policy_blocked"

    def __init__(self, reason: str, detail: str, **context: Any) -> None:
        super().__init__(detail, reason=reason, **context)
        self.reason = reason

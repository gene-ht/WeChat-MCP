from __future__ import annotations

import pytest

from wechat_mcp import mcp_server
from wechat_mcp.errors import ChatNotFound, PolicyBlocked
from wechat_mcp.models import ChatMessage


class StubService:
    def __init__(self) -> None:
        self.mode = "active"
        self.calls: list[tuple] = []

    def status(self):
        return {"mode": self.mode, "wechat_running": True, "current_chat": "群A"}

    def set_mode(self, mode):
        self.mode = mode
        self.calls.append(("set_mode", mode))
        return self.status()

    def list_chats(self):
        return ["群A", "群B"]

    def fetch_messages(self, chat_name, last_n=50, sender_names=False):
        self.calls.append(("fetch", chat_name, last_n))
        return [ChatMessage(sender="OTHER", text="在吗")]

    def send_reply(self, chat_name, text, **kwargs):
        self.calls.append(("send", chat_name, text))
        return {"ok": True, "sent": True, "verified": True}

    def draft_reply(self, chat_name, text, **kwargs):
        self.calls.append(("draft", chat_name, text))
        return {"ok": True, "drafted": True, "sent": False}

    def fetch_since(self, chat_name, since, max_messages=500, sender_names=False):
        self.calls.append(("fetch_since", chat_name, since))
        return {"ok": True, "chat_name": chat_name, "count": 1, "messages": []}

    def open_chat(self, chat_name):
        self.calls.append(("open", chat_name))

    def ensure_ui_writable(self):
        self.calls.append(("ui_write_gate",))


@pytest.fixture
def stub(monkeypatch):
    service = StubService()
    monkeypatch.setattr(mcp_server, "get_service", lambda: service)
    return service


def test_status_tool(stub):
    assert mcp_server.get_wechat_status()["mode"] == "active"


def test_set_mode_tool(stub):
    result = mcp_server.set_wechat_mode("draft")
    assert result["mode"] == "draft_only"
    assert stub.calls[-1] == ("set_mode", "draft_only")


def test_set_mode_rejects_unknown_value(stub):
    result = mcp_server.set_wechat_mode("banana")
    assert result["ok"] is False
    assert "allowed" in result


def test_list_chats_tool(stub):
    assert mcp_server.list_wechat_chats() == ["群A", "群B"]


def test_fetch_tool_returns_message_dicts(stub):
    result = mcp_server.fetch_messages_by_chat("群A", last_n=5)
    assert result == [{"sender": "OTHER", "text": "在吗"}]
    assert stub.calls[-1] == ("fetch", "群A", 5)


def test_fetch_tool_surfaces_candidates(stub, monkeypatch):
    def raise_not_found(chat_name, last_n=50, sender_names=False):
        raise ChatNotFound(
            "no exact match",
            chat_name=chat_name,
            contacts=["群A-客户"],
            group_chats=["群A-内部"],
        )

    monkeypatch.setattr(stub, "fetch_messages", raise_not_found)
    result = mcp_server.fetch_messages_by_chat("群A")
    assert result[0]["error"] == "no exact match"
    assert result[0]["candidates"]["contacts"] == ["群A-客户"]
    assert result[0]["tool"] == "fetch_messages_by_chat"


def test_reply_tool_returns_blocked_payload(stub, monkeypatch):
    def blocked(chat_name, text, **kwargs):
        return {"ok": False, "sent": False, "blocked_reason": "mode_draft_only"}

    monkeypatch.setattr(stub, "send_reply", blocked)
    result = mcp_server.reply_to_messages_by_chat("群A", "好的")
    assert result["sent"] is False
    assert result["blocked_reason"] == "mode_draft_only"


def test_reply_tool_maps_policy_errors(stub, monkeypatch):
    def boom(chat_name, text, **kwargs):
        raise PolicyBlocked("chat_not_allowed", "nope", chat_name=chat_name)

    monkeypatch.setattr(stub, "send_reply", boom)
    result = mcp_server.reply_to_messages_by_chat("别的群", "好的")
    assert result["ok"] is False
    assert result["blocked_reason"] == "chat_not_allowed"


def test_reply_without_text_only_opens_chat(stub):
    result = mcp_server.reply_to_messages_by_chat("群A", None)
    assert result["sent"] is False
    assert stub.calls[-1] == ("open", "群A")


def test_draft_tool_delegates(stub):
    result = mcp_server.draft_reply_to_chat("群A", "草稿")
    assert result["drafted"] is True
    assert stub.calls[-1] == ("draft", "群A", "草稿")


def test_error_mapping_includes_typed_context():
    exc = ChatNotFound("missing", chat_name="群A", contacts=["群A"])
    payload = mcp_server._tool_error(exc, "群A")
    assert payload["code"] == "chat_not_found"
    assert payload["candidates"]["contacts"] == ["群A"]


def test_publish_is_blocked_when_ui_writes_are_disabled(stub, monkeypatch):
    def deny():
        raise PolicyBlocked("mode_takeover", "human is using WeChat")

    monkeypatch.setattr(stub, "ensure_ui_writable", deny)
    result = mcp_server.publish_moment_without_media("hi")
    assert result["ok"] is False
    assert result["blocked_reason"] == "mode_takeover"


def test_add_contact_is_gated_too(stub, monkeypatch):
    def deny():
        raise PolicyBlocked("mode_paused", "paused")

    monkeypatch.setattr(stub, "ensure_ui_writable", deny)
    result = mcp_server.add_contact_by_wechat_id("wxid_test")
    assert result["blocked_reason"] == "mode_paused"


def test_fetch_since_tool_parses_date(stub):
    result = mcp_server.fetch_messages_since("群A", "2026-10-01")
    assert result["ok"] is True
    assert stub.calls[-1][0] == "fetch_since"
    assert stub.calls[-1][2].date().isoformat() == "2026-10-01"


def test_fetch_since_tool_rejects_bad_date(stub):
    result = mcp_server.fetch_messages_since("群A", "not-a-date")
    assert result["ok"] is False
    assert "Invalid date" in result["error"]

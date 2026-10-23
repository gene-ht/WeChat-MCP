from __future__ import annotations

from contextlib import nullcontext

import pytest

from wechat_mcp import services
from wechat_mcp.audit import NullAuditLog
from wechat_mcp.config import Settings
from wechat_mcp.errors import ChatNotFound, PolicyBlocked
from wechat_mcp.models import ChatMessage, RuntimeMode
from wechat_mcp.services import WeChatService
from wechat_mcp.state import StateStore


class Harness:
    def __init__(self, service, audit):
        self.service = service
        self.audit = audit
        self.sent: list[dict] = []
        self.drafts: list[str] = []
        self.opened: list[str] = []
        self.current = "工作群"
        self.last_message = ChatMessage(sender="OTHER", text="在吗")
        self.messages = [ChatMessage(sender="OTHER", text="在吗")]
        self.open_result = None


@pytest.fixture
def harness(tmp_path, monkeypatch):
    settings = Settings(state_dir=tmp_path, allowed_chats=("工作群",))
    state = StateStore(settings.state_path, default_mode=RuntimeMode.ACTIVE)
    state.set_mode(RuntimeMode.ACTIVE)
    audit = NullAuditLog()
    service = WeChatService(settings=settings, state=state, audit=audit)
    h = Harness(service, audit)
    monkeypatch.setattr(services, "preserve_focus", lambda *a, **k: nullcontext())

    def fake_open(name):
        h.opened.append(name)
        if h.open_result is not None:
            return h.open_result
        h.current = name
        return None

    monkeypatch.setattr(services, "get_current_chat_name", lambda: h.current)
    monkeypatch.setattr(services, "open_chat_for_contact", fake_open)
    monkeypatch.setattr(services, "is_wechat_running", lambda: True)
    monkeypatch.setattr(services, "list_chat_names", lambda: ["工作群", "家人群"])
    monkeypatch.setattr(
        services.messages, "fetch_recent_messages", lambda last_n, **kw: h.messages
    )
    monkeypatch.setattr(services.messages, "read_last_message", lambda: h.last_message)
    monkeypatch.setattr(
        services.composer,
        "send_message",
        lambda text, **kwargs: (
            h.sent.append({"text": text, **kwargs})
            or {"sent": True, "verified": True, "method": "return"}
        ),
    )
    monkeypatch.setattr(
        services.composer, "set_input_value", lambda text: h.drafts.append(text)
    )
    return h


def test_status_reports_mode_and_chat(harness):
    status = harness.service.status()
    assert status["mode"] == "active"
    assert status["wechat_running"] is True
    assert status["current_chat"] == "工作群"
    assert status["allowed_chats"] == ["工作群"]


def test_fetch_messages_does_not_reopen_same_chat(harness):
    messages = harness.service.fetch_messages("工作群", last_n=10)
    assert messages == harness.messages
    assert harness.opened == []


def test_fetch_messages_opens_other_chat(harness):
    harness.messages = [ChatMessage(sender="ME", text="hi")]
    harness.service.fetch_messages("工作群", last_n=10)
    assert harness.current == "工作群"
    assert harness.opened == []


def test_fetch_raises_with_candidates_when_chat_missing(harness):
    harness.current = "家人群"
    harness.open_result = {
        "error": "no exact match",
        "chat_name": "工作群",
        "candidates": {"contacts": ["工作群-客户"], "group_chats": []},
    }
    with pytest.raises(ChatNotFound) as excinfo:
        harness.service.fetch_messages("工作群")
    assert excinfo.value.context["candidates"]["contacts"] == ["工作群-客户"]


def test_draft_only_writes_composer_and_records_draft(harness):
    harness.service.state.set_mode(RuntimeMode.DRAFT_ONLY)
    result = harness.service.draft_reply("工作群", "收到，稍后回复")

    assert result["drafted"] is True
    assert result["sent"] is False
    assert harness.drafts == ["收到，稍后回复"]
    assert harness.sent == []
    assert harness.service.state.get_draft("工作群").text == "收到，稍后回复"
    assert harness.audit.entries[-1]["action"] == "draft_reply"


def test_draft_is_blocked_when_paused(harness):
    harness.service.state.set_mode(RuntimeMode.PAUSED)
    with pytest.raises(PolicyBlocked) as excinfo:
        harness.service.draft_reply("工作群", "hi")
    assert excinfo.value.reason == "mode_paused"
    assert harness.drafts == []


def test_send_records_state_and_clears_draft(harness):
    harness.service.state.set_draft("工作群", "先前的草稿")
    result = harness.service.send_reply("工作群", "好的")

    assert result["sent"] is True
    assert result["verified"] is True
    assert harness.sent[0]["text"] == "好的"
    assert harness.service.state.sends_since(0) == 1
    assert harness.service.state.get_draft("工作群") is None


def test_send_is_blocked_in_draft_only_without_touching_wechat(harness):
    harness.service.state.set_mode(RuntimeMode.DRAFT_ONLY)
    result = harness.service.send_reply("工作群", "好的")

    assert result["sent"] is False
    assert result["blocked_reason"] == "mode_draft_only"
    assert harness.sent == []


def test_send_outside_allowlist_is_blocked(harness):
    result = harness.service.send_reply("别的群", "好的")
    assert result["blocked_reason"] == "chat_not_allowed"
    assert harness.opened == []
    assert harness.sent == []


def test_send_refuses_duplicate_of_last_message(harness):
    harness.last_message = ChatMessage(sender="ME", text="好的")
    result = harness.service.send_reply("工作群", "好的")

    assert result["blocked_reason"] == "duplicate_reply"
    assert harness.sent == []


def test_send_verification_can_be_forced_off(harness):
    harness.service.send_reply("工作群", "好的", verify=False)
    assert harness.sent[0]["verify"] is False


def test_list_chats(harness):
    assert harness.service.list_chats() == ["工作群", "家人群"]


def test_set_mode_persists(harness):
    status = harness.service.set_mode("takeover")
    assert status["mode"] == "takeover"
    assert harness.service.state.mode is RuntimeMode.TAKEOVER

from __future__ import annotations

import time

import pytest

from wechat_mcp.activity import Presence
from wechat_mcp.config import Settings
from wechat_mcp.models import ChatMessage, RuntimeMode
from wechat_mcp.policy import RuntimePolicy
from wechat_mcp.policy_config import PolicyConfig
from wechat_mcp.state import StateStore


@pytest.fixture
def make_policy(tmp_path):
    def _make(mode: RuntimeMode = RuntimeMode.ACTIVE, **overrides):
        settings = Settings(state_dir=tmp_path, **overrides)
        store = StateStore(settings.state_path, default_mode=mode)
        store.set_mode(mode)
        return RuntimePolicy(settings, store), store

    return _make


def test_active_allows_read_draft_and_send(make_policy):
    policy, _ = make_policy()
    assert policy.check_read("工作群").allowed
    assert policy.check_draft("工作群", "收到").allowed
    assert policy.check_send("工作群", "收到").allowed


def test_draft_only_blocks_send_but_allows_draft(make_policy):
    policy, _ = make_policy(RuntimeMode.DRAFT_ONLY)
    assert policy.check_draft("工作群", "收到").allowed
    decision = policy.check_send("工作群", "收到")
    assert not decision.allowed
    assert decision.reason == "mode_draft_only"


def test_paused_blocks_draft_and_send(make_policy):
    policy, _ = make_policy(RuntimeMode.PAUSED)
    assert policy.check_read("工作群").allowed
    assert policy.check_draft("工作群", "收到").reason == "mode_paused"
    assert policy.check_send("工作群", "收到").reason == "mode_paused"


def test_takeover_blocks_everything(make_policy):
    policy, _ = make_policy(RuntimeMode.TAKEOVER)
    assert policy.check_read("工作群").reason == "mode_takeover"
    assert policy.check_draft("工作群", "收到").reason == "mode_takeover"
    assert policy.check_send("工作群", "收到").reason == "mode_takeover"


def test_allowlist_limits_writes_but_not_reads(make_policy):
    policy, _ = make_policy(allowed_chats=("工作群",))
    assert policy.check_read("别的群").allowed
    assert policy.check_draft("工作群", "hi").allowed
    assert policy.check_send("别的群", "hi").reason == "chat_not_allowed"


def test_allowlist_is_case_insensitive(make_policy):
    policy, _ = make_policy(allowed_chats=("Work Group",))
    assert policy.check_send("work group", "hi").allowed


def test_empty_and_oversized_text_are_rejected(make_policy):
    policy, _ = make_policy(max_reply_chars=10)
    assert policy.check_send("工作群", "   ").reason == "empty_reply"
    assert policy.check_send("工作群", "x" * 11).reason == "reply_too_long"


def test_rate_limit_uses_state(make_policy):
    policy, store = make_policy(max_sends_per_hour=2)
    now = time.time()
    store.record_send(now)
    store.record_send(now)
    assert policy.check_send("工作群", "hi", now=now).reason == "rate_limited"


def test_duplicate_of_my_last_message_is_blocked(make_policy):
    policy, _ = make_policy()
    last = ChatMessage(sender="ME", text="收到")
    assert policy.check_send("工作群", "收到", last_message=last).reason == "duplicate_reply"


def test_duplicate_check_ignores_other_peoples_messages(make_policy):
    policy, _ = make_policy()
    last = ChatMessage(sender="OTHER", text="收到")
    assert policy.check_send("工作群", "收到", last_message=last).allowed


def test_duplicate_can_be_allowed_by_setting(make_policy):
    policy, _ = make_policy(allow_duplicate_reply=True)
    last = ChatMessage(sender="ME", text="收到")
    assert policy.check_send("工作群", "收到", last_message=last).allowed


def test_ui_writes_follow_the_same_mode_gate(make_policy):
    active, _ = make_policy()
    assert active.check_ui_write().allowed

    paused, _ = make_policy(RuntimeMode.PAUSED)
    assert paused.check_ui_write().reason == "mode_paused"

    takeover, _ = make_policy(RuntimeMode.TAKEOVER)
    assert takeover.check_ui_write().reason == "mode_takeover"


def test_human_activity_yields_until_idle(tmp_path):
    now = [1000.0]
    presence = Presence(idle_seconds=30, clock=lambda: now[0])
    settings = Settings(state_dir=tmp_path, human_idle_seconds=30)
    store = StateStore(settings.state_path, default_mode=RuntimeMode.ACTIVE)
    store.set_mode(RuntimeMode.ACTIVE)
    policy = RuntimePolicy(settings, store, presence)

    presence.mark_input()
    assert policy.check_send("工作群", "hi").reason == "human_active"
    assert policy.check_draft("工作群", "hi").reason == "human_active"
    assert policy.check_read("工作群").reason == "human_active"

    now[0] += 31
    assert policy.check_send("工作群", "hi").allowed
    assert policy.check_draft("工作群", "hi").allowed


def test_respect_human_can_be_disabled(tmp_path):
    presence = Presence(idle_seconds=30)
    presence.mark_input()
    settings = Settings(state_dir=tmp_path, respect_human=False)
    store = StateStore(settings.state_path, default_mode=RuntimeMode.ACTIVE)
    policy = RuntimePolicy(settings, store, presence)
    assert policy.check_send("工作群", "hi").allowed


def test_rate_limit_can_be_disabled_with_zero(make_policy):
    policy, store = make_policy(max_sends_per_hour=0)
    now = time.time()
    for _ in range(5):
        store.record_send(now)
    assert policy.check_send("工作群", "hi", now=now).allowed


def test_friend_requests_are_limited_per_minute(make_policy, tmp_path):
    policy, store = make_policy()
    policy.config = PolicyConfig(
        friend_request_max_per_minute=1, friend_request_max_per_day=100
    )
    now = time.time()
    store.record_friend_request(now)

    denied = policy.check_friend_request(now=now + 30)
    assert denied.reason == "friend_request_rate_limited"
    assert "per minute" in denied.detail or "/minute" in denied.detail

    assert policy.check_friend_request(now=now + 61).allowed


def test_friend_requests_are_limited_per_day(make_policy):
    policy, store = make_policy()
    policy.config = PolicyConfig(
        friend_request_max_per_minute=0, friend_request_max_per_day=2
    )
    now = time.time()
    store.record_friend_request(now - 7200)
    store.record_friend_request(now - 3600)

    denied = policy.check_friend_request(now=now)
    assert denied.reason == "friend_request_rate_limited"
    assert "/day" in denied.detail

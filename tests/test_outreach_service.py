from __future__ import annotations

import pytest

from wechat_mcp.config import Settings
from wechat_mcp.outreach.models import LeadStage
from wechat_mcp.outreach.scripts import ChannelScripts
from wechat_mcp.outreach.service import OutreachService
from wechat_mcp.outreach.store import LeadStore
from wechat_mcp.policy import RuntimePolicy
from wechat_mcp.policy_config import PolicyConfig
from wechat_mcp.state import StateStore

CONFIG = """
[defaults]
company = "ACME"

[channels.douyin_job]
label = "抖音-求职"
greeting = "你好{name}，我是{company}的招聘，看到你想找{user_state}工作。"
followup = ["欢迎通过", "方便发简历吗？"]
"""


class FakeWeChat:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.followups: list[tuple[str, list[str]]] = []

    def send_friend_request(self, phone: str, greeting: str):
        self.requests.append((phone, greeting))
        return {"ok": True}

    def send_followup(self, chat_name: str, messages: list[str]):
        self.followups.append((chat_name, messages))
        return [{"sent": True} for _ in messages]


@pytest.fixture
def service(tmp_path):
    config = tmp_path / "outreach.toml"
    config.write_text(CONFIG, encoding="utf-8")
    store = LeadStore(tmp_path / "leads.csv")
    fake = FakeWeChat()
    svc = OutreachService(store, ChannelScripts.load(config), wechat=fake, dry_run=False)
    return svc, fake


def test_dry_run_previews_without_touching_wechat(tmp_path):
    config = tmp_path / "outreach.toml"
    config.write_text(CONFIG, encoding="utf-8")
    fake = FakeWeChat()
    svc = OutreachService(
        LeadStore(tmp_path / "leads.csv"),
        ChannelScripts.load(config),
        wechat=fake,
        dry_run=True,
    )

    result = svc.send_friend_request(
        "13800000000", channel="douyin_job", name="张三", user_state="运营"
    )
    assert result["dry_run"] is True
    assert "张三" in result["would_send"]
    assert fake.requests == []
    assert svc.store.get("13800000000").stage is LeadStage.NEW


def test_live_request_uses_channel_copy_and_advances_stage(service):
    svc, fake = service
    result = svc.send_friend_request(
        "13800000000", channel="douyin_job", name="张三", user_state="运营"
    )

    assert result["ok"] is True
    assert fake.requests == [
        ("13800000000", "你好张三，我是ACME的招聘，看到你想找运营工作。")
    ]
    lead = svc.store.get("13800000000")
    assert lead.stage is LeadStage.REQUEST_SENT
    assert lead.to_dict()["greeting_sent"] is True
    assert lead.to_dict()["accepted"] is False


def test_request_is_refused_when_not_new(service):
    svc, _ = service
    svc.send_friend_request(
        "13800000000", channel="douyin_job", name="张三", user_state="运营"
    )
    result = svc.send_friend_request("13800000000", channel="douyin_job")

    assert result["ok"] is False
    assert "already at stage request_sent" in result["reason"]


def test_followup_requires_accepted_stage(service):
    svc, fake = service
    svc.store.upsert("13800000000", "douyin_job", name="张三")
    result = svc.send_followup("13800000000")

    assert result["ok"] is False
    assert "requires stage 'accepted'" in result["reason"]
    assert fake.followups == []


def test_followup_sends_all_messages_then_marks_greeted(service):
    svc, fake = service
    svc.store.upsert("13800000000", "douyin_job", name="张三")
    svc.store.update_stage("13800000000", LeadStage.REQUEST_SENT)
    svc.store.update_stage("13800000000", LeadStage.ACCEPTED)

    result = svc.send_followup("13800000000")

    assert result["ok"] is True
    assert fake.followups == [("张三", ["欢迎通过", "方便发简历吗？"])]
    lead = svc.store.get("13800000000")
    assert lead.stage is LeadStage.GREETED
    assert lead.to_dict()["followup_sent"] is True


def test_import_counts_and_reports_bad_channels(service):
    svc, _ = service
    result = svc.import_leads(
        [
            {"phone": "1", "channel": "douyin_job", "name": "A"},
            {"identifier": "2", "name": "B"},
            {"identifier": "3", "channel": "unknown", "name": "C"},
        ],
        default_channel="douyin_job",
    )

    assert result["created"] == 2
    assert len(result["errors"]) == 1
    assert "unknown" in result["errors"][0]
    assert svc.store.get("2").channel == "douyin_job"


def test_mark_stage_can_record_reply(service):
    svc, _ = service
    svc.store.upsert("13800000000", "douyin_job")
    svc.store.update_stage("13800000000", LeadStage.REQUEST_SENT)
    svc.store.update_stage("13800000000", LeadStage.ACCEPTED)
    svc.store.update_stage("13800000000", LeadStage.GREETED)

    result = svc.mark_stage("13800000000", "replied", note="客户回复了")

    assert result["lead"]["stage"] == "replied"
    assert result["lead"]["replied"] is True


def test_friend_request_is_blocked_by_policy(tmp_path):
    config = tmp_path / "outreach.toml"
    config.write_text(CONFIG, encoding="utf-8")
    store = LeadStore(tmp_path / "leads.csv")
    fake = FakeWeChat()
    state = StateStore(tmp_path / "state.json")
    policy = RuntimePolicy(
        Settings(state_dir=tmp_path),
        state,
        config=PolicyConfig(friend_request_max_per_minute=1),
    )
    svc = OutreachService(
        store,
        ChannelScripts.load(config),
        wechat=fake,
        dry_run=False,
        policy=policy,
    )

    import time

    state.record_friend_request(time.time())
    result = svc.send_friend_request(
        "13800000000", channel="douyin_job", name="张三", user_state="运营"
    )

    assert result["ok"] is False
    assert result["blocked_reason"] == "friend_request_rate_limited"
    assert fake.requests == []
    assert svc.store.get("13800000000").stage is LeadStage.NEW


def test_successful_request_is_recorded_in_state(tmp_path):
    config = tmp_path / "outreach.toml"
    config.write_text(CONFIG, encoding="utf-8")
    state = StateStore(tmp_path / "state.json")
    policy = RuntimePolicy(
        Settings(state_dir=tmp_path),
        state,
        config=PolicyConfig(),
    )
    svc = OutreachService(
        LeadStore(tmp_path / "leads.csv"),
        ChannelScripts.load(config),
        wechat=FakeWeChat(),
        dry_run=False,
        policy=policy,
    )

    svc.send_friend_request(
        "13800000000", channel="douyin_job", name="张三", user_state="运营"
    )
    assert state.friend_requests_since(0) == 1

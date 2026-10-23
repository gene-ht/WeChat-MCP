from __future__ import annotations

import csv

import pytest

from wechat_mcp.outreach.models import LeadStage, StageTransitionError
from wechat_mcp.outreach.store import LeadStore


@pytest.fixture
def store(tmp_path):
    return LeadStore(tmp_path / "leads.csv")


def test_upsert_creates_and_updates_descriptive_fields(store):
    lead = store.upsert("13800000000", "douyin_job", name="张三", user_state="求职")
    assert lead.stage is LeadStage.NEW
    assert lead.name == "张三"

    again = store.upsert("13800000000", "douyin_job", name="张三丰", user_state="求职-运营")
    assert again.name == "张三丰"
    assert again.user_state == "求职-运营"
    assert again.stage is LeadStage.NEW


def test_stage_transitions_are_validated_and_flip_flags(store):
    store.upsert("13800000000", "douyin_job")
    lead = store.update_stage("13800000000", LeadStage.REQUEST_SENT)
    assert lead.stage is LeadStage.REQUEST_SENT
    assert lead.greeting_sent is True

    with pytest.raises(StageTransitionError):
        store.update_stage("13800000000", LeadStage.REPLIED)


def test_accepted_then_greeted_flags(store):
    store.upsert("13800000000", "douyin_job")
    store.update_stage("13800000000", LeadStage.REQUEST_SENT)
    accepted = store.update_stage("13800000000", LeadStage.ACCEPTED)
    assert accepted.accepted is True

    greeted = store.update_stage("13800000000", LeadStage.GREETED)
    assert greeted.followup_sent is True


def test_list_filters_by_stage_and_channel(store):
    store.upsert("1", "douyin_job")
    store.upsert("2", "wechat_job")
    store.update_stage("1", LeadStage.REQUEST_SENT)

    assert [lead.phone for lead in store.list(stage=LeadStage.REQUEST_SENT)] == ["1"]
    assert [lead.phone for lead in store.list(channel="wechat_job")] == ["2"]


def test_unknown_phone_raises(store):
    with pytest.raises(KeyError):
        store.get("missing")


def test_csv_is_the_store(store, tmp_path):
    store.upsert("13800000000", "douyin_job", name="张三")
    store.update_stage("13800000000", LeadStage.REQUEST_SENT)

    with (tmp_path / "leads.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["phone"] == "13800000000"
    assert rows[0]["stage"] == "request_sent"
    assert rows[0]["greeting_sent"] == "true"
    assert rows[0]["accepted"] == "false"


def test_external_edits_are_picked_up(tmp_path):
    path = tmp_path / "leads.csv"
    store = LeadStore(path)
    store.upsert("13800000000", "douyin_job", name="张三")

    # Simulate someone editing the CSV by hand.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["name"] = "张三丰"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    assert store.get("13800000000").name == "张三丰"


def test_legacy_identifier_header_is_accepted(tmp_path):
    path = tmp_path / "leads.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["identifier", "channel", "name", "stage"])
        writer.writerow(["13800000000", "douyin_job", "张三", "new"])

    store = LeadStore(path)
    assert store.get("13800000000").phone == "13800000000"

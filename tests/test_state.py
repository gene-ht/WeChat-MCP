from __future__ import annotations

import json
import time

from wechat_mcp.models import RuntimeMode
from wechat_mcp.state import StateStore


def test_mode_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.set_mode(RuntimeMode.DRAFT_ONLY)

    reloaded = StateStore(path)
    assert reloaded.mode is RuntimeMode.DRAFT_ONLY


def test_drafts_persist_and_clear(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.set_draft("工作群", "收到，稍后回复")
    assert store.get_draft("工作群").text == "收到，稍后回复"

    reloaded = StateStore(path)
    assert reloaded.pending_drafts()["工作群"]["text"] == "收到，稍后回复"

    reloaded.clear_draft("工作群")
    assert StateStore(path).get_draft("工作群") is None


def test_corrupt_state_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    store = StateStore(path, default_mode=RuntimeMode.PAUSED)
    assert store.mode is RuntimeMode.PAUSED
    assert store.pending_drafts() == {}


def test_unknown_mode_in_file_falls_back(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "mode": "banana"}), encoding="utf-8")
    store = StateStore(path)
    assert store.mode is RuntimeMode.ACTIVE


def test_send_history_counts_only_the_window(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    now = time.time()
    store.record_send(now - 7200)
    store.record_send(now - 60)

    assert store.sends_since(now - 3600) == 1
    assert store.snapshot()["sends_last_hour"] == 1


def test_snapshot_reports_mode_and_drafts(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.set_mode(RuntimeMode.DRAFT_ONLY)
    store.set_draft("群A", "hi")

    snapshot = store.snapshot()
    assert snapshot["mode"] == "draft_only"
    assert snapshot["pending_drafts"] == 1
    assert snapshot["drafts"]["群A"]["text"] == "hi"


def test_state_write_is_atomic_no_tmp_left_behind(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.set_mode(RuntimeMode.PAUSED)
    assert path.exists()
    assert not (tmp_path / "state.json.tmp").exists()

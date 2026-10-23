from __future__ import annotations

from wechat_mcp.config import Settings, load_settings
from wechat_mcp.models import RuntimeMode, parse_mode, try_parse_mode


def test_defaults_preserve_legacy_behaviour(tmp_path):
    settings = Settings(state_dir=tmp_path)
    assert settings.mode is RuntimeMode.ACTIVE
    assert settings.allowed_chats == ()
    assert settings.is_chat_allowed("任意群")


def test_allowlist_matching_is_case_and_space_insensitive(tmp_path):
    settings = Settings(state_dir=tmp_path, allowed_chats=("Work Group",))
    assert settings.is_chat_allowed(" work group ")
    assert not settings.is_chat_allowed("Family")


def test_load_settings_reads_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WECHAT_MCP_MODE", "draft")
    monkeypatch.setenv("WECHAT_MCP_ALLOWED_CHATS", "工作群, 家人群，客户群")
    monkeypatch.setenv("WECHAT_MCP_MAX_SENDS_PER_HOUR", "5")
    monkeypatch.setenv("WECHAT_MCP_MAX_REPLY_CHARS", "300")
    monkeypatch.setenv("WECHAT_MCP_VERIFY_SENDS", "0")
    monkeypatch.setenv("WECHAT_MCP_AUDIT_TEXT", "1")

    settings = load_settings()
    assert settings.state_dir == tmp_path
    assert settings.mode is RuntimeMode.DRAFT_ONLY
    assert settings.allowed_chats == ("工作群", "家人群", "客户群")
    assert settings.max_sends_per_hour == 5
    assert settings.max_reply_chars == 300
    assert settings.verify_sends is False
    assert settings.audit_include_text is True


def test_invalid_numbers_fall_back_to_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("WECHAT_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WECHAT_MCP_MAX_SENDS_PER_HOUR", "many")
    assert load_settings().max_sends_per_hour == 30


def test_mode_aliases():
    assert parse_mode("auto", default=RuntimeMode.PAUSED) is RuntimeMode.ACTIVE
    assert parse_mode("draft", default=RuntimeMode.ACTIVE) is RuntimeMode.DRAFT_ONLY
    assert parse_mode("human", default=RuntimeMode.ACTIVE) is RuntimeMode.TAKEOVER
    assert parse_mode("nonsense", default=RuntimeMode.PAUSED) is RuntimeMode.PAUSED
    assert try_parse_mode("nonsense") is None


def test_outreach_defaults_live_in_artifacts(tmp_path):
    settings = Settings(state_dir=tmp_path)
    assert settings.resolved_outreach_config == Settings().resolved_outreach_config
    assert str(settings.resolved_leads_csv).endswith("artifacts/outreach/leads.csv")

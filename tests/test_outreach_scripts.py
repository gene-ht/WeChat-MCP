from __future__ import annotations

import pytest

from wechat_mcp.outreach.scripts import ChannelScripts, ScriptError

CONFIG = """
[defaults]
company = "ACME"

[channels.douyin_job]
label = "抖音-求职"
greeting = "你好{name}，我是{company}的{recruiter}，看到你在看{user_state}机会。"
followup = ["欢迎通过 {name}", "方便发简历吗？"]

[channels.douyin_job.vars]
recruiter = "小王"
"""


@pytest.fixture
def scripts(tmp_path):
    path = tmp_path / "outreach.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return ChannelScripts.load(path)


def test_greeting_merges_defaults_vars_and_lead_fields(scripts):
    text = scripts.greeting_for(
        "douyin_job", name="张三", user_state="运营"
    )
    assert text == "你好张三，我是ACME的小王，看到你在看运营机会。"


def test_followup_renders_every_message(scripts):
    assert scripts.followup_for("douyin_job", name="张三") == [
        "欢迎通过 张三",
        "方便发简历吗？",
    ]


def test_unknown_channel_lists_available_ones(scripts):
    with pytest.raises(ScriptError) as excinfo:
        scripts.get("wechat_job")
    assert "douyin_job" in str(excinfo.value)


def test_missing_variable_is_reported(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text(
        '[channels.x]\ngreeting = "hi {nobody}"\n', encoding="utf-8"
    )
    scripts = ChannelScripts.load(path)
    with pytest.raises(ScriptError) as excinfo:
        scripts.greeting_for("x")
    assert "nobody" in str(excinfo.value)


def test_missing_file_yields_empty_config(tmp_path):
    scripts = ChannelScripts.load(tmp_path / "nope.toml")
    assert scripts.names() == []


def test_example_config_is_valid():
    scripts = ChannelScripts.load("config/outreach.example.toml")
    assert set(scripts.names()) == {"douyin_job", "wechat_job", "douyin_partner"}
    text = scripts.greeting_for("douyin_job", user_state="运营")
    assert "示例科技" in text and "找工作" in text

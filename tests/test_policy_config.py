from __future__ import annotations

from wechat_mcp.policy_config import PolicyConfig


def test_defaults_are_conservative():
    config = PolicyConfig()
    assert config.messages_max_per_hour == 30
    assert config.friend_request_max_per_minute == 1
    assert config.friend_request_max_per_day == 100


def test_load_reads_limits(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(
        "[messages]\nmax_per_hour = 200\n"
        "[friend_request]\nmax_per_minute = 2\nmax_per_day = 50\n",
        encoding="utf-8",
    )
    config = PolicyConfig.load(path)
    assert config.messages_max_per_hour == 200
    assert config.friend_request_max_per_minute == 2
    assert config.friend_request_max_per_day == 50


def test_zero_means_unlimited(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text("[friend_request]\nmax_per_day = 0\n", encoding="utf-8")
    assert PolicyConfig.load(path).friend_request_max_per_day == 0


def test_invalid_values_fall_back(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text("[friend_request]\nmax_per_day = -5\n", encoding="utf-8")
    assert PolicyConfig.load(path).friend_request_max_per_day == 100


def test_missing_file_uses_defaults(tmp_path):
    assert PolicyConfig.load(tmp_path / "nope.toml") == PolicyConfig()


def test_reply_pacing_is_loaded(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(
        "[reply_pacing]\nenabled = false\nthinking_min = 2.0\n"
        "thinking_max = 3.0\ntyping_chars_per_second = 5\n"
        "min_delay = 0.4\nmax_delay = 1.2\n",
        encoding="utf-8",
    )
    config = PolicyConfig.load(path)
    assert config.pacing_enabled is False
    assert config.thinking_min == 2.0
    assert config.thinking_max == 3.0
    assert config.typing_chars_per_second == 5
    assert config.min_delay == 0.4
    assert config.max_delay == 1.2

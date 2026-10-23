from __future__ import annotations

from wechat_mcp import pacing
from wechat_mcp.pacing import Pacing


def test_pause_is_jittered_within_bounds(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(pacing.time, "sleep", calls.append)

    p = Pacing(enabled=True, min_delay=0.2, max_delay=0.4)
    p.pause()
    p.pause(scale=2.0)

    assert 0.2 <= calls[0] <= 0.4
    assert 0.4 <= calls[1] <= 0.8


def test_pause_can_be_disabled(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(pacing.time, "sleep", calls.append)

    p = Pacing(enabled=False)
    p.pause()
    p.typing_pause("hello")

    assert calls == []


def test_typing_pause_scales_with_length_and_is_capped(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(pacing.time, "sleep", calls.append)

    p = Pacing(enabled=True)
    p.typing_pause("hi")
    p.typing_pause("x" * 5000)

    assert calls[0] >= 0.8
    assert calls[1] == 8.0


def test_wait_until_returns_true_when_predicate_flips(monkeypatch):
    monkeypatch.setattr(pacing.time, "sleep", lambda _: None)
    state = {"n": 0}

    def predicate() -> bool:
        state["n"] += 1
        return state["n"] >= 3

    assert Pacing(enabled=True).wait_until(predicate, timeout=1.0) is True
    assert state["n"] >= 3


def test_wait_until_times_out(monkeypatch):
    monkeypatch.setattr(pacing.time, "sleep", lambda _: None)
    assert Pacing(enabled=False).wait_until(lambda: False, timeout=0.05) is False


def test_typing_speed_is_configurable(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(pacing.time, "sleep", calls.append)

    Pacing(enabled=True, typing_chars_per_second=10).typing_pause("x" * 100)
    assert calls[0] == 8.0  # capped even at 10 cps


def test_thinking_window_is_configurable(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(pacing.time, "sleep", calls.append)

    Pacing(enabled=True, thinking_min=2.0, thinking_max=2.0).thinking_pause()
    assert calls[0] == 2.0

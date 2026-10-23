from __future__ import annotations

import threading

from wechat_mcp.activity import Presence


def test_presence_starts_idle():
    now = [100.0]
    presence = Presence(idle_seconds=30, clock=lambda: now[0])
    assert presence.human_active() is False
    assert presence.seconds_since_input() >= 30


def test_human_input_pauses_until_idle_window_passes():
    now = [100.0]
    presence = Presence(idle_seconds=30, clock=lambda: now[0])

    presence.mark_input()
    assert presence.human_active() is True

    now[0] += 29
    assert presence.human_active() is True

    now[0] += 2
    assert presence.human_active() is False


def test_mark_input_is_thread_safe():
    presence = Presence(idle_seconds=30)

    def touch() -> None:
        for _ in range(200):
            presence.mark_input()

    threads = [threading.Thread(target=touch) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert presence.human_active() is True

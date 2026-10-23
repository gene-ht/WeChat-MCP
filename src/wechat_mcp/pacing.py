"""Human-like pacing for UI actions.

WeChat is a GUI: a real person pauses between opening a chat, reading it and
typing. Firing clicks and keystrokes back-to-back is both unnatural and
unreliable — Qt needs a moment to build its accessibility tree after a window
comes forward, and the message list needs a beat to settle after a scroll.

Delays are jittered so consecutive actions never share an identical rhythm.
Everything degrades to a no-op when pacing is disabled (tests, benchmarks).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

# Deliberately conservative: roughly 0.2–0.7 s between UI actions, which is
# fast enough to stay useful but slow enough for the client to keep up.
DEFAULT_MIN_DELAY = 0.25
DEFAULT_MAX_DELAY = 0.90


@dataclass
class Pacing:
    enabled: bool = True
    min_delay: float = DEFAULT_MIN_DELAY
    max_delay: float = DEFAULT_MAX_DELAY
    thinking_min: float = 1.0
    thinking_max: float = 2.8
    typing_chars_per_second: float = 7.0

    def pause(self, scale: float = 1.0, *, minimum: float = 0.0) -> None:
        """Sleep for a jittered delay, optionally scaled (e.g. 2.0 for "think")."""
        if not self.enabled:
            return
        delay = random.uniform(self.min_delay, self.max_delay) * scale
        time.sleep(max(minimum, delay))

    def wait_until(
        self,
        predicate: Callable[[], bool],
        *,
        timeout: float = 3.0,
        interval: float = 0.2,
    ) -> bool:
        """Poll ``predicate`` until it is true or ``timeout`` expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return bool(predicate())

    def typing_pause(self, text: str) -> None:
        """Pause proportional to the amount of text being written.

        ~7 characters per second is a relaxed human pace for Chinese input;
        capped so a long message does not stall the tool for minutes.
        """
        if not self.enabled:
            return
        delay = min(8.0, max(0.8, len(text) / max(1.0, self.typing_chars_per_second)))
        time.sleep(delay)

    def thinking_pause(self) -> None:
        """A short beat before hitting send, as if reading and deciding."""
        if not self.enabled:
            return
        low = min(self.thinking_min, self.thinking_max)
        high = max(self.thinking_min, self.thinking_max)
        time.sleep(random.uniform(low, high))


_pacing = Pacing()


def configure_pacing(pacing: Pacing) -> None:
    global _pacing
    _pacing = pacing


def get_pacing() -> Pacing:
    return _pacing


def pause(scale: float = 1.0, *, minimum: float = 0.0) -> None:
    _pacing.pause(scale, minimum=minimum)


def wait_until(
    predicate: Callable[[], bool], *, timeout: float = 3.0, interval: float = 0.2
) -> bool:
    return _pacing.wait_until(predicate, timeout=timeout, interval=interval)


def typing_pause(text: str) -> None:
    _pacing.typing_pause(text)


def thinking_pause() -> None:
    _pacing.thinking_pause()

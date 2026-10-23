"""Detect real human input and yield the machine back to its owner.

While the agent is working, a human may start typing or using the mouse. The
server should stop touching WeChat immediately, then resume once the keyboard
and mouse have been quiet for a while.

Synthetic events posted by this process carry its PID in
``kCGEventSourceUnixProcessID``, which is how the monitor tells the agent's own
clicks and keystrokes apart from a person's.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable

import Quartz

from .logging_config import logger

WATCHED_EVENTS = (
    Quartz.kCGEventKeyDown,
    Quartz.kCGEventFlagsChanged,
    Quartz.kCGEventLeftMouseDown,
    Quartz.kCGEventRightMouseDown,
    Quartz.kCGEventOtherMouseDown,
    Quartz.kCGEventLeftMouseDragged,
    Quartz.kCGEventScrollWheel,
    Quartz.kCGEventMouseMoved,
)


class Presence:
    """Tracks the last time a human touched the keyboard or mouse."""

    def __init__(self, idle_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_input = clock() - idle_seconds

    def mark_input(self) -> None:
        with self._lock:
            self._last_input = self._clock()

    def seconds_since_input(self) -> float:
        with self._lock:
            return max(0.0, self._clock() - self._last_input)

    def human_active(self) -> bool:
        return self.seconds_since_input() < self.idle_seconds


class HumanActivityMonitor(threading.Thread):
    """Listen-only CGEventTap that reports human input to :class:`Presence`."""

    def __init__(self, presence: Presence) -> None:
        super().__init__(name="wechat-mcp-presence", daemon=True)
        self._presence = presence
        self._stopping = threading.Event()
        self._runloop = None

    def run(self) -> None:
        # CGEventMaskBit(type) is 1 << type; pyobjc does not export the macro.
        mask = 0
        for event_type in WATCHED_EVENTS:
            mask |= 1 << event_type

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if tap is None:
            logger.warning(
                "Could not create the input monitor (Accessibility permission?); "
                "human-activity auto-pause is disabled."
            )
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._runloop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._runloop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(tap, True)
        logger.info("Human-activity monitor started")

        while not self._stopping.is_set():
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.25, False)

        Quartz.CGEventTapEnable(tap, False)

    def _callback(self, proxy, event_type, event, refcon):
        try:
            source_pid = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGEventSourceUnixProcessID
            )
            if source_pid != os.getpid():
                self._presence.mark_input()
        except Exception:  # pragma: no cover - never break the tap
            pass
        return event

    def stop(self) -> None:
        self._stopping.set()
        if self._runloop is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                Quartz.CFRunLoopStop(self._runloop)

"""Double-tap-Ctrl detection with pynput (cross-platform).

A "tap" is Ctrl pressed and released on its own (no other key in between,
held < 350 ms). Two taps whose press times are within `interval_ms` fire
`on_trigger`. Synthetic key events (e.g. our own Ctrl+C) are ignored.
Callbacks run on the pynput thread — marshal to the UI thread yourself.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from pynput import keyboard

log = logging.getLogger(__name__)

_CTRL = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
MAX_HOLD_S = 0.35


class DoubleTapDetector:
    """Pure state machine (no I/O) so it can be unit tested."""

    def __init__(self, interval_ms: int = 400):
        self.interval_s = interval_ms / 1000
        self._down_at: float | None = None    # when Ctrl went down (None if up)
        self._dirty = False                   # another key pressed during this Ctrl press
        self._last_tap: float | None = None   # press time of the previous clean tap

    def press(self, is_ctrl: bool, now: float) -> None:
        if is_ctrl:
            if self._down_at is None:          # ignore auto-repeat while held
                self._down_at = now
                self._dirty = False
        else:
            self._dirty = True
            self._last_tap = None              # Ctrl+C, typing etc. break the sequence

    def release(self, is_ctrl: bool, now: float) -> bool:
        """Returns True when a double tap completes."""
        if not is_ctrl or self._down_at is None:
            return False
        down, self._down_at = self._down_at, None
        if self._dirty or now - down > MAX_HOLD_S:
            self._last_tap = None
            return False
        if self._last_tap is not None and down - self._last_tap <= self.interval_s:
            self._last_tap = None
            return True
        self._last_tap = down
        return False


class DoubleTapListener:
    def __init__(self, on_trigger: Callable[[], None], interval_ms: int = 400,
                 on_escape: Callable[[], None] | None = None):
        self.on_trigger = on_trigger
        self.on_escape = on_escape
        self.detector = DoubleTapDetector(interval_ms)
        self.enabled = True
        self._suppress_until = 0.0
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    def set_interval(self, interval_ms: int) -> None:
        self.detector.interval_s = interval_ms / 1000

    def suppress(self, seconds: float) -> None:
        """Ignore all key events for a while (used while we send Ctrl+C)."""
        self._suppress_until = time.monotonic() + seconds

    def start(self) -> None:
        if self._listener is None:
            self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self._listener.daemon = True
            self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _ignored(self, injected: bool) -> bool:
        return injected or time.monotonic() < self._suppress_until

    def _press(self, key, injected: bool = False) -> None:
        if self._ignored(injected):
            return
        if key == keyboard.Key.esc and self.on_escape:
            self.on_escape()
        with self._lock:
            self.detector.press(key in _CTRL, time.monotonic())

    def _release(self, key, injected: bool = False) -> None:
        if self._ignored(injected):
            return
        with self._lock:
            fired = self.detector.release(key in _CTRL, time.monotonic())
        if fired and self.enabled:
            try:
                self.on_trigger()
            except Exception:  # noqa: BLE001 — never kill the listener thread
                log.exception("hotkey callback failed")

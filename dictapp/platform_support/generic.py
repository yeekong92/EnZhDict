"""Placeholder for non-Windows platforms (macOS/Linux support is future work)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def copy_selection(timeout: float = 0.5) -> str | None:
    log.warning("Selected-text capture is not implemented on this platform yet")
    return None


def set_start_with_os(enabled: bool) -> None:
    log.warning("Start-with-OS is not implemented on this platform yet")


def is_start_with_os() -> bool:
    return False


def make_noactivate(win_id: int) -> None:
    pass

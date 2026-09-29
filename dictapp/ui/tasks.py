"""Run blocking work on a thread pool and deliver results on the UI thread."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)
_pool = QThreadPool.globalInstance()
_pool.setMaxThreadCount(max(6, _pool.maxThreadCount()))
_live: set["_Signals"] = set()  # keep signal objects alive until delivery


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn, args, kwargs, signals):
        super().__init__()
        self.fn, self.args, self.kwargs, self.signals = fn, args, kwargs, signals

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001 — report every failure to the caller
            log.debug("background job failed:\n%s", traceback.format_exc())
            self._emit(self.signals.failed, e)
        else:
            self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            pass  # receiver already destroyed (e.g. app shutting down)


def run_async(fn: Callable[..., Any], *args,
              on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None, **kwargs) -> None:
    """Run ``fn(*args, **kwargs)`` off the UI thread; callbacks run on the UI thread."""
    signals = _Signals()
    _live.add(signals)

    def finish(cb, value):
        _live.discard(signals)
        signals.deleteLater()
        if cb:
            try:
                cb(value)
            except Exception:  # noqa: BLE001 — a UI callback error must not kill the app
                log.exception("callback failed")

    signals.done.connect(lambda v: finish(on_done, v))
    signals.failed.connect(lambda e: finish(on_error, e))
    _pool.start(_Job(fn, args, kwargs, signals))

"""Quick-lookup popup shown near the cursor after a double Ctrl tap."""
from __future__ import annotations

import math
from urllib.parse import unquote

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core.models import LookupResult
from ..platform_support import make_noactivate
from . import render
from .context import AppContext, LookupSession

GRACE_MS = 700          # ignore "mouse away" right after showing
AWAY_PX_BEFORE = 320    # never entered: close once the cursor is this far away
AWAY_PX_AFTER = 48      # entered then left: close once this far outside...
AWAY_MS_AFTER = 450     # ...for this long


class _MouseBridge(QObject):
    clicked = Signal()


class Popup(QWidget):
    open_in_main = Signal(str)

    def __init__(self, ctx: AppContext):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.ctx = ctx
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.session = LookupSession(ctx, self)
        self.session.updated.connect(self._render)
        self.session.busy.connect(self._set_busy)
        self.result: LookupResult | None = None
        self._busy = False
        self._anchor = QPoint()

        frame = QFrame(objectName="popupFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.setSpacing(6)

        self.body = QLabel()
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.TextSelectableByMouse)
        self.body.linkActivated.connect(self._link)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        lay.addWidget(self.scroll, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.save_btn = QPushButton("☆ Save")
        self.save_btn.setFocusPolicy(Qt.NoFocus)
        self.save_btn.clicked.connect(self._save)
        more = QPushButton("More ↗")
        more.setToolTip("Open in the main window")
        more.setFocusPolicy(Qt.NoFocus)
        more.clicked.connect(self._open_main)
        self.status = QLabel()
        self.status.setObjectName("status")
        close = QPushButton("✕")
        close.setFlat(True)
        close.setFixedWidth(28)
        close.setFocusPolicy(Qt.NoFocus)
        close.clicked.connect(self.hide)
        row.addWidget(self.save_btn)
        row.addWidget(more)
        row.addWidget(self.status, 1)
        row.addWidget(close)
        lay.addLayout(row)
        self._apply_style()
        ctx.settings_changed.connect(self._apply_style)
        ctx.saved_changed.connect(self._refresh_save)

        # Auto-close when the mouse wanders away.
        self._watch = QTimer(self, interval=100)
        self._watch.timeout.connect(self._check_mouse)
        self._shown_at = 0
        self._entered = False
        self._away_ms = 0
        self._mouse_listener = None
        self._bridge = _MouseBridge()
        self._bridge.clicked.connect(self._global_click)

        self.winId()  # create the native window now so we can set its styles
        make_noactivate(int(self.winId()))

    # ------------------------------------------------------------ public
    def show_for(self, text: str) -> None:
        self._anchor = QCursor.pos()
        res = self.session.start(text)
        self._render(res)
        self._entered = False
        self._away_ms = 0
        self._shown_at = 0
        self.show()
        self.raise_()
        self._watch.start()
        self._start_mouse_listener()
        if self.ctx.settings.auto_play and res.kind == "word" and res.found:
            self._play("zh" if res.language == "zh" else self.ctx.settings.default_accent)

    def show_message(self, text: str) -> None:
        self._anchor = QCursor.pos()
        self.result = None
        self.body.setText(f"<span>{text}</span>")
        self.save_btn.setEnabled(False)
        self.status.clear()
        self._resize_and_place()
        self.show()
        self._watch.start()
        self._start_mouse_listener()
        QTimer.singleShot(2500, self.hide)

    def hideEvent(self, event) -> None:
        self._watch.stop()
        self._stop_mouse_listener()
        super().hideEvent(event)

    # ----------------------------------------------------------- render
    def _apply_style(self) -> None:
        s = self.ctx.settings
        f = QFont(self.font())
        f.setPointSize(s.font_size)
        self.body.setFont(f)
        pal = self.palette()
        dark = pal.window().color().lightness() < 128
        bg = "#202124" if dark else "#ffffff"
        border = "#5f6368" if dark else "#c8ccd0"
        self.setStyleSheet(
            f"#popupFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
            f"QLabel#status {{ color: #9aa0a6; font-size: 8pt; }}")
        self.setFixedWidth(s.popup_width + 12)
        if self.result:
            self._render(self.result)

    def _render(self, res: LookupResult) -> None:
        self.result = res
        self.body.setText(render.popup_html(res, self._busy))
        self.save_btn.setEnabled(bool(res.query))
        self._refresh_save()
        self._resize_and_place()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.status.setText("loading…" if busy else "")
        if not busy and self.result:
            if self.result.online_error and self.ctx.settings.online_lookups:
                self.status.setText("offline")
                self.status.setToolTip(self.result.online_error)
            self._render(self.result)

    def _refresh_save(self) -> None:
        saved = bool(self.result and self.result.query and self.ctx.saved_entry(self.result))
        self.save_btn.setText("★ Saved" if saved else "☆ Save")

    def _resize_and_place(self) -> None:
        width = self.ctx.settings.popup_width
        inner_w = width - 24 - 4
        content_h = self.body.heightForWidth(inner_w)
        if content_h < 0:
            content_h = self.body.sizeHint().height()
        chrome = 12 + 10 + 8 + 6 + self.save_btn.sizeHint().height()
        h = min(self.ctx.settings.popup_max_height, content_h + chrome + 8)
        self.resize(width + 12, h)
        self._place()

    def _place(self) -> None:
        pos = self._anchor
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area: QRect = screen.availableGeometry()
        w, h = self.width(), self.height()
        x, y = pos.x() + 14, pos.y() + 18
        if x + w > area.right():
            x = pos.x() - w - 6
        if y + h > area.bottom():
            y = pos.y() - h - 10
        x = max(area.left(), min(x, area.right() - w + 1))
        y = max(area.top(), min(y, area.bottom() - h + 1))
        self.move(x, y)

    # --------------------------------------------------------- actions
    def _link(self, href: str) -> None:
        if href.startswith("play:"):
            self._play(href[5:])
        elif href.startswith("lookup:"):
            self.show_for(unquote(href[7:]))

    def _play(self, accent: str) -> None:
        res = self.result
        if res:
            url = {"uk": res.audio_uk, "us": res.audio_us}.get(accent, "")
            self.ctx.audio.play(res.headword or res.query, accent, url)

    def _save(self) -> None:
        if self.result and self.result.query:
            _, created = self.ctx.save_result(self.result)
            self.status.setText("saved ✓" if created else "already saved")

    def _open_main(self) -> None:
        if self.result:
            self.open_in_main.emit(self.result.query)
        self.hide()

    # ------------------------------------------------- auto-close logic
    def _distance_outside(self, p: QPoint) -> float:
        g = self.frameGeometry()
        dx = max(g.left() - p.x(), 0, p.x() - g.right())
        dy = max(g.top() - p.y(), 0, p.y() - g.bottom())
        return math.hypot(dx, dy)

    def _check_mouse(self) -> None:
        self._shown_at += self._watch.interval()
        d = self._distance_outside(QCursor.pos())
        if d == 0:
            self._entered = True
            self._away_ms = 0
            return
        if self._shown_at < GRACE_MS:
            return
        if not self._entered:
            if d > AWAY_PX_BEFORE:
                self.hide()
        elif d > AWAY_PX_AFTER:
            self._away_ms += self._watch.interval()
            if self._away_ms >= AWAY_MS_AFTER:
                self.hide()
        else:
            self._away_ms = 0

    def _start_mouse_listener(self) -> None:
        if self._mouse_listener is not None:
            return
        from pynput import mouse

        def on_click(_x, _y, _button, pressed, *_rest):
            if pressed:
                self._bridge.clicked.emit()  # queued to the UI thread

        self._mouse_listener = mouse.Listener(on_click=on_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

    def _stop_mouse_listener(self) -> None:
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    def _global_click(self) -> None:
        # Use Qt's cursor position (logical pixels) rather than pynput's physical ones.
        if self.isVisible() and self._distance_outside(QCursor.pos()) > 0:
            self.hide()

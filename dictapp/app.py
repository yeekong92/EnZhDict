"""Application wiring: tray icon, global hotkey -> popup, single instance."""
from __future__ import annotations

import getpass
import logging
import sys

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import platform_support as plat
from .config import Settings, app_data_dir, bundle_dir
from .ui.context import AppContext
from .ui.main_window import MainWindow
from .ui.popup import Popup
from .ui.settings_dialog import SettingsDialog
from .ui.tasks import run_async

log = logging.getLogger(__name__)
SERVER_NAME = f"EnZhDict-{getpass.getuser()}"


def make_icon(size: int = 256) -> QIcon:
    png = bundle_dir() / "resources" / "icon.png"
    if png.exists():
        return QIcon(str(png))
    return QIcon(draw_icon_pixmap(size))


def draw_icon_pixmap(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), size * 0.2, size * 0.2)
    p.fillPath(path, QColor("#1a64c8"))
    p.setPen(QColor("white"))
    f = QFont(["Microsoft YaHei UI", "Microsoft YaHei", "SimHei"])
    f.setPixelSize(int(size * 0.58))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, -size * 0.03, size, size), Qt.AlignCenter, "词")
    p.end()
    return pm


class _HotkeyBridge(QObject):
    """Signals emitted from the pynput thread, delivered on the UI thread."""
    triggered = Signal()
    escape = Signal()


class DictionaryApp(QObject):
    def __init__(self, qapp: QApplication, start_hidden: bool = False):
        super().__init__()
        self.qapp = qapp
        self.ctx = AppContext(Settings.load())
        self.icon = make_icon()
        qapp.setWindowIcon(self.icon)

        self.window = MainWindow(self.ctx)
        self.popup = Popup(self.ctx)
        self.popup.open_in_main.connect(self.window.lookup_and_show)
        self.ctx.settings_changed.connect(self._on_settings_changed)
        self.ctx.saved_changed.connect(self._update_tray_tooltip)

        self._grabbing = False
        self.bridge = _HotkeyBridge()
        self.bridge.triggered.connect(self._on_hotkey)
        self.bridge.escape.connect(self._on_escape)
        self.hotkey = plat.DoubleTapListener(self.bridge.triggered.emit,
                                             self.ctx.settings.hotkey_interval_ms,
                                             on_escape=self.bridge.escape.emit)
        self.hotkey.enabled = self.ctx.settings.hotkey_enabled
        self.hotkey.start()

        self._build_tray()
        self._sync_startup_setting()
        if not start_hidden:
            self.window.show()
        elif QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.showMessage("EnZhDict is running",
                                  "Select text anywhere and press Ctrl twice to look it up.",
                                  self.icon, 4000)
        if self.ctx.dict_error:
            QMessageBox.warning(self.window, "Dictionary missing", self.ctx.dict_error)

    # ------------------------------------------------------------- tray
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon, self)
        menu = QMenu()
        act_open = QAction("Open", menu, triggered=self.window.show_and_raise)
        menu.addAction(act_open)
        menu.setDefaultAction(act_open)
        self.act_pause = QAction("Pause hotkey", menu, checkable=True)
        self.act_pause.setChecked(not self.ctx.settings.hotkey_enabled)
        self.act_pause.toggled.connect(self._set_paused)
        menu.addAction(self.act_pause)
        menu.addAction(QAction("Settings…", menu, triggered=self.open_settings))
        menu.addSeparator()
        menu.addAction(QAction("Quit", menu, triggered=self.quit))
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self._update_tray_tooltip()
        self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.show_and_raise()

    def _update_tray_tooltip(self) -> None:
        due = self.ctx.srs.due_count()
        paused = " (hotkey paused)" if not self.hotkey.enabled else ""
        self.tray.setToolTip(f"EnZhDict{paused}\n{due} card(s) due today")

    def _set_paused(self, paused: bool) -> None:
        self.hotkey.enabled = not paused
        self.ctx.settings.hotkey_enabled = not paused
        self.ctx.settings.save()
        self._update_tray_tooltip()

    # --------------------------------------------------------- settings
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.ctx.settings, self.window if self.window.isVisible() else None)
        if dlg.exec():
            self.ctx.apply_settings(dlg.result_settings())

    def _on_settings_changed(self) -> None:
        self.hotkey.set_interval(self.ctx.settings.hotkey_interval_ms)
        self._sync_startup_setting()

    def _sync_startup_setting(self) -> None:
        want = self.ctx.settings.start_with_windows
        try:
            if want or plat.is_start_with_os():
                plat.set_start_with_os(want)  # also refreshes the path if the app moved
        except OSError as e:
            self.ctx.status.emit(f"Couldn't update start-up setting: {e}")

    # ----------------------------------------------------------- hotkey
    def _on_hotkey(self) -> None:
        if self._grabbing:
            return
        self._grabbing = True
        self.hotkey.suppress(0.6)

        def done(text):
            self._grabbing = False
            if text and text.strip():
                self.popup.show_for(text[:2000])
            else:
                self.popup.show_message("Select some text first, then press Ctrl twice.")

        def failed(err):
            self._grabbing = False
            log.warning("copy_selection failed: %s", err)

        run_async(plat.copy_selection, on_done=done, on_error=failed)

    def _on_escape(self) -> None:
        if self.popup.isVisible():
            self.popup.hide()

    # -------------------------------------------------------------- misc
    def activate_from_other_instance(self) -> None:
        self.window.show_and_raise()

    def quit(self) -> None:
        self.hotkey.stop()
        self.popup.hide()
        self.tray.hide()
        self.qapp.quit()


def _single_instance(qapp: QApplication) -> QLocalServer | None:
    """Return a server if we're the first instance; otherwise ping it and return None."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer(qapp)
    server.listen(SERVER_NAME)
    return server


def run(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(app_data_dir() / "enzhdict.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stderr)] if sys.stderr else
                 [logging.FileHandler(app_data_dir() / "enzhdict.log", encoding="utf-8")],
    )
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(argv)
    qapp.setApplicationName("EnZhDict")
    qapp.setQuitOnLastWindowClosed(False)
    font = qapp.font()
    font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "SimSun"])
    qapp.setFont(font)

    server = _single_instance(qapp)
    if server is None:
        return 0
    app = DictionaryApp(qapp, start_hidden="--tray" in argv)

    def on_connection():
        conn = server.nextPendingConnection()
        conn.readyRead.connect(lambda: (conn.readAll(), app.activate_from_other_instance()))

    server.newConnection.connect(on_connection)
    if "--selftest" in argv:
        QTimer.singleShot(0, lambda: _selftest(app))
    return qapp.exec()


def _selftest(app: DictionaryApp) -> None:
    """Smoke test for packaged builds: `EnZhDict.exe --selftest`, then read enzhdict.log."""
    import time
    t = time.perf_counter()
    res = app.ctx.offline_lookup("ubiquitous")
    log.info("selftest: lookup found=%s zh=%s in %.1f ms", res.found, res.short_zh(1),
             (time.perf_counter() - t) * 1000)
    res = app.ctx.offline_lookup("kumain")
    log.info("selftest: tagalog lookup lang=%s found=%s en=%s", res.language, res.found, res.short_en(1))
    from PySide6.QtMultimedia import QMediaDevices
    log.info("selftest: audio outputs=%s", [d.description() for d in QMediaDevices.audioOutputs()])
    try:
        from PySide6.QtTextToSpeech import QTextToSpeech
        log.info("selftest: tts engines=%s", QTextToSpeech.availableEngines())
    except ImportError as e:
        log.error("selftest: QtTextToSpeech missing: %s", e)
    app.popup.show_for("serendipity")
    QTimer.singleShot(1500, lambda: (log.info("selftest: popup visible=%s", app.popup.isVisible()),
                                     log.info("selftest: done"), app.quit()))

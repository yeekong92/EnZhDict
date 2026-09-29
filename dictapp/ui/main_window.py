"""Main window: search bar, result header, and the English / Chinese / Examples / Saved tabs."""
from __future__ import annotations

from urllib.parse import unquote

from PySide6.QtCore import QStringListModel, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from ..core.models import LookupResult
from . import render
from .context import AppContext, LookupSession
from .saved_tab import SavedTab

TAB_EN, TAB_ZH, TAB_TL, TAB_EX, TAB_SAVED = range(5)
LANG_CHOICES = [("Auto", None), ("EN", "en"), ("中文", "zh"), ("TL", "tl")]


class ResultView(QTextBrowser):
    """QTextBrowser that routes play:/lookup: links to callbacks."""

    def __init__(self, on_link, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(lambda url: on_link(url.toString()))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("EnZhDict — English ⇄ 中文")
        self.resize(760, 640)
        self.session = LookupSession(ctx, self)
        self.session.updated.connect(self._show_result)
        self.session.busy.connect(self._set_busy)
        self.result: LookupResult | None = None
        self._busy = False
        self._history: list[tuple[str, str | None]] = []  # (text, forced language)
        self._hist_pos = -1
        self._translating_for = ""

        # --- search bar
        self.back_btn = QToolButton(text="◀", toolTip="Back (Alt+Left)", autoRaise=True)
        self.fwd_btn = QToolButton(text="▶", toolTip="Forward (Alt+Right)", autoRaise=True)
        self.back_btn.clicked.connect(lambda: self._step_history(-1))
        self.fwd_btn.clicked.connect(lambda: self._step_history(1))
        self.search = QLineEdit(placeholderText="Type an English or Chinese word or sentence…  (Ctrl+L)")
        self.search.setClearButtonEnabled(True)
        f = self.search.font()
        f.setPointSize(f.pointSize() + 3)
        self.search.setFont(f)
        self.search.setMinimumHeight(34)
        self.search.returnPressed.connect(lambda: self.lookup(self.search.text()))
        self._completion = QStringListModel(self)
        completer = QCompleter(self._completion, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.activated.connect(self.lookup)
        self.search.setCompleter(completer)
        self._suggest_timer = QTimer(self, singleShot=True, interval=120)
        self._suggest_timer.timeout.connect(self._update_suggestions)
        self.search.textEdited.connect(lambda _: self._suggest_timer.start())
        go = QPushButton("Look up")
        go.clicked.connect(lambda: self.lookup(self.search.text()))
        self.save_btn = QPushButton("☆ Save")
        self.save_btn.setToolTip("Save to your list (Ctrl+S)")
        self.save_btn.clicked.connect(self.save_current)
        self.save_btn.setEnabled(False)
        bar = QHBoxLayout()
        for w in (self.back_btn, self.fwd_btn):
            bar.addWidget(w)
        bar.addWidget(self.search, 1)
        self.lang_box = QComboBox()
        self.lang_box.setToolTip("Input language: Auto-detect, or force English / Chinese / Tagalog")
        for label, code in LANG_CHOICES:
            self.lang_box.addItem(label, code)
        self.lang_box.currentIndexChanged.connect(self._lang_changed)
        bar.addWidget(self.lang_box)
        bar.addWidget(go)
        bar.addWidget(self.save_btn)

        # --- header (headword, phonetics)
        self.header = QLabel()
        self.header.setTextFormat(Qt.RichText)
        self.header.setWordWrap(True)
        self.header.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.header.setOpenExternalLinks(False)
        self.header.linkActivated.connect(self._link)
        self.header.setContentsMargins(4, 6, 4, 2)

        # --- tabs
        self.tabs = QTabWidget()
        self.en_view = ResultView(self._link)
        self.zh_view = ResultView(self._link)
        self.tl_view = ResultView(self._link)
        self.ex_view = ResultView(self._link)
        ex_page = QWidget()
        ex_lay = QVBoxLayout(ex_page)
        ex_lay.setContentsMargins(0, 4, 0, 0)
        self.ex_toggle = QCheckBox("Show translations")
        self.ex_toggle.setChecked(ctx.settings.show_example_translations)
        self.ex_toggle.toggled.connect(self._toggle_example_zh)
        ex_lay.addWidget(self.ex_toggle)
        ex_lay.addWidget(self.ex_view, 1)
        self.saved = SavedTab(ctx)
        self.saved.lookup_requested.connect(self.lookup_and_show)
        self.saved.due_count_changed.connect(self._update_due)
        self.tabs.addTab(self.en_view, "English")
        self.tabs.addTab(self.zh_view, "中文 Chinese")
        self.tabs.addTab(self.tl_view, "Tagalog")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.addTab(ex_page, "Examples")
        self.tabs.addTab(self.saved, "Saved")
        self._update_due(ctx.srs.due_count())

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(bar)
        lay.addWidget(self.header)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.statusBar()
        ctx.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        ctx.saved_changed.connect(self._refresh_save_button)
        ctx.settings_changed.connect(self.apply_font)

        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_search)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_current)
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self._step_history(-1))
        QShortcut(QKeySequence("Alt+Right"), self, activated=lambda: self._step_history(1))
        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        self._update_history_buttons()
        self.apply_font()
        self._show_welcome()

    # ------------------------------------------------------------ public
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def lookup_and_show(self, text: str, lang: str | None = None) -> None:
        self.show_and_raise()
        self.lookup(text, lang=lang)

    def lookup(self, text: str, record: bool = True, lang: str | None = None) -> None:
        """Look up `text`. `lang` forces a language for this lookup only; otherwise
        the EN/中文/TL selector applies (Auto = detect)."""
        text = text.strip()
        if not text:
            return
        lang = lang or self.lang_box.currentData()
        if self.ctx.dict_error:
            self.statusBar().showMessage(self.ctx.dict_error)
        if record:
            del self._history[self._hist_pos + 1:]
            if not self._history or self._history[-1] != (text, lang):
                self._history.append((text, lang))
            self._hist_pos = len(self._history) - 1
            self._update_history_buttons()
        self.search.setText(text)
        self.search.completer().popup().hide()
        if self.tabs.currentIndex() == TAB_SAVED:
            self.tabs.setCurrentIndex(TAB_EN)
        res = self.session.start(text, lang)
        tab = self.tabs.currentIndex()
        if res.kind == "sentence" and tab == TAB_EN and res.language in ("zh", "en"):
            self.tabs.setCurrentIndex(TAB_ZH)
        elif res.language == "tl" and tab == TAB_ZH:
            self.tabs.setCurrentIndex(TAB_EN)
        elif res.language == "zh" and tab == TAB_TL:
            self.tabs.setCurrentIndex(TAB_ZH)
        if self.tabs.currentIndex() == TAB_TL:
            self.session.ensure_tagalog()
        if res.found and res.kind == "word" and self.ctx.settings.auto_play:
            self._play(res.language if res.language in ("zh", "tl") else self.ctx.settings.default_accent)

    def save_current(self) -> None:
        res = self.result
        if not res or not res.query:
            return
        eid, created = self.ctx.save_result(res)
        self.statusBar().showMessage(("Saved “%s”" if created else "“%s” is already saved")
                                     % self.ctx.save_text(res)[:50], 4000)

    def apply_font(self) -> None:
        f = QFont(self.font())
        f.setPointSize(self.ctx.settings.font_size)
        for v in (self.en_view, self.zh_view, self.tl_view, self.ex_view):
            v.setFont(f)
        if self.result:
            self._show_result(self.result)

    # ----------------------------------------------------------- private
    def _show_welcome(self) -> None:
        c = render.colors()
        msg = ("<h3>Welcome!</h3><p>Type a word or sentence above and press Enter.</p>"
               "<p>Anywhere in Windows: <b>select text, then press Ctrl twice quickly</b> "
               "for a quick-lookup popup.</p>")
        if self.ctx.dict_error:
            msg += f"<p style='color:{c['pos']}'>{self.ctx.dict_error}</p>"
        self.en_view.setHtml(msg)

    def _show_result(self, res: LookupResult) -> None:
        self.result = res
        pending = self._busy
        self.header.setText(render.header_html(res))
        self.en_view.setHtml(render.english_html(res, pending))
        self.zh_view.setHtml(render.chinese_html(res, pending))
        self.tl_view.setHtml(render.tagalog_html(res, pending))
        self.ex_view.setHtml(render.examples_html(res, self.ex_toggle.isChecked(), pending))
        self.tabs.setTabText(TAB_EX, f"Examples ({len(res.examples)})" if res.examples else "Examples")
        self.save_btn.setEnabled(bool(res.query))
        self._refresh_save_button()
        if not pending and res.online_error and self.ctx.settings.online_lookups:
            self.statusBar().showMessage(f"Offline results only — {res.online_error}", 8000)
        if self.ex_toggle.isChecked() and not pending and any(not e.zh for e in res.examples[:6]):
            self._toggle_example_zh(True)

    def _set_busy(self, busy: bool) -> None:
        was_busy, self._busy = self._busy, busy
        if busy:
            self.statusBar().showMessage("Loading online data…")
            if not was_busy and self.result:
                self._show_result(self.result)  # swap "No translation" for "Translating…"
        else:
            if self.statusBar().currentMessage() == "Loading online data…":
                self.statusBar().clearMessage()
            if self.result:
                self._show_result(self.result)

    def _refresh_save_button(self) -> None:
        saved = bool(self.result and self.result.query and self.ctx.saved_entry(self.result))
        self.save_btn.setText("★ Saved" if saved else "☆ Save")

    def _toggle_example_zh(self, on: bool) -> None:
        if self.ctx.settings.show_example_translations != on:
            self.ctx.settings.show_example_translations = on
            self.ctx.settings.save()
        if on and self.result and any(not e.zh for e in self.result.examples):
            if self.ctx.settings.online_lookups and self._translating_for != self.result.query:
                self._translating_for = self.result.query
                self.session.translate_examples()
        if self.result:
            self.ex_view.setHtml(render.examples_html(self.result, on, self._busy))

    def _update_suggestions(self) -> None:
        if self.ctx.dictionary is None:
            return
        text = self.search.text()
        lang = self.lang_box.currentData()
        self._completion.setStringList(
            self.ctx.dictionary.suggest(text, lang=lang) if len(text.strip()) >= 2 else [])

    def _lang_changed(self) -> None:
        if self.search.text().strip():
            self.lookup(self.search.text())

    def _tab_changed(self, index: int) -> None:
        if index == TAB_TL:
            self.session.ensure_tagalog()

    def _update_due(self, n: int) -> None:
        self.tabs.setTabText(TAB_SAVED, f"Saved · {n} due" if n else "Saved")

    def _link(self, href: str) -> None:
        if href.startswith("play:"):
            self._play(href[5:])
        elif href.startswith("lookup:"):
            self.lookup(unquote(href[7:]))
        elif href.startswith("lookupas:"):
            lang, _, word = href[9:].partition(":")
            self.lookup(unquote(word), lang=lang)
        elif href.startswith("http"):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(href))

    def _play(self, accent: str) -> None:
        res = self.result
        if not res:
            return
        text = res.headword or res.query
        url = {"uk": res.audio_uk, "us": res.audio_us}.get(accent, "")
        self.ctx.audio.play(text, accent, url)

    def _step_history(self, step: int) -> None:
        pos = self._hist_pos + step
        if 0 <= pos < len(self._history):
            self._hist_pos = pos
            self._update_history_buttons()
            text, lang = self._history[pos]
            self.lookup(text, record=False, lang=lang)

    def _update_history_buttons(self) -> None:
        self.back_btn.setEnabled(self._hist_pos > 0)
        self.fwd_btn.setEnabled(self._hist_pos < len(self._history) - 1)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Closing the window just hides it; the app keeps running in the tray.
        event.ignore()
        self.hide()

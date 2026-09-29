"""Saved items table (search / sort / inline notes / edit / delete / CSV) and review mode."""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget,
    QTableView, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core.srs import end_of_today
from ..data.userdb import Entry, parse_iso, utcnow
from . import render
from .context import AppContext

COLUMNS = ["Item", "Type", "Meaning / Translation", "Note", "Date added", "Next review"]
NOTE_COL = 3
LANG_SHORT = {"en": "EN", "zh": "中文", "tl": "TL"}


def _local(dt_iso: str | None) -> datetime | None:
    dt = parse_iso(dt_iso)
    return dt.astimezone() if dt else None


def due_label(due_iso: str | None) -> str:
    due = _local(due_iso)
    if not due:
        return ""
    today = datetime.now().astimezone().date()
    if due.date() < today:
        return "Overdue"
    if due.date() == today:
        return "Today"
    if due.date() == today + timedelta(days=1):
        return "Tomorrow"
    return due.strftime("%Y-%m-%d")


class EntriesModel(QAbstractTableModel):
    note_edited = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[Entry] = []

    def set_entries(self, entries: list[Entry]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        col = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0:
                return e.text
            if col == 1:
                kind = "Word" if e.type == "word" else "Sentence"
                return f"{kind} · {LANG_SHORT.get(e.language, e.language.upper())}"
            if col == 2:
                return " ".join(e.meaning.split())[:200] if role == Qt.DisplayRole else e.meaning
            if col == 3:
                return e.note
            if col == 4:
                d = _local(e.created_at)
                return d.strftime("%Y-%m-%d") if d else ""
            if col == 5:
                return due_label(e.due)
        if role == Qt.UserRole:  # sort key
            return {4: e.created_at or "", 5: e.due or ""}.get(col, (self.data(index) or "").lower())
        if role == Qt.ToolTipRole and col in (0, 2, 3):
            return {0: e.text, 2: e.meaning, 3: e.note or "Double-click to add a note"}[col]
        if role == Qt.ForegroundRole and col == 5 and e.due:
            due = parse_iso(e.due)
            if due and due <= end_of_today():
                return QColor("#d93025")
        return None

    def flags(self, index):
        f = super().flags(index)
        if index.column() == NOTE_COL:
            f |= Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or index.column() != NOTE_COL:
            return False
        e = self.entries[index.row()]
        e.note = str(value)
        self.note_edited.emit(e.id, e.note)
        self.dataChanged.emit(index, index)
        return True


class EditEntryDialog(QDialog):
    def __init__(self, entry: Entry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit — {entry.text[:40]}")
        self.resize(520, 480)
        form = QFormLayout(self)
        self.fields: dict[str, QLineEdit | QPlainTextEdit] = {}

        def add(name, label, multiline=False):
            w = QPlainTextEdit(getattr(entry, name)) if multiline else QLineEdit(getattr(entry, name))
            if multiline:
                w.setTabChangesFocus(True)
            self.fields[name] = w
            form.addRow(label, w)

        add("text", "Item")
        if entry.type == "word":
            if entry.language == "en":
                add("phonetic_uk", "UK IPA")
                add("phonetic_us", "US IPA")
                add("meaning_zh", "Chinese meaning", True)
                add("meaning_en", "English meaning", True)
                add("meaning_tl", "Tagalog meaning", True)
            elif entry.language == "tl":
                add("phonetic_uk", "IPA")
                add("meaning_en", "English meaning", True)
            else:
                add("pinyin", "Pinyin")
                add("meaning_en", "English meaning", True)
        else:
            add("translation", "Translation", True)
        add("note", "Note", True)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {k: (w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()).strip()
                for k, w in self.fields.items()}


class ReviewWidget(QWidget):
    finished = Signal()

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.queue: list[int] = []
        self.done_count = 0
        self.current: Entry | None = None

        top = QHBoxLayout()
        self.progress = QLabel()
        back = QPushButton("← Back to list")
        back.clicked.connect(self.finished)
        top.addWidget(self.progress)
        top.addStretch()
        top.addWidget(back)

        self.card = QTextBrowser()
        self.card.setOpenLinks(False)
        self.card.anchorClicked.connect(self._link)

        self.show_btn = QPushButton("Show answer  (Space)")
        self.show_btn.setMinimumHeight(40)
        self.show_btn.clicked.connect(self.reveal)
        self.rate_row = QWidget()
        rr = QHBoxLayout(self.rate_row)
        rr.setContentsMargins(0, 0, 0, 0)
        self.rate_buttons: dict[str, QPushButton] = {}
        for i, (name, color) in enumerate((("again", "#d93025"), ("hard", "#e37400"),
                                           ("good", "#188038"), ("easy", "#1a73e8")), 1):
            b = QPushButton()
            b.setMinimumHeight(44)
            b.setStyleSheet(f"QPushButton {{ color: {color}; font-weight: 600; }}")
            b.clicked.connect(lambda _=False, n=name: self.rate(n))
            rr.addWidget(b)
            self.rate_buttons[name] = b
            QShortcut(QKeySequence(str(i)), self, activated=lambda n=name: self._key_rate(n),
                      context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.reveal,
                  context=Qt.WidgetWithChildrenShortcut)
        self.setFocusPolicy(Qt.StrongFocus)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.card, 1)
        lay.addWidget(self.show_btn)
        lay.addWidget(self.rate_row)

    def start(self) -> bool:
        self.queue = self.ctx.srs.due_ids()
        self.done_count = 0
        if not self.queue:
            return False
        self._next()
        return True

    def _next(self) -> None:
        self.current = None
        while self.queue and self.current is None:
            self.current = self.ctx.db.get_entry(self.queue.pop(0))
        if self.current is None:
            self.card.setHtml(f"<div align='center' style='margin-top:80px'>"
                              f"<p style='font-size:22pt'>🎉 All done!</p>"
                              f"<p>You reviewed {self.done_count} card(s). Come back tomorrow.</p></div>")
            self.show_btn.hide()
            self.rate_row.hide()
            self.progress.setText("")
            self.ctx.saved_changed.emit()
            return
        self.progress.setText(f"Reviewed {self.done_count} · {len(self.queue) + 1} left")
        self.show_btn.show()
        self.rate_row.hide()
        self._render(back=False)
        if self.current.type == "word" and self.ctx.settings.auto_play:
            self._play(self._accent())

    def _accent(self) -> str:
        lang = self.current.language
        return lang if lang in ("zh", "tl") else self.ctx.settings.default_accent

    def _render(self, back: bool) -> None:
        e = self.current
        c = render.colors()
        size = "26pt" if e.type == "word" else "16pt"
        html = [f"<div align='center' style='margin-top:30px'><p style='font-size:{size};font-weight:600'>"
                f"{escape(e.text)}</p>"]
        if e.language == "tl":
            html.append(f"<p style='color:{c['tl']}'>Tagalog</p>")
        if back:
            if e.type == "word" and e.language == "en":
                ph = []
                for acc in ("uk", "us"):
                    ipa = getattr(e, f"phonetic_{acc}")
                    ph.append(f'<a href="play:{acc}" style="color:{c["accent"]};text-decoration:none">'
                              f'🔊 {acc.upper()}</a> <span style="color:{c["muted"]}">'
                              f'{"/" + escape(ipa) + "/" if ipa else ""}</span>')
                html.append("<p>" + "&nbsp;&nbsp;&nbsp;".join(ph) + "</p>")
            elif e.type == "word" and e.language == "tl":
                ipa = f"/{escape(e.phonetic_uk)}/" if e.phonetic_uk else ""
                html.append(f'<p><a href="play:tl" style="color:{c["accent"]};text-decoration:none">🔊</a> '
                            f'<span style="color:{c["muted"]}">{ipa}</span></p>')
            elif e.type == "word":
                html.append(f'<p><a href="play:zh" style="color:{c["accent"]};text-decoration:none">🔊</a> '
                            f'<span style="color:{c["zh"]};font-size:14pt">{escape(e.pinyin)}</span></p>')
            else:
                acc = self._accent()
                html.append(f'<p><a href="play:{acc}" style="color:{c["accent"]};text-decoration:none">🔊 Listen</a></p>')
            html.append("</div><hr>")
            if e.type == "sentence":
                html.append(f"<p style='font-size:14pt'>{escape(e.translation) or '<i>(no translation saved)</i>'}</p>")
            else:
                if e.meaning_zh:
                    html.append("<p style='font-size:13pt'>" + escape(e.meaning_zh).replace("\n", "<br>") + "</p>")
                if e.meaning_en:
                    style = "font-size:13pt" if e.language == "tl" else f"color:{c['muted']}"
                    html.append(f"<p style='{style}'>" + escape(e.meaning_en).replace("\n", "<br>") + "</p>")
                if e.meaning_tl and self.ctx.settings.show_tagalog:
                    html.append(f"<p><span style='color:{c['muted']}'>Tagalog:</span> "
                                f"<span style='color:{c['tl']}'>{escape(e.meaning_tl)}</span></p>")
            if e.note:
                html.append(f"<p style='background:{c['tag_bg']};padding:6px'>📝 {escape(e.note)}</p>")
        else:
            html.append("</div>")
        self.card.setHtml("".join(html))

    def reveal(self) -> None:
        if not self.current or self.rate_row.isVisible():
            return
        self._render(back=True)
        self.show_btn.hide()
        preview = self.ctx.srs.preview(self.current.id)
        for name, b in self.rate_buttons.items():
            b.setText(f"{name.title()}\n{preview.get(name, '')}")
        self.rate_row.show()
        if self.ctx.settings.auto_play and self.current.type == "sentence":
            self._play(self._accent())

    def _key_rate(self, name: str) -> None:
        if self.rate_row.isVisible():
            self.rate(name)

    def rate(self, name: str) -> None:
        if not self.current:
            return
        due = self.ctx.srs.review(self.current.id, name)
        self.done_count += 1
        if due <= end_of_today() and due - utcnow() < timedelta(hours=1):
            self.queue.append(self.current.id)  # learning step: see it again this session
        self._next()

    def _play(self, accent: str) -> None:
        if self.current:
            self.ctx.audio.play(self.current.text, accent)

    def _link(self, url) -> None:
        s = url.toString()
        if s.startswith("play:"):
            self._play(s[5:])


class SavedTab(QWidget):
    lookup_requested = Signal(str, str)   # text, language
    due_count_changed = Signal(int)

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        # --- list page
        page = QWidget()
        pl = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search saved items, meanings and notes…")
        self.search.setClearButtonEnabled(True)
        self.review_btn = QPushButton("▶ Start Review")
        self.review_btn.setStyleSheet("font-weight:600")
        self.review_btn.clicked.connect(self.start_review)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.review_btn)
        pl.addLayout(bar)

        self.model = EntriesModel(self)
        self.model.note_edited.connect(lambda eid, note: self.ctx.db.update_entry(eid, note=note))
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.UserRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(4, Qt.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for col, w in ((0, 160), (1, 70), (3, 160), (4, 90), (5, 90)):
            self.table.setColumnWidth(col, w)
        self.table.doubleClicked.connect(self._double_clicked)
        QShortcut(QKeySequence.Delete, self.table, activated=self.delete_selected)
        pl.addWidget(self.table, 1)

        btns = QHBoxLayout()
        for label, slot in (("Look up", self._lookup_selected), ("Edit…", self.edit_selected),
                            ("Delete", self.delete_selected), (None, None),
                            ("Import CSV…", self.import_csv), ("Export CSV…", self.export_csv)):
            if label is None:
                btns.addStretch()
                continue
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        self.count_label = QLabel()
        btns.insertWidget(3, self.count_label)
        pl.addLayout(btns)
        self.stack.addWidget(page)

        # --- review page
        self.review = ReviewWidget(ctx)
        self.review.finished.connect(self.show_list)
        self.stack.addWidget(self.review)

        ctx.saved_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.model.set_entries(self.ctx.db.list_entries())
        due = self.ctx.srs.due_count()
        self.count_label.setText(f"{len(self.model.entries)} items · {due} due today")
        self.review_btn.setText(f"▶ Start Review ({due})")
        self.review_btn.setEnabled(due > 0)
        self.due_count_changed.emit(due)

    def _selected(self) -> list[Entry]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.entries[r] for r in sorted(rows)]

    def _double_clicked(self, index) -> None:
        if index.column() != NOTE_COL:
            self.edit_selected()

    def _lookup_selected(self) -> None:
        sel = self._selected()
        if sel:
            self.lookup_requested.emit(sel[0].text, sel[0].language)

    def edit_selected(self) -> None:
        sel = self._selected()
        if not sel:
            return
        dlg = EditEntryDialog(sel[0], self)
        if dlg.exec() == QDialog.Accepted:
            vals = dlg.values()
            if not vals.get("text"):
                QMessageBox.warning(self, "Edit", "The item text can't be empty.")
                return
            try:
                self.ctx.db.update_entry(sel[0].id, **vals)
            except Exception as e:  # noqa: BLE001 — e.g. renaming onto an existing item
                QMessageBox.warning(self, "Edit", f"Couldn't save: {e}")
            self.ctx.saved_changed.emit()

    def delete_selected(self) -> None:
        sel = self._selected()
        if not sel:
            return
        what = f"“{sel[0].text[:40]}”" if len(sel) == 1 else f"{len(sel)} items"
        if QMessageBox.question(self, "Delete", f"Delete {what} and its review history?") == QMessageBox.Yes:
            self.ctx.db.delete_entries([e.id for e in sel])
            self.ctx.saved_changed.emit()

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import saved items", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            added, skipped = self.ctx.db.import_csv(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import failed", f"Couldn't read {path}:\n{e}")
            return
        self.ctx.saved_changed.emit()
        QMessageBox.information(self, "Import", f"Imported {added} item(s); skipped {skipped} "
                                                f"(duplicates or empty rows).")

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export saved items", "saved_words.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            n = self.ctx.db.export_csv(path)
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        self.ctx.status.emit(f"Exported {n} items to {path}")

    def start_review(self) -> None:
        if self.review.start():
            self.stack.setCurrentIndex(1)
            self.review.setFocus()
        else:
            QMessageBox.information(self, "Review", "No cards are due today. 🎉")

    def show_list(self) -> None:
        self.stack.setCurrentIndex(0)
        self.refresh()

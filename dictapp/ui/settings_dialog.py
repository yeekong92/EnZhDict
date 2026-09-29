"""Settings dialog."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox,
)

from ..config import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._orig = settings
        form = QFormLayout(self)

        self.interval = QSpinBox(minimum=150, maximum=1500, singleStep=50, suffix=" ms")
        self.interval.setValue(settings.hotkey_interval_ms)
        self.interval.setToolTip("Maximum time between the two Ctrl presses")
        form.addRow("Double-Ctrl interval", self.interval)

        self.width_ = QSpinBox(minimum=260, maximum=900, singleStep=20, suffix=" px")
        self.width_.setValue(settings.popup_width)
        form.addRow("Popup width", self.width_)
        self.height_ = QSpinBox(minimum=160, maximum=1000, singleStep=20, suffix=" px")
        self.height_.setValue(settings.popup_max_height)
        form.addRow("Popup max height", self.height_)
        self.font_size = QSpinBox(minimum=8, maximum=24, suffix=" pt")
        self.font_size.setValue(settings.font_size)
        form.addRow("Font size", self.font_size)

        self.accent = QComboBox()
        self.accent.addItem("US English", "us")
        self.accent.addItem("UK English", "uk")
        self.accent.setCurrentIndex(0 if settings.default_accent == "us" else 1)
        form.addRow("Default accent", self.accent)

        self.auto_play = QCheckBox("Play pronunciation automatically")
        self.auto_play.setChecked(settings.auto_play)
        form.addRow(self.auto_play)
        self.online = QCheckBox("Use online lookups (audio, extra definitions, examples, translation)")
        self.online.setChecked(settings.online_lookups)
        form.addRow(self.online)
        self.show_tl = QCheckBox("Show Tagalog equivalents for English words (popup and flashcards)")
        self.show_tl.setChecked(settings.show_tagalog)
        form.addRow(self.show_tl)
        self.startup = QCheckBox("Start with Windows (in the tray)")
        self.startup.setChecked(settings.start_with_windows)
        form.addRow(self.startup)
        hint = QLabel("<small>Quick lookup: select text in any app, then press <b>Ctrl</b> twice.</small>")
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_settings(self) -> Settings:
        return replace(
            self._orig,
            hotkey_interval_ms=self.interval.value(),
            popup_width=self.width_.value(),
            popup_max_height=self.height_.value(),
            font_size=self.font_size.value(),
            default_accent=self.accent.currentData(),
            auto_play=self.auto_play.isChecked(),
            online_lookups=self.online.isChecked(),
            start_with_windows=self.startup.isChecked(),
            show_tagalog=self.show_tl.isChecked(),
        )

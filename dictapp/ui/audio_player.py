"""Qt playback for pronunciations (see core/audio.py for how audio is found)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLocale, QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from ..core.audio import resolve_audio
from .tasks import run_async

_LOCALES = {
    "uk": (QLocale.English, QLocale.UnitedKingdom),
    "us": (QLocale.English, QLocale.UnitedStates),
    "zh": (QLocale.Chinese, QLocale.China),
}


class AudioPlayer(QObject):
    """Plays pronunciations; emits `status` with user-facing messages."""
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._out = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._out)
        self._player.errorOccurred.connect(lambda _e, msg: self.status.emit(f"Audio error: {msg}"))
        self._tts = None
        self._voices: list = []
        self._request = 0
        self.online = True

    def play(self, text: str, accent: str = "us", url: str = "") -> None:
        text = text.strip()
        if not text:
            return
        self._request += 1
        req = self._request

        def ok(path: Path):
            if req == self._request:  # ignore results of superseded clicks
                self._player.stop()
                self._player.setSource(QUrl.fromLocalFile(str(path)))
                self._player.play()

        def fail(_err: Exception):
            if req == self._request:
                self._speak_offline(text, accent)

        run_async(resolve_audio, text, accent, url, self.online, on_done=ok, on_error=fail)

    def _speak_offline(self, text: str, accent: str) -> None:
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
        except ImportError:
            self.status.emit("No audio available offline.")
            return
        if self._tts is None:
            self._tts = QTextToSpeech(self)
            for loc in self._tts.availableLocales():
                self._tts.setLocale(loc)
                self._voices += self._tts.availableVoices()
        lang, country = _LOCALES.get(accent, _LOCALES["us"])
        pick = (next((v for v in self._voices
                      if v.locale().language() == lang and v.locale().territory() == country), None)
                or next((v for v in self._voices if v.locale().language() == lang), None))
        if pick is None:
            self.status.emit("No audio: offline, and no matching system voice installed.")
            return
        self._tts.setLocale(pick.locale())
        self._tts.setVoice(pick)
        self.status.emit("Offline: using system voice")
        self._tts.say(text)

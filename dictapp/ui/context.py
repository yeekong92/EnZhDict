"""Shared application services and the lookup controller used by both the
main window and the popup."""
from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QObject, Signal

from ..config import Settings, dict_db_path, user_db_path
from ..core import online
from ..core.lookup import Dictionary, DictionaryUnavailable
from ..core.models import LookupResult
from ..core.srs import SRS
from ..core.translate import TranslationUnavailable, translate
from ..data.userdb import UserDB
from .audio_player import AudioPlayer
from .tasks import run_async

log = logging.getLogger(__name__)


def _friendly(err: Exception) -> str:
    if isinstance(err, (online.OfflineError, TranslationUnavailable)):
        return str(err)
    return f"{type(err).__name__}: {err}"


class AppContext(QObject):
    saved_changed = Signal()        # entries added/removed/edited
    settings_changed = Signal()
    status = Signal(str)            # transient user-facing message

    def __init__(self, settings: Settings | None = None, db: UserDB | None = None,
                 dictionary: Dictionary | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.dict_error = ""
        if dictionary is None:
            try:
                dictionary = Dictionary(dict_db_path())
            except DictionaryUnavailable as e:
                self.dict_error = str(e)
        self.dictionary = dictionary
        self.db = db or UserDB(user_db_path())
        self.srs = SRS(self.db)
        self.audio = AudioPlayer(self)
        self.audio.online = self.settings.online_lookups
        self.audio.status.connect(self.status)

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        settings.save()
        self.audio.online = settings.online_lookups
        self.settings_changed.emit()

    # ------------------------------------------------------------- lookup
    def offline_lookup(self, text: str) -> LookupResult:
        if self.dictionary is None:
            from ..core.langdetect import detect_language, looks_like_single_term, normalize_query
            q = normalize_query(text)
            lang = detect_language(q)
            return LookupResult(query=q, language=lang,
                                kind="word" if looks_like_single_term(q, lang) else "sentence")
        res = self.dictionary.lookup(text)
        if res.language == "zh" and res.kind == "sentence":
            for tok, entry in self.dictionary.segment_zh(res.query):
                if entry:
                    res.gloss.append((tok, entry.pinyin,
                                      next((d for d in entry.definitions if not d.startswith("CL:")), "")))
                else:
                    res.gloss.append((tok, "", ""))
        return res

    # --------------------------------------------------------------- save
    def saved_entry(self, res: LookupResult):
        return self.db.find_entry(self.save_text(res))

    @staticmethod
    def save_text(res: LookupResult) -> str:
        return (res.headword or res.query) if res.kind == "word" and res.found else res.query

    def save_result(self, res: LookupResult) -> tuple[int, bool]:
        kind = "word" if res.kind == "word" and res.found else "sentence"
        if kind == "word":
            eid, created = self.db.add_entry(
                text=self.save_text(res), type="word", language=res.language,
                meaning_en=res.meaning_en_text(), meaning_zh=res.meaning_zh_text(),
                phonetic_uk=res.phonetic_uk, phonetic_us=res.phonetic_us, pinyin=res.pinyin)
        else:
            eid, created = self.db.add_entry(
                text=res.query, type="sentence", language=res.language,
                translation=res.translation)
        self.saved_changed.emit()
        return eid, created

    def backfill_saved(self, res: LookupResult) -> None:
        """Fill empty fields of an already-saved entry with newly arrived data."""
        entry = self.saved_entry(res)
        if not entry:
            return
        fresh = {"phonetic_uk": res.phonetic_uk, "phonetic_us": res.phonetic_us,
                 "translation": res.translation if entry.type == "sentence" else "",
                 "meaning_en": res.meaning_en_text() if entry.type == "word" else ""}
        updates = {k: v for k, v in fresh.items() if v and not getattr(entry, k)}
        if updates:
            self.db.update_entry(entry.id, **updates)
            self.saved_changed.emit()


class LookupSession(QObject):
    """One lookup at a time: offline result first, then online data as it arrives.

    `updated` fires with a fresh LookupResult copy each time more data lands;
    `busy` reports whether background work is still pending.
    """
    updated = Signal(object)
    busy = Signal(bool)

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.result: LookupResult | None = None
        self._token = 0
        self._pending = 0

    def start(self, text: str) -> LookupResult:
        self._token += 1
        token = self._token
        self._pending = 0
        res = self.ctx.offline_lookup(text)
        self.result = res
        self._emit()
        if not res.query:
            return res
        s = self.ctx.settings
        want_translation = res.kind == "sentence" or (res.kind == "word" and not res.found)
        if want_translation and res.language in ("en", "zh"):
            src, tgt = ("en", "zh") if res.language == "en" else ("zh", "en")
            self._job(token, translate, res.query, src, tgt, online=s.online_lookups, db=self.ctx.db,
                      apply=self._apply_translation, fail=self._fail_translation)
        if res.kind == "word" and res.language == "en" and not res.found and self.ctx.dictionary:
            self._job(token, self.ctx.dictionary.did_you_mean, res.query,
                      apply=lambda r, v: setattr(r, "suggestions", v))
        if s.online_lookups and res.kind == "word" and res.language in ("en", "zh"):
            if res.language == "en":
                self._job(token, online.fetch_dictionary_for, copy.deepcopy(res), self.ctx.db,
                          apply=online.apply_dictionary, fail=self._fail_online)
            t2s = self.ctx.dictionary.to_simplified if self.ctx.dictionary else None
            self._job(token, online.fetch_examples, res.headword or res.query, res.language, self.ctx.db,
                      apply=lambda r, v: online.apply_examples(r, v, t2s), fail=self._fail_online)
        return res

    def translate_examples(self, limit: int = 6) -> None:
        """Machine-translate examples that lack a Chinese translation."""
        res = self.result
        if not res:
            return
        token = self._token
        for i, ex in enumerate([e for e in res.examples if not e.zh][:limit]):
            def apply(r, v, en=ex.en):
                for e in r.examples:
                    if e.en == en:
                        e.zh = v[0]
            self._job(token, translate, ex.en, "en", "zh", online=self.ctx.settings.online_lookups,
                      db=self.ctx.db, apply=apply, fail=lambda r, e: None)

    # ------------------------------------------------------------ helpers
    def _job(self, token, fn, *args, apply, fail=None, **kwargs):
        self._pending += 1
        self.busy.emit(True)

        def done(value, ok=True):
            if token != self._token:
                return  # a newer lookup started; drop stale data
            self._pending -= 1
            res = self.result
            try:
                (apply if ok else (fail or (lambda r, e: None)))(res, value)
            except Exception:  # noqa: BLE001
                log.exception("applying background result failed")
            if self._pending == 0:
                res.online_loaded = not res.online_error
                self.busy.emit(False)
            self._emit()
            self.ctx.backfill_saved(res)

        run_async(fn, *args, **kwargs, on_done=done, on_error=lambda e: done(e, ok=False))

    def _emit(self):
        self.updated.emit(copy.deepcopy(self.result))

    @staticmethod
    def _apply_translation(res: LookupResult, value) -> None:
        res.translation, res.translation_engine = value
        res.translation_error = ""

    @staticmethod
    def _fail_translation(res: LookupResult, err: Exception) -> None:
        res.translation_error = _friendly(err)

    @staticmethod
    def _fail_online(res: LookupResult, err: Exception) -> None:
        msg = _friendly(err)
        if msg not in res.online_error:
            res.online_error = "; ".join(filter(None, [res.online_error, msg]))

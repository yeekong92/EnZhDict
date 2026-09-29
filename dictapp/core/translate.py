"""Sentence translation with a fallback chain.

Order: Google (deep-translator) -> MyMemory (deep-translator) -> Argos (offline,
optional install). Results are cached. Blocking: call from a worker thread.
"""
from __future__ import annotations

import logging
import time

from ..data.userdb import UserDB

log = logging.getLogger(__name__)

_DT_CODES = {"en": "en", "zh": "zh-CN"}
_MYMEMORY_CODES = {"en": "en-GB", "zh": "zh-CN"}
_COOLDOWN_S = 600
_cooldown_until: dict[str, float] = {}  # engine -> time it may be retried


class TranslationUnavailable(RuntimeError):
    pass


def _google(text: str, src: str, tgt: str) -> str:
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source=_DT_CODES[src], target=_DT_CODES[tgt]).translate(text)


def _mymemory(text: str, src: str, tgt: str) -> str:
    from deep_translator import MyMemoryTranslator
    out = MyMemoryTranslator(source=_MYMEMORY_CODES[src], target=_MYMEMORY_CODES[tgt]).translate(text)
    if out and "MYMEMORY WARNING" in out.upper():
        raise RuntimeError("MyMemory daily quota reached")
    return out


def _argos(text: str, src: str, tgt: str) -> str:
    try:
        import argostranslate.translate as at
    except ImportError as e:
        raise RuntimeError("argostranslate not installed") from e
    return at.translate(text, src, tgt)


def argos_available(src: str = "en", tgt: str = "zh") -> bool:
    try:
        import argostranslate.translate as at
    except ImportError:
        return False
    langs = {l.code: l for l in at.get_installed_languages()}
    return src in langs and tgt in langs and langs[src].get_translation(langs[tgt]) is not None


def translate(text: str, src: str, tgt: str, *, online: bool = True,
              db: UserDB | None = None) -> tuple[str, str]:
    """Translate `text`; returns (translation, engine name)."""
    text = text.strip()
    if not text:
        return "", ""
    key = f"tr:{src}:{tgt}:{text}"
    if db and (hit := db.cache_get(key)) is not None:
        return hit["text"], hit["engine"]
    engines = ([("Google", _google), ("MyMemory", _mymemory)] if online else []) + [("Argos", _argos)]
    for name, fn in engines:
        if _cooldown_until.get(name, 0) > time.monotonic():
            continue
        try:
            out = (fn(text, src, tgt) or "").strip()
        except Exception as e:  # noqa: BLE001 — any engine failure falls through to the next
            log.info("translator %s failed: %s", name, e)
            if name != "Argos":  # rate-limited / blocked: stop hammering it for a while
                _cooldown_until[name] = time.monotonic() + _COOLDOWN_S
            continue
        if out:
            if db:
                db.cache_set(key, {"text": out, "engine": name})
            return out, name
    if online:
        msg = "Translation unavailable (no internet, and no offline Argos model installed)."
    else:
        msg = "Online lookups are off and no offline Argos model is installed."
    raise TranslationUnavailable(msg)

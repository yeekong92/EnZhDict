"""Online enrichment: Free Dictionary API (definitions, UK/US IPA + audio) and
Tatoeba (bilingual example sentences). Results are cached in the user DB so a
repeat lookup works offline. All functions here are blocking: call them from a
worker thread, never from the UI thread."""
from __future__ import annotations

import copy
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import requests

from ..data.userdb import UserDB
from .models import Example, LookupResult, Sense

FREE_DICT_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{}"
TATOEBA_URL = "https://api.tatoeba.org/unstable/sentences"
TIMEOUT = (4, 15)  # connect, read — dictionaryapi.dev is sometimes slow
CACHE_DAYS = 60
_session = requests.Session()
_session.headers["User-Agent"] = "EnZhDict/1.0 (desktop dictionary)"


class OfflineError(RuntimeError):
    """Network unavailable or service failed."""


def _get_json(url: str, **params):
    try:
        r = _session.get(url, params=params or None, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise OfflineError(f"Network error: {type(e).__name__}") from e
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise OfflineError(f"{url.split('/')[2]} returned HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise OfflineError("Bad response from server") from e


def _audio_accent(url: str) -> str:
    m = re.search(r"-(uk|us|au|ca)\.mp3$", url or "")
    return m.group(1) if m else ""


def parse_free_dictionary(data: list) -> dict:
    out = {"phonetic_uk": "", "phonetic_us": "", "audio_uk": "", "audio_us": "",
           "phonetic": "", "senses": [], "examples": []}
    senses: dict[str, list[str]] = {}
    for entry in data or []:
        if not out["phonetic"] and entry.get("phonetic"):
            out["phonetic"] = entry["phonetic"].strip("/[] ")
        for ph in entry.get("phonetics", []):
            acc = _audio_accent(ph.get("audio", ""))
            text = (ph.get("text") or "").strip("/[] ")
            if acc in ("uk", "us"):
                if ph.get("audio") and not out[f"audio_{acc}"]:
                    out[f"audio_{acc}"] = ph["audio"]
                if text and not out[f"phonetic_{acc}"]:
                    out[f"phonetic_{acc}"] = text
        for m in entry.get("meanings", []):
            pos = m.get("partOfSpeech", "")
            for d in m.get("definitions", []):
                if d.get("definition"):
                    senses.setdefault(pos, []).append(d["definition"])
                if d.get("example"):
                    out["examples"].append(d["example"])
    out["senses"] = [{"pos": p, "definitions": ds} for p, ds in senses.items()]
    return out


def fetch_free_dictionary(word: str, db: UserDB | None = None) -> dict | None:
    key = f"fd:{word.lower()}"
    if db and (hit := db.cache_get(key, CACHE_DAYS)) is not None:
        return hit or None
    data = _get_json(FREE_DICT_URL.format(requests.utils.quote(word.lower())))
    parsed = parse_free_dictionary(data) if data else {}
    if db:
        db.cache_set(key, parsed)
    return parsed or None


def fetch_examples(word: str, lang: str = "en", db: UserDB | None = None, limit: int = 8) -> list[Example]:
    """Bilingual sentences containing `word` from Tatoeba."""
    key = f"tat:{lang}:{word.lower()}"
    if db and (hit := db.cache_get(key, CACHE_DAYS)) is not None:
        return [Example(**e) for e in hit]
    src, tgt = ("eng", "cmn") if lang == "en" else ("cmn", "eng")
    q = f"={word}" if lang == "en" and " " not in word else word
    data = _get_json(TATOEBA_URL, lang=src, q=q, **{"trans:lang": tgt}, sort="relevance",
                     limit=limit * 2) or {}
    out: list[Example] = []
    for s in data.get("data", []):
        trans = [t for t in s.get("translations", []) if t.get("lang") == tgt]
        if not trans:
            continue
        # Prefer simplified script and direct translations.
        trans.sort(key=lambda t: (t.get("script") == "Hant", not t.get("is_direct", False)))
        if lang == "en":
            out.append(Example(en=s["text"], zh=trans[0]["text"]))
        else:
            out.append(Example(en=trans[0]["text"], zh=s["text"]))
        if len(out) >= limit:
            break
    if db:
        db.cache_set(key, [e.__dict__ for e in out])
    return out


def fetch_dictionary_for(res: LookupResult, db: UserDB | None = None) -> dict | None:
    """Free Dictionary data for an English word result (falls back to its lemma)."""
    if res.language != "en" or res.kind != "word":
        return None
    word = res.headword or res.query
    fd = fetch_free_dictionary(word, db)
    if fd is None and res.lemma:
        fd = fetch_free_dictionary(res.lemma, db)
    return fd


def apply_dictionary(res: LookupResult, fd: dict | None) -> None:
    if not fd:
        return
    res.phonetic_uk = fd["phonetic_uk"] or res.phonetic_uk or fd["phonetic"]
    res.phonetic_us = fd["phonetic_us"] or res.phonetic_us or fd["phonetic"]
    res.audio_uk = fd["audio_uk"] or res.audio_uk
    res.audio_us = fd["audio_us"] or res.audio_us
    if fd["senses"]:
        res.senses_en = [Sense(**s) for s in fd["senses"]]
        res.found = True
    apply_examples(res, [Example(en=x) for x in fd["examples"]])


def apply_examples(res: LookupResult, examples: list[Example],
                   to_simplified: Callable[[str], str] | None = None) -> None:
    seen = {e.en.lower(): e for e in res.examples}
    for ex in examples:
        if ex.zh and to_simplified:
            ex = Example(ex.en, to_simplified(ex.zh))
        if (old := seen.get(ex.en.lower())) is not None:
            old.zh = old.zh or ex.zh
            continue
        seen[ex.en.lower()] = ex
        res.examples.append(ex)
    # Bilingual examples first — they're the most useful to learners.
    res.examples.sort(key=lambda e: e.zh is None)


def enrich(result: LookupResult, db: UserDB | None = None,
           to_simplified: Callable[[str], str] | None = None) -> LookupResult:
    """Return a copy of `result` with all online data merged in (best effort)."""
    res = copy.deepcopy(result)
    if res.kind != "word":
        return res
    errors: list[str] = []
    word = res.headword or res.query
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_fd = pool.submit(fetch_dictionary_for, res, db)
        fut_ex = pool.submit(fetch_examples, word, res.language, db)
        for fut, apply in ((fut_fd, lambda v: apply_dictionary(res, v)),
                           (fut_ex, lambda v: apply_examples(res, v, to_simplified))):
            try:
                apply(fut.result())
            except OfflineError as e:
                errors.append(str(e))
    res.online_loaded = not errors
    res.online_error = "; ".join(dict.fromkeys(errors))
    return res

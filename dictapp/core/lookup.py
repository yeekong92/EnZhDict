"""Offline dictionary lookup over the prebuilt dict.db (ECDICT + CC-CEDICT)."""
from __future__ import annotations

import difflib
import re
import sqlite3
import threading
from pathlib import Path

from .langdetect import detect_language, is_cjk, looks_like_single_term, normalize_query, tidy_sentence
from .models import LookupResult, Sense, ZhEntry

_EN_POS = {
    "n": "noun", "v": "verb", "vt": "verb", "vi": "verb", "a": "adjective",
    "s": "adjective", "adj": "adjective", "r": "adverb", "adv": "adverb",
    "prep": "preposition", "conj": "conjunction", "pron": "pronoun",
    "int": "interjection", "interj": "interjection", "art": "article",
    "num": "numeral", "abbr": "abbreviation", "aux": "auxiliary verb",
}
_DEF_LINE = re.compile(r"^(n|v|vt|vi|a|s|adj|r|adv|prep|conj|pron|int|interj|art|num|abbr|aux)\.?\s+(.*)$")
_ZH_LINE = re.compile(r"^((?:[a-z]+\.\s*)+|\[[^\]]+\]\s*)(.*)$")
_INFLECTION_ZH = re.compile(r"的(过去式|过去分词|现在分词|复数|第三人称单数|比较级|最高级)")

_EXCHANGE_NAMES = {
    "p": "past tense", "d": "past participle", "i": "present participle",
    "3": "third-person singular", "r": "comparative", "t": "superlative",
    "s": "plural",
}
_TAG_NAMES = {"zk": "中考", "gk": "高考", "cet4": "CET-4", "cet6": "CET-6",
              "ky": "考研", "toefl": "TOEFL", "ielts": "IELTS", "gre": "GRE"}


class DictionaryUnavailable(RuntimeError):
    pass


def fix_ipa(p: str) -> str:
    """ECDICT uses ASCII stand-ins; convert to proper IPA symbols."""
    if not p:
        return ""
    p = p.replace("ә", "ə").replace("'", "ˈ").replace(",", "ˌ").replace(":", "ː")
    return p.strip("/[] ")


def _parse_exchange(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (raw or "").split("/"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k] = v
    return out


def _group_definitions(text: str) -> list[Sense]:
    senses: list[Sense] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _DEF_LINE.match(line)
        pos, body = (_EN_POS.get(m.group(1), m.group(1)), m.group(2)) if m else ("", line)
        if senses and senses[-1].pos == pos:
            senses[-1].definitions.append(body)
        else:
            existing = next((s for s in senses if s.pos == pos), None)
            if existing:
                existing.definitions.append(body)
            else:
                senses.append(Sense(pos, [body]))
    return senses


def _split_zh(text: str) -> list[tuple[str, str]]:
    main, domain = [], []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _ZH_LINE.match(line)
        pos, body = (m.group(1).strip(), m.group(2).strip()) if m else ("", line)
        (domain if pos.startswith("[") else main).append((pos, body))
    return main + domain  # subject-specific senses ([医], [计]...) last


class Dictionary:
    """Thread-safe read-only access to dict.db."""

    def __init__(self, path: Path | str | None):
        if not path or not Path(path).exists():
            raise DictionaryUnavailable(
                "Offline dictionary not found. Run `python scripts/build_dict.py` "
                "to download and build resources/dict.db.")
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        self._con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        self._t2s: dict[str, str] | None = None

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._con.execute(sql, args).fetchall()

    # ------------------------------------------------------------------ API
    def lookup(self, raw: str) -> LookupResult:
        text = normalize_query(raw)
        lang = detect_language(text)
        if lang == "zh":
            res = self.lookup_zh(text)
        elif lang == "en":
            res = self.lookup_en(text)
        else:
            return LookupResult(query=text, language="unknown", kind="word")
        if res.kind == "sentence":
            res.query = res.headword = tidy_sentence(raw) or text
        return res

    def lookup_en(self, text: str) -> LookupResult:
        if not looks_like_single_term(text, "en"):
            return LookupResult(query=text, language="en", kind="sentence")
        row = self._ecdict_row(text)
        if row is None:
            for cand in _candidate_bases(text):
                row = self._ecdict_row(cand)
                if row:
                    break
        if row is None:
            kind = "word" if " " not in text else "sentence"
            return LookupResult(query=text, language="en", kind=kind, headword=text)
        return self._build_en(text, row)

    def lookup_zh(self, text: str) -> LookupResult:
        entries = self.zh_entries(text)
        if entries:
            return LookupResult(query=text, language="zh", kind="word", found=True,
                                headword=text, zh_entries=entries)
        return LookupResult(query=text, language="zh", kind="sentence", headword=text)

    def zh_entries(self, text: str) -> list[ZhEntry]:
        rows = self._q("SELECT simp, trad, pinyin, definitions FROM cedict "
                       "WHERE simp=? OR trad=?", (text, text))
        entries = [ZhEntry(s, t, p, d.split("\n")) for s, t, p, d in rows]

        def rank(e: ZhEntry) -> tuple:
            first = e.definitions[0].lower() if e.definitions else ""
            minor = first.startswith(("surname", "variant of", "old variant", "see ", "used in"))
            proper = e.pinyin[:1].isupper()
            return (minor, proper, -len(e.definitions))
        entries.sort(key=rank)
        return entries

    def segment_zh(self, text: str, max_len: int = 8) -> list[tuple[str, ZhEntry | None]]:
        """Forward maximum-matching segmentation with a per-token dictionary gloss."""
        out: list[tuple[str, ZhEntry | None]] = []
        i = 0
        while i < len(text):
            ch = text[i]
            if not ch.strip() or not is_cjk(ch):
                j = i
                while j < len(text) and not is_cjk(text[j]):
                    j += 1
                out.append((text[i:j], None))
                i = j
                continue
            for n in range(min(max_len, len(text) - i), 0, -1):
                piece = text[i:i + n]
                if n == 1 or self._q("SELECT 1 FROM cedict WHERE simp=? OR trad=? LIMIT 1",
                                     (piece, piece)):
                    entries = self.zh_entries(piece)
                    out.append((piece, entries[0] if entries else None))
                    i += n
                    break
        return out

    def to_simplified(self, text: str) -> str:
        """Character-level traditional -> simplified conversion using CC-CEDICT."""
        if self._t2s is None:
            rows = self._q("SELECT trad, simp FROM cedict WHERE length(trad) = 1 AND trad != simp")
            t2s: dict[str, str] = {}
            for t, s_ in rows:
                t2s.setdefault(t, s_)
            self._t2s = t2s
        return "".join(self._t2s.get(ch, ch) for ch in text)

    def suggest(self, prefix: str, limit: int = 12) -> list[str]:
        prefix = normalize_query(prefix)
        if not prefix:
            return []
        if detect_language(prefix) == "zh":
            rows = self._q("SELECT DISTINCT simp FROM cedict WHERE simp LIKE ? "
                           "ORDER BY length(simp) LIMIT ?", (prefix + "%", limit))
            return [r[0] for r in rows]
        esc = prefix.replace("%", r"\%").replace("_", r"\_")
        rows = self._q(
            "SELECT word FROM (SELECT word, frq, bnc FROM ecdict WHERE word LIKE ? ESCAPE '\\' "
            "LIMIT 400) ORDER BY (frq = 0), frq, length(word) LIMIT ?", (esc + "%", limit))
        seen, out = set(), []
        for (w,) in rows:
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        return out

    def did_you_mean(self, word: str, n: int = 5) -> list[str]:
        word = word.lower()
        if len(word) < 3:
            return []
        rows = self._q("SELECT word FROM ecdict WHERE word LIKE ? AND frq > 0 LIMIT 3000",
                       (word[:2] + "%",))
        pool = [r[0] for r in rows if " " not in r[0]]
        return difflib.get_close_matches(word, pool, n=n, cutoff=0.75)

    # -------------------------------------------------------------- helpers
    def _ecdict_row(self, word: str):
        rows = self._q(
            "SELECT word, phonetic, definition, translation, exchange, tag "
            "FROM ecdict WHERE word = ? COLLATE NOCASE "
            "ORDER BY (word = ?) DESC, (translation = '') ASC LIMIT 1", (word, word))
        return rows[0] if rows else None

    def _build_en(self, query: str, row) -> LookupResult:
        word, phonetic, definition, translation, exchange, tag = row
        ex = _parse_exchange(exchange)
        res = LookupResult(query=query, language="en", kind="word", found=True, headword=word)
        res.phonetic_uk = fix_ipa(phonetic)
        res.senses_en = _group_definitions(definition)
        res.meanings_zh = _split_zh(translation)
        res.tags = [_TAG_NAMES.get(t, t) for t in (tag or "").split() if t]
        res.forms = {_EXCHANGE_NAMES[k]: v for k, v in ex.items() if k in _EXCHANGE_NAMES}

        lemma = ex.get("0", "")
        if lemma and lemma.lower() != word.lower():
            res.lemma = lemma
            kinds = [_EXCHANGE_NAMES.get(k, "") for k in ex.get("1", "")]
            kinds = [k for k in kinds if k]
            res.inflection = f"{' / '.join(kinds) or 'form'} of {lemma}"
            base = self._ecdict_row(lemma)
            if base:
                own_zh = [(p, t) for p, t in res.meanings_zh if not _INFLECTION_ZH.search(t)]
                base_zh = _split_zh(base[3])
                res.meanings_zh = own_zh + [m for m in base_zh if m not in own_zh]
                if not res.senses_en:
                    res.senses_en = _group_definitions(base[2])
                if not res.phonetic_uk:
                    res.phonetic_uk = fix_ipa(base[1])
        return res


def _candidate_bases(word: str) -> list[str]:
    """Guess base forms for words ECDICT doesn't list directly."""
    w = word.lower().replace("’", "'")
    cands: list[str] = []
    if w.endswith("'s"):
        cands.append(w[:-2])
    if w.endswith("s'"):
        cands.append(w[:-1])
    if "-" in w:
        cands += [w.replace("-", ""), w.replace("-", " ")]
    rules = [("ies", "y"), ("es", ""), ("s", ""), ("ied", "y"), ("ed", ""), ("ed", "e"),
             ("ing", ""), ("ing", "e"), ("ier", "y"), ("iest", "y"), ("er", ""), ("est", ""),
             ("ly", ""), ("ily", "y")]
    for suf, rep in rules:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            stem = w[: -len(suf)] + rep
            cands.append(stem)
            if rep == "" and len(stem) >= 3 and stem[-1] == stem[-2]:
                cands.append(stem[:-1])  # stopped -> stop
    if w != word:
        cands.insert(0, w)
    return list(dict.fromkeys(c for c in cands if c))

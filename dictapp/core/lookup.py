"""Offline dictionary lookup over the prebuilt dict.db (ECDICT + CC-CEDICT + Wiktionary Tagalog)."""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

from .langdetect import (
    TAGALOG_MARKERS, detect_language, is_cjk, latin_words, looks_like_single_term,
    normalize_query, tagalog_markers, tidy_sentence,
)
from .models import Example, LookupResult, Sense, TlEntry, ZhEntry

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
# Tagalog verb/noun affixes, used only as a weak hint for words in neither dictionary.
_TL_MORPH = re.compile(r"^(nag|mag|pag|naka|maka|nakaka|ipag|pinag|pinaka|mang|nang|pang|ika)\w{3,}"
                       r"|^\w{2,}(han|hin)$|^(\w)([aeiou])\3\4")
_TL_SHOW_TAGS = {"obsolete", "archaic", "rare", "uncommon", "dated", "slang", "colloquial",
                 "informal", "formal", "figuratively", "idiomatic", "vulgar", "derogatory"}
# Single characters whose most common reading in running text is the particle one,
# not CC-CEDICT's first listed reading (了 le, not liǎo).
_PARTICLE_PINYIN = {"了": "le", "的": "de", "地": "de", "得": "de", "着": "zhe", "么": "me",
                    "吗": "ma", "呢": "ne", "吧": "ba", "啊": "a", "过": "guo", "们": "men",
                    "个": "gè", "子": "zi"}
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
        self._guide_cache: dict[tuple[str, str], str] = {}
        self.has_tagalog = bool(self._q(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tagalog'"))

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._con.execute(sql, args).fetchall()

    # ------------------------------------------------------------------ API
    def lookup(self, raw: str, lang: str | None = None) -> LookupResult:
        """Look up `raw`. `lang` ("en"/"tl") overrides auto-detection for
        Latin-script text; Chinese script is always treated as Chinese."""
        text = normalize_query(raw)
        script = detect_language(text)
        if script == "unknown":
            return LookupResult(query=text, language="unknown", kind="word")
        if script == "zh":
            res = self.lookup_zh(text)
        else:
            if lang not in ("en", "tl"):
                lang = self.detect_latin(text)
            res = self.lookup_tl(text) if lang == "tl" else self.lookup_en(text)
        if res.kind == "sentence":
            res.query = res.headword = tidy_sentence(raw) or text
        else:
            self._add_cross_language(res)
        return res

    def detect_latin(self, text: str) -> str:
        """English or Tagalog? Scores each word against both dictionaries."""
        words = latin_words(text)[:30]
        if not self.has_tagalog or not words:
            return "en"
        en = tl = 0.0
        tl += 2 * tagalog_markers(words)
        for w in words:
            if w in TAGALOG_MARKERS:
                continue
            plain = _strip_accents(w)
            if plain != w:
                tl += 1.0                       # stress marks (baháy) are a Tagalog giveaway
            in_tl = bool(self._q("SELECT 1 FROM tagalog WHERE word = ? COLLATE NOCASE LIMIT 1", (plain,)))
            row = self._q("SELECT frq, bnc, collins, oxford FROM ecdict WHERE word = ? COLLATE NOCASE "
                          "LIMIT 1", (w,))
            common_en = bool(row) and any(v and v > 0 for v in row[0])
            if common_en:
                en += 0.6 if in_tl else 1.5       # "said", "is" are also Tagalog words
                tl += 0.3 if in_tl else 0
            elif in_tl:
                tl += 0.8 if row else 1.5        # rare English homographs lose to Tagalog
                en += 0.3 if row else 0
            elif row:
                en += 1.0
            elif _TL_MORPH.search(w):
                tl += 0.5
        return "tl" if tl > en else "en"

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

    def lookup_tl(self, text: str) -> LookupResult:
        entries = self.tl_entries(text) if self.has_tagalog else []
        if not entries:
            kind = "word" if looks_like_single_term(text, "tl") and " " not in text else "sentence"
            return LookupResult(query=text, language="tl", kind=kind, headword=text)
        res = LookupResult(query=text, language="tl", kind="word", found=True,
                           headword=entries[0].canonical or entries[0].word, tl_entries=entries)
        res.senses_en = _tl_senses(entries)
        base = next((e for e in entries if e.lemma), None)
        if base:
            res.lemma, res.inflection = base.lemma, base.lemma_note
            only_forms = all(g == e.lemma_note for e in entries for g, _ in e.senses)
            if only_forms:  # e.g. "kinain": show the root verb's meanings too
                res.senses_en += _tl_senses(self.tl_entries(base.lemma))
        for e in entries:
            res.examples += [Example(en=en, tl=tl) for tl, en in e.examples]
        return res

    def tl_entries(self, word: str) -> list[TlEntry]:
        sql = ("SELECT word, canonical, pos, ipa, senses, examples, lemma, lemma_note FROM tagalog "
               "WHERE word = ? COLLATE NOCASE ORDER BY (word = ?) DESC, rowid")
        rows = self._q(sql, (word, word))
        if not rows:
            plain = _strip_accents(word)
            if plain != word:
                rows = self._q(sql, (plain, plain))
        out = []
        for w, canon, pos, ipa, senses, examples, lemma, note in rows:
            out.append(TlEntry(w, canon, pos, ipa,
                               [(x["gloss"], x["tags"]) for x in json.loads(senses)],
                               [tuple(x) for x in json.loads(examples)], lemma or "", note or ""))
        # Alphabet-letter names ("ka" = the letter K) are rarely what a reader wants.
        out.sort(key=lambda e: all(g.startswith("the name of the ") and "letter" in g
                                   for g, _ in e.senses))
        return out

    def tagalog_for_english(self, word: str, limit: int = 8) -> list[tuple[str, str]]:
        """English word -> [(Tagalog word, its gloss)], best first."""
        if not self.has_tagalog:
            return []
        rows = self._q(
            "SELECT tl, gloss FROM tl_rev WHERE en = ? AND substr(tl, 1, 1) != \"'\" "
            "ORDER BY rank / 10, (rank % 10 >= 5), (rank % 10 > 0), -richness LIMIT ?",
            (word.lower(), limit))
        return [(t, g) for t, g in rows]

    def gloss_tl(self, text: str) -> list[tuple[str, str, str]]:
        """Word-by-word gloss of a Tagalog sentence: (token, "", first meaning)."""
        out = []
        for w in latin_words(text):
            entries = self.tl_entries(w)
            meaning = ""
            for e in entries:
                good = [g for g, _tags in e.senses if g != e.lemma_note]
                if good:
                    meaning = good[0]
                    break
            if not meaning and entries and entries[0].lemma:
                meaning = entries[0].lemma_note
            out.append((w, "", meaning))
        return out

    def _add_cross_language(self, res: LookupResult) -> None:
        """English words get their Tagalog equivalents, and a hint when the same
        spelling is also a word in the other Latin-script language."""
        if not self.has_tagalog or res.language not in ("en", "tl"):
            return
        if res.language == "en":
            word = res.headword or res.query
            res.meanings_tl = self.tagalog_for_english(word)
            if not res.meanings_tl and res.lemma:
                res.meanings_tl = self.tagalog_for_english(res.lemma)
            tl = self.tl_entries(res.query) if " " not in res.query else []
            if tl:
                res.alt_lang = "tl"
                res.alt_summary = next((g for e in tl for g, _ in e.senses), "")
        else:
            row = self._ecdict_row(res.query)
            common = row and self._q("SELECT 1 FROM ecdict WHERE word = ? COLLATE NOCASE AND "
                                     "(frq > 0 OR bnc > 0 OR collins > 0) LIMIT 1", (res.query,))
            if common and (row[3] or row[2]):
                res.alt_lang = "en"
                res.alt_summary = (row[3] or row[2]).splitlines()[0]

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

    def pronunciation_guide(self, text: str, lang: str) -> str:
        """Word-by-word pronunciation for a sentence: IPA for English/Tagalog,
        pinyin for Chinese. Words with no known pronunciation are left as-is."""
        key = (text, lang)
        if key in self._guide_cache:
            return self._guide_cache[key]
        if lang == "zh":
            parts = []
            for tok, entry in self.segment_zh(text):
                if tok in _PARTICLE_PINYIN:
                    parts.append(_PARTICLE_PINYIN[tok])
                elif entry:
                    parts.append(entry.pinyin)
                elif any(ch.isalnum() and not is_cjk(ch) for ch in tok):
                    parts.append(tok.strip())  # Latin words / numbers inside Chinese text
            out = " ".join(p for p in parts if p)
        else:
            parts = []
            for w in latin_words(text):
                ipa = self._word_ipa(w, lang)
                parts.append(ipa or w)
            out = "/" + " ".join(parts) + "/" if parts else ""
        if len(self._guide_cache) > 4000:
            self._guide_cache.clear()
        self._guide_cache[key] = out
        return out

    def _word_ipa(self, word: str, lang: str) -> str:
        if lang == "tl":
            if not self.has_tagalog:
                return ""
            plain = _strip_accents(word)
            rows = self._q("SELECT ipa FROM tagalog WHERE word = ? COLLATE NOCASE AND ipa != '' "
                           "ORDER BY rowid LIMIT 1", (plain,))
            return rows[0][0] if rows else ""
        w = word.replace("’", "'")
        rows = self._q("SELECT phonetic FROM ecdict WHERE word = ? COLLATE NOCASE AND phonetic != '' "
                       "ORDER BY (word = ?) DESC LIMIT 1", (w, w.lower()))
        return fix_ipa(rows[0][0]) if rows else ""

    def suggest(self, prefix: str, limit: int = 12, lang: str | None = None) -> list[str]:
        prefix = normalize_query(prefix)
        if not prefix:
            return []
        tl_words: list[str] = []
        if self.has_tagalog and detect_language(prefix) != "zh" and lang != "en":
            esc_tl = prefix.replace("%", r"\%").replace("_", r"\_")
            tl_words = [r[0] for r in self._q(
                "SELECT word FROM tagalog WHERE word LIKE ? ESCAPE '\\' AND pos != 'proper noun' "
                "GROUP BY lower(word) ORDER BY (lower(word) != lower(?)), -max(richness), length(word) "
                "LIMIT ?", (esc_tl + "%", prefix, limit if lang == "tl" else 3))]
            if lang == "tl":
                return tl_words
            limit -= len(tl_words)
        if detect_language(prefix) == "zh":
            rows = self._q("SELECT DISTINCT simp FROM cedict WHERE simp LIKE ? "
                           "ORDER BY length(simp) LIMIT ?", (prefix + "%", limit))
            return [r[0] for r in rows]
        esc = prefix.replace("%", r"\%").replace("_", r"\_")
        rows = self._q(
            "SELECT word FROM (SELECT word, frq, bnc FROM ecdict WHERE word LIKE ? ESCAPE '\\' "
            "LIMIT 400) ORDER BY (frq = 0), frq, length(word) LIMIT ?", (esc + "%", limit))
        seen, out = set(), []
        for w in [r[0] for r in rows] + tl_words:
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


def _strip_accents(text: str) -> str:
    """"baháy" -> "bahay" (Wiktionary headwords are stored without stress marks)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def _tl_senses(entries: list[TlEntry]) -> list[Sense]:
    senses: list[Sense] = []
    for e in entries:
        defs = []
        for gloss, tags in e.senses:
            shown = [t for t in tags if t in _TL_SHOW_TAGS]
            defs.append(f"{gloss} ({', '.join(shown)})" if shown else gloss)
        existing = next((x for x in senses if x.pos == e.pos), None)
        if existing:
            existing.definitions += defs
        else:
            senses.append(Sense(e.pos, defs))
    return senses

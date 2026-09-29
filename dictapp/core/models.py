"""Plain data objects shared by lookup, UI and storage."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Sense:
    pos: str                      # "noun", "verb", ...
    definitions: list[str] = field(default_factory=list)


@dataclass
class Example:
    en: str
    zh: str | None = None
    tl: str | None = None         # Tagalog sentence (for Tagalog lookups `en` is its translation)


@dataclass
class ZhEntry:
    """A CC-CEDICT entry."""
    simp: str
    trad: str
    pinyin: str
    definitions: list[str]


@dataclass
class TlEntry:
    """A Wiktionary Tagalog entry (one part of speech)."""
    word: str
    canonical: str                # with stress accents, e.g. "baháy"
    pos: str
    ipa: str
    senses: list[tuple[str, list[str]]]      # (English gloss, tags)
    examples: list[tuple[str, str]] = field(default_factory=list)  # (Tagalog, English)
    lemma: str = ""
    lemma_note: str = ""


@dataclass
class LookupResult:
    query: str
    language: str                 # "en" / "zh" / "tl"
    kind: str                     # "word" / "sentence"
    found: bool = False
    headword: str = ""
    phonetic_uk: str = ""
    phonetic_us: str = ""
    senses_en: list[Sense] = field(default_factory=list)       # English definitions by POS
    meanings_zh: list[tuple[str, str]] = field(default_factory=list)  # (pos, Chinese text)
    zh_entries: list[ZhEntry] = field(default_factory=list)   # Chinese headword entries
    tl_entries: list[TlEntry] = field(default_factory=list)   # Tagalog headword entries
    meanings_tl: list[tuple[str, str]] = field(default_factory=list)  # English word -> (Tagalog word, gloss)
    examples: list[Example] = field(default_factory=list)
    audio_uk: str = ""            # URL or local path
    audio_us: str = ""
    lemma: str = ""               # base form if the query is inflected ("went" -> "go")
    inflection: str = ""          # human-readable note, e.g. "past tense of go"
    forms: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)            # CET4, IELTS, ...
    translation: str = ""         # sentence translation
    translation_engine: str = ""
    translation_error: str = ""
    translation_tl: str = ""      # English -> Tagalog machine translation (fetched on demand)
    translation_tl_error: str = ""
    alt_lang: str = ""            # the text is also a word in this other language...
    alt_summary: str = ""         # ...meaning this (shown as a hint with a link)
    gloss: list[tuple[str, str, str]] = field(default_factory=list)  # zh sentence: (token, pinyin, meaning)
    suggestions: list[str] = field(default_factory=list)             # "did you mean"
    online_loaded: bool = False
    online_error: str = ""

    # --- convenience summaries used by popup / saved table -----------------
    @property
    def pinyin(self) -> str:
        seen: list[str] = []
        for e in self.zh_entries:
            if e.pinyin not in seen:
                seen.append(e.pinyin)
        return " / ".join(seen)

    def short_en(self, n: int = 3) -> list[str]:
        out: list[str] = []
        for s in self.senses_en:
            for d in s.definitions:
                out.append(f"{_abbr(s.pos)} {d}".strip())
                break
            if len(out) >= n:
                return out
        if len(out) < n:
            for s in self.senses_en:
                for d in s.definitions[1:]:
                    out.append(f"{_abbr(s.pos)} {d}".strip())
                    if len(out) >= n:
                        return out
        if not out:
            for e in self.zh_entries:
                defs = [d for d in e.definitions if not d.startswith("CL:")]
                out.extend(defs[: n - len(out)])
                if len(out) >= n:
                    break
        return out[:n]

    @property
    def ipa(self) -> str:
        """Tagalog IPA (English uses phonetic_uk / phonetic_us)."""
        return next((e.ipa for e in self.tl_entries if e.ipa), "")

    def short_tl(self, n: int = 3) -> list[str]:
        words = list(dict.fromkeys(w for w, _ in self.meanings_tl))
        return words[:n]

    def meaning_tl_text(self) -> str:
        return "; ".join(self.short_tl(6)) or self.translation_tl

    def short_zh(self, n: int = 3) -> list[str]:
        return [f"{pos} {txt}".strip() for pos, txt in self.meanings_zh[:n]]

    def meaning_en_text(self) -> str:
        if self.language == "zh":
            return "\n".join(f"[{e.pinyin}] " + "; ".join(e.definitions) for e in self.zh_entries)
        lines = []
        for s in self.senses_en:
            lines.append(f"{s.pos}: " + "; ".join(s.definitions[:4]))
        return "\n".join(lines)

    def meaning_zh_text(self) -> str:
        return "\n".join(f"{pos} {txt}".strip() for pos, txt in self.meanings_zh)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LookupResult":
        d = dict(d)
        d["senses_en"] = [Sense(**s) for s in d.get("senses_en", [])]
        d["zh_entries"] = [ZhEntry(**e) for e in d.get("zh_entries", [])]
        d["tl_entries"] = [TlEntry(**e) for e in d.get("tl_entries", [])]
        d["meanings_tl"] = [tuple(m) for m in d.get("meanings_tl", [])]
        d["examples"] = [Example(**e) for e in d.get("examples", [])]
        d["meanings_zh"] = [tuple(m) for m in d.get("meanings_zh", [])]
        d["gloss"] = [tuple(g) for g in d.get("gloss", [])]
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


_ABBR = {"noun": "n.", "verb": "v.", "adjective": "adj.", "adverb": "adv.",
         "pronoun": "pron.", "preposition": "prep.", "conjunction": "conj.",
         "interjection": "int.", "determiner": "det.", "abbreviation": "abbr."}


def _abbr(pos: str) -> str:
    return _ABBR.get(pos, f"{pos}." if pos else "")

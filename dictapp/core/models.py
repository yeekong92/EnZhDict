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


@dataclass
class ZhEntry:
    """A CC-CEDICT entry."""
    simp: str
    trad: str
    pinyin: str
    definitions: list[str]


@dataclass
class LookupResult:
    query: str
    language: str                 # "en" / "zh"
    kind: str                     # "word" / "sentence"
    found: bool = False
    headword: str = ""
    phonetic_uk: str = ""
    phonetic_us: str = ""
    senses_en: list[Sense] = field(default_factory=list)       # English definitions by POS
    meanings_zh: list[tuple[str, str]] = field(default_factory=list)  # (pos, Chinese text)
    zh_entries: list[ZhEntry] = field(default_factory=list)   # Chinese headword entries
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

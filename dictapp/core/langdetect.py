"""Language detection and query normalisation for English / Chinese input."""
from __future__ import annotations

import re
import unicodedata

_CJK_RANGES = (
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F), # Extensions B–F + compatibility supplement
)
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:[-'’][A-Za-z]+)*")
# Punctuation to trim from the ends of a selection (ASCII + CJK + quotes).
_EDGE_PUNCT = " \t\r\n\"'`“”‘’«»()[]{}<>.,;:!?。，、；：！？（）【】《》「」『』…—-·*_#"


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def detect_language(text: str) -> str:
    """Return ``"zh"``, ``"en"`` or ``"unknown"``.

    Any meaningful share of Han characters makes it Chinese, since mixed
    selections like ``用 Python 写`` should still be treated as Chinese text.
    """
    cjk = sum(1 for ch in text if is_cjk(ch))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cjk == 0 and latin == 0:
        return "unknown"
    # A Han character carries about as much as a 5-letter English word.
    return "zh" if cjk * 5 >= latin else "en"


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("­", "")  # soft hyphen (common in PDFs)
    # Join words broken across lines in PDFs: "exam-\nple" -> "example"
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_query(text: str) -> str:
    """Tidy a raw selection: NFKC, collapse whitespace, trim edge punctuation."""
    return _clean(text).strip(_EDGE_PUNCT).strip()


def tidy_sentence(text: str) -> str:
    """Like normalize_query, but keeps sentence punctuation (only wrapping quotes go)."""
    return _clean(text).strip(" \"'`“”‘’«»()[]{}<>（）【】《》「」『』").strip()


def looks_like_single_term(text: str, lang: str | None = None) -> bool:
    """Heuristic: is this a word/short phrase (dictionary lookup) or a sentence?"""
    lang = lang or detect_language(text)
    if not text:
        return False
    if lang == "zh":
        # No sentence punctuation and short enough to be a dictionary headword.
        return len(text) <= 8 and not re.search(r"[，。！？；,.!?;\s]", text)
    words = _LATIN_WORD.findall(text)
    if len(words) == 1 and _LATIN_WORD.fullmatch(text):
        return True
    # Short phrases (phrasal verbs, idioms) are still dictionary candidates.
    return 1 < len(words) <= 4 and not re.search(r"[.!?;:,]", text)

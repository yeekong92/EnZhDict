"""Convert CC-CEDICT numbered pinyin (``ni3 hao3``) to tone marks (``nǐ hǎo``)."""
from __future__ import annotations

import re

_TONES = {
    "a": "āáǎà", "e": "ēéěè", "i": "īíǐì",
    "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ",
}
_SYLLABLE = re.compile(r"([A-Za-züÜ:]+)([1-5])")


def _mark(syllable: str, tone: int) -> str:
    syllable = syllable.replace("u:", "ü").replace("U:", "Ü").replace("v", "ü")
    if tone == 5 or tone == 0:
        return syllable
    lower = syllable.lower()
    # Placement rules: a/e take the mark; in "ou" the o does; otherwise the last vowel.
    if "a" in lower:
        idx = lower.index("a")
    elif "e" in lower:
        idx = lower.index("e")
    elif "ou" in lower:
        idx = lower.index("o")
    else:
        idx = max((i for i, ch in enumerate(lower) if ch in "iouü"), default=-1)
        if idx < 0:
            return syllable
    ch = lower[idx]
    marked = _TONES[ch][tone - 1]
    if syllable[idx].isupper():
        marked = marked.upper()
    return syllable[:idx] + marked + syllable[idx + 1:]


def numbered_to_marks(text: str) -> str:
    return _SYLLABLE.sub(lambda m: _mark(m.group(1), int(m.group(2))), text)

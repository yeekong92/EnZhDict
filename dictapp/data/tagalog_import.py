"""Parse Kaikki.org (English Wiktionary) Tagalog JSONL into dict.db rows.

Used by scripts/build_dict.py; kept here so the parsing is unit-testable.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

SKIP_POS = {"character", "symbol", "punct", "romanization"}
_ASPECT = re.compile(r"^(complete|progressive|contemplative|contemplated|imperative|infinitive)"
                     r" aspect of (\S+)$", re.I)
_OF_FORM = re.compile(r"^(?:[\w\s-]+ )?(?:form|spelling|variant|equivalent|plural|diminutive|"
                      r"augmentative|clipping|abbreviation|contraction) of (\S+?)(?::.*)?$", re.I)
_LOW_VALUE_TAGS = {"obsolete", "archaic", "rare", "uncommon", "dated", "slang", "Internet",
                   "dialectal", "vulgar", "derogatory", "colloquial", "humorous", "regional",
                   "nonstandard", "euphemistic", "offensive", "figuratively", "idiomatic"}


def _low_value(tags: list[str]) -> bool:
    # Capitalised tags are regions/dialects (e.g. "Batangas", "Marinduque").
    return bool(_LOW_VALUE_TAGS.intersection(tags)) or any(t[:1].isupper() for t in tags)
_ARTICLE = re.compile(r"^(to|a|an|the)\s+", re.I)
_PAREN = re.compile(r"\s*\([^)]*\)")

POS_NAMES = {"adj": "adjective", "adv": "adverb", "intj": "interjection", "pron": "pronoun",
             "prep": "preposition", "conj": "conjunction", "num": "numeral", "name": "proper noun",
             "det": "determiner", "prep_phrase": "prepositional phrase"}


def _gloss(sense: dict) -> str:
    glosses = sense.get("glosses") or sense.get("raw_glosses") or []
    # Nested senses repeat the parent gloss first; the last element is the specific one.
    return glosses[-1].strip() if glosses else ""


def _ipa(rec: dict) -> str:
    for s in rec.get("sounds", []):
        ipa = s.get("ipa", "")
        if ipa.startswith("/"):
            return ipa.strip("/")
    for s in rec.get("sounds", []):
        if s.get("ipa"):
            return s["ipa"].strip("/[]")
    return ""


def parse_record(rec: dict) -> dict | None:
    """One Kaikki record -> a row dict for the `tagalog` table (or None to skip)."""
    pos = rec.get("pos", "")
    word = (rec.get("word") or "").strip()
    if not word or pos in SKIP_POS:
        return None
    canonical = next((f["form"] for f in rec.get("forms", [])
                      if "canonical" in (f.get("tags") or [])), word)
    senses, examples = [], []
    lemma = lemma_note = ""
    for s in rec.get("senses", []):
        g = _gloss(s)
        if not g:
            continue
        tags = [t for t in (s.get("tags") or []) if t not in ("form-of", "alt-of")]
        senses.append({"gloss": g, "tags": tags})
        if not lemma:
            if s.get("form_of"):
                lemma, lemma_note = s["form_of"][0].get("word", ""), g
            elif m := _ASPECT.match(g):
                lemma, lemma_note = m.group(2), g
            elif s.get("alt_of"):
                lemma, lemma_note = s["alt_of"][0].get("word", ""), g
        for ex in s.get("examples") or []:
            en = ex.get("english") or ex.get("translation")
            if ex.get("text") and en and len(examples) < 6:
                examples.append([ex["text"].strip(), en.strip()])
    if not senses:
        return None
    if lemma.lower() == word.lower():
        lemma = lemma_note = ""
    return {
        "word": word, "canonical": canonical, "pos": POS_NAMES.get(pos, pos), "ipa": _ipa(rec),
        "senses": json.dumps(senses, ensure_ascii=False),
        "examples": json.dumps(examples, ensure_ascii=False),
        "lemma": lemma, "lemma_note": lemma_note,
        # Wiktionary has no frequencies; richer entries tend to be the common words.
        "richness": len(senses) + 2 * len(examples) + 3 * (not lemma),
    }


def reverse_keys(gloss: str) -> list[tuple[str, int]]:
    """English lookup keys from a gloss, with a sub-rank (0 = the whole gloss)."""
    if len(gloss) > 80 or " of " in gloss and _OF_FORM.match(gloss) or _ASPECT.match(gloss):
        return []
    clean = _PAREN.sub("", gloss)
    out: list[tuple[str, int]] = []
    pieces = []
    for part in clean.split(";"):
        commas = [p for p in part.split(",") if p.strip()]
        # "torn, ripped" is a list of synonyms; "small, dried shrimp" is one phrase.
        if len(commas) > 1 and len({len(p.split()) for p in commas}) == 1 and len(commas[0].split()) <= 2:
            pieces += commas
        else:
            pieces.append(part)
    for i, piece in enumerate(pieces):
        key = _ARTICLE.sub("", piece.strip().strip(".:!")).strip().lower()
        if key and len(key.split()) <= 3 and re.fullmatch(r"[a-z][a-z' -]*", key):
            sub = 0 if len(pieces) == 1 else min(1 + i, 4)
            out.append((key, sub))
            head = key.split()[-1]
            if " " in key and len(head) >= 3 and not key.startswith(("be ", "make ", "get ")):
                out.append((head, 5 + min(i, 4)))  # "cooked rice" is also findable as "rice"
    return out


def load_tagalog(con: sqlite3.Connection, path: Path) -> tuple[int, int]:
    con.execute("""CREATE TABLE tagalog (
        word TEXT NOT NULL, canonical TEXT, pos TEXT, ipa TEXT, senses TEXT,
        examples TEXT, lemma TEXT, lemma_note TEXT, richness INTEGER)""")
    con.execute("CREATE TABLE tl_rev (en TEXT NOT NULL, tl TEXT NOT NULL, gloss TEXT, rank INTEGER, "
                "richness INTEGER)")
    rows, rev = [], {}
    richness: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            row = parse_record(rec)
            if not row:
                continue
            rows.append(row)
            key_word = row["word"].lower()
            richness[key_word] = richness.get(key_word, 0) + row["richness"]
            if row["pos"] == "proper noun":
                continue
            for si, sense in enumerate(json.loads(row["senses"])):
                penalty = 50 if _low_value(sense["tags"]) else 0
                for key, sub in reverse_keys(sense["gloss"]):
                    rank = penalty + min(si, 4) * 10 + sub
                    k = (key, row["word"])
                    if k not in rev or rev[k][1] > rank:
                        rev[k] = (sense["gloss"], rank)
    for row in rows:  # richness is per word, summed over all its part-of-speech records
        row["richness"] = richness[row["word"].lower()]
    con.executemany("INSERT INTO tagalog VALUES (:word, :canonical, :pos, :ipa, :senses, "
                    ":examples, :lemma, :lemma_note, :richness)", rows)
    con.executemany("INSERT INTO tl_rev VALUES (?,?,?,?,?)",
                    [(en, tl, g, r, richness[tl.lower()]) for (en, tl), (g, r) in rev.items()])
    con.execute("CREATE INDEX ix_tagalog_word ON tagalog(word COLLATE NOCASE)")
    con.execute("CREATE INDEX ix_tl_rev_en ON tl_rev(en)")
    return len(rows), len(rev)

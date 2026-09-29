"""Tagalog: Wiktionary import, English/Tagalog detection, lookup, reverse lookup."""
import json
import sqlite3

import pytest

from dictapp.core.langdetect import latin_words, tagalog_markers
from dictapp.core.lookup import Dictionary
from dictapp.data.tagalog_import import load_tagalog, parse_record, reverse_keys
from dictapp.data.userdb import UserDB


def rec(word, pos, *glosses, canonical=None, ipa=None, examples=(), tags=(), form_of=None):
    senses = []
    for i, g in enumerate(glosses):
        s = {"glosses": [g], "tags": list(tags)}
        if i == 0 and examples:
            s["examples"] = [{"text": t, "english": e} for t, e in examples]
        if i == 0 and form_of:
            s["form_of"] = [{"word": form_of}]
        senses.append(s)
    r = {"word": word, "pos": pos, "senses": senses, "lang_code": "tl"}
    if canonical:
        r["forms"] = [{"form": canonical, "tags": ["canonical"]}]
    if ipa:
        r["sounds"] = [{"ipa": f"/{ipa}/"}, {"ipa": "[x]"}]
    return r


RECORDS = [
    rec("bahay", "noun", "house", "edifice; building; hall", ipa="ˈbahaj",
        examples=[("Malaki ang bahay.", "The house is big.")]),
    rec("kasa", "noun", "house", tags=["colloquial"]),
    rec("kumain", "verb", "to eat", ipa="kuˈmaʔin"),
    rec("kainin", "verb", "to eat (something)"),
    rec("kinain", "verb", "complete aspect of kainin", ipa="kiˈnaʔin"),
    rec("maganda", "adj", "beautiful", "good", canonical="magandá", ipa="maɡanˈda"),
    rec("diwata", "noun", "beautiful, lovely maiden"),
    rec("aso", "noun", "dog", ipa="ˈʔaso"),
    rec("said", "adj", "consumed; with everything used up"),
    rec("may", "particle", "particle used as an existential marker: to be; to have"),
    rec("ka", "noun", "the name of the Latin script letter K/k, in the Abakada alphabet"),
    rec("ka", "pron", "you (second person singular)"),
    rec("amiga", "noun", "female equivalent of amigo: female friend", form_of="amigo"),
    rec("A", "character", "the first letter"),
]

ECDICT = [  # word, phonetic, definition, translation, pos, collins, oxford, tag, bnc, frq, exchange
    ("house", "haʊs", "n. a dwelling", "n. 房子", "", 5, 1, "", 100, 100, ""),
    ("said", "sed", "v. express in words", "say的过去式", "", 0, 0, "", 50, 50, "0:say/1:p"),
    ("may", "meɪ", "n. the month following April", "aux. 可以", "", 5, 1, "", 30, 30, ""),
    ("the", "ðə", "art. definite article", "art. 这", "", 5, 1, "", 1, 1, ""),
    ("is", "iz", "v. be", "v. 是", "", 5, 1, "", 2, 2, ""),
    ("big", "bɪɡ", "a. large", "a. 大的", "", 5, 1, "", 80, 80, ""),
    ("aso", "", "abbr. American Society of Oceanographers", "abbr. 美国海洋学会", "", 0, 0, "", 0, 0, ""),
    ("beautiful", "", "a. pleasing", "a. 美丽的", "", 5, 1, "", 300, 300, ""),
]


@pytest.fixture(scope="module")
def tl_dict(tmp_path_factory):
    d = tmp_path_factory.mktemp("tl")
    jsonl = d / "tl.jsonl"
    jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS), encoding="utf-8")
    path = d / "dict.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ecdict (word, phonetic, definition, translation, pos, collins, "
                "oxford, tag, bnc, frq, exchange)")
    con.execute("CREATE TABLE cedict (trad, simp, pinyin_num, pinyin, definitions)")
    con.executemany("INSERT INTO ecdict VALUES (?,?,?,?,?,?,?,?,?,?,?)", ECDICT)
    load_tagalog(con, jsonl)
    con.commit()
    con.close()
    return Dictionary(path)


# ---------------------------------------------------------------- import
def test_parse_record_fields():
    row = parse_record(RECORDS[5])
    assert row["word"] == "maganda" and row["canonical"] == "magandá"
    assert row["pos"] == "adjective" and row["ipa"] == "maɡanˈda"
    assert [s["gloss"] for s in json.loads(row["senses"])] == ["beautiful", "good"]
    assert parse_record(RECORDS[-1]) is None          # 'character' entries skipped


def test_parse_record_lemmas():
    kinain = parse_record(RECORDS[4])
    assert kinain["lemma"] == "kainin" and kinain["lemma_note"] == "complete aspect of kainin"
    assert parse_record(RECORDS[12])["lemma"] == "amigo"   # structured form_of
    assert json.loads(parse_record(RECORDS[0])["examples"]) == [["Malaki ang bahay.", "The house is big."]]


@pytest.mark.parametrize("gloss,keys", [
    ("house", ["house"]),
    ("to eat", ["eat"]),
    ("torn, ripped", ["torn", "ripped"]),
    ("small, dried shrimp", []),                     # adjective + noun, not synonyms
    ("edifice; building; hall", ["edifice", "building", "hall"]),
    ("cooked rice", ["cooked rice", "rice"]),        # head word also indexed
    ("complete aspect of kainin", []),
    ("female equivalent of amigo: female friend", []),
])
def test_reverse_keys(gloss, keys):
    assert [k for k, _ in reverse_keys(gloss)] == keys


# ------------------------------------------------------------- detection
def test_markers():
    assert tagalog_markers(latin_words("Kumain ka na ba?")) == 2   # ka, ba
    assert latin_words("Baháy ko") == ["baháy", "ko"]


@pytest.mark.parametrize("text,lang", [
    ("bahay", "tl"),
    ("kumain", "tl"),
    ("house", "en"),
    ("said", "en"),                # common English word that is also Tagalog
    ("may", "en"),
    ("aso", "tl"),                 # only an obscure English abbreviation
    ("baháy", "tl"),               # stress accent
    ("Kumain ka na ba?", "tl"),
    ("Ang ganda ng bahay mo", "tl"),
    ("The house is big.", "en"),
    ("nagluluto", "tl"),           # in neither dictionary: Tagalog affix
])
def test_detect(tl_dict, text, lang):
    assert tl_dict.lookup(text).language == lang


def test_override(tl_dict):
    assert tl_dict.lookup("said", lang="tl").language == "tl"
    assert tl_dict.lookup("bahay", lang="en").language == "en"
    assert tl_dict.lookup("银行", lang="tl").language == "zh"   # script wins


# ---------------------------------------------------------------- lookup
def test_tagalog_word(tl_dict):
    r = tl_dict.lookup("maganda")
    assert r.found and r.kind == "word" and r.headword == "magandá"
    assert r.ipa == "maɡanˈda"
    assert r.short_en(2) == ["adj. beautiful", "adj. good"]


def test_tagalog_examples(tl_dict):
    r = tl_dict.lookup("bahay")
    assert r.examples[0].tl == "Malaki ang bahay." and r.examples[0].en == "The house is big."


def test_inflected_verb_pulls_in_root(tl_dict):
    r = tl_dict.lookup("kinain")
    assert r.lemma == "kainin" and r.inflection == "complete aspect of kainin"
    assert any("to eat" in d for s in r.senses_en for d in s.definitions)


def test_letter_names_sort_last(tl_dict):
    assert tl_dict.tl_entries("ka")[0].pos == "pronoun"


def test_english_word_gets_tagalog(tl_dict):
    r = tl_dict.lookup("house")
    assert r.short_tl(2) == ["bahay", "kasa"]            # colloquial ranked lower
    r = tl_dict.lookup("beautiful")
    assert r.short_tl(1) == ["maganda"]                  # exact gloss beats "beautiful, lovely maiden"


def test_cross_language_hints(tl_dict):
    r = tl_dict.lookup("said")
    assert r.alt_lang == "tl" and r.alt_summary.startswith("consumed")
    r = tl_dict.lookup("aso")
    assert r.alt_lang == ""                              # obscure English entries don't count


def test_gloss_sentence(tl_dict):
    r = tl_dict.lookup("Kumain ka na ba?")
    assert r.kind == "sentence"
    gloss = dict((t, m) for t, _, m in tl_dict.gloss_tl(r.query))
    assert gloss["kumain"] == "to eat" and gloss["ka"].startswith("you")


def test_suggest_tagalog(tl_dict):
    assert tl_dict.suggest("bah", lang="tl") == ["bahay"]
    assert "bahay" in tl_dict.suggest("bah")


# --------------------------------------------------------------- storage
def test_same_spelling_saved_per_language(tmp_path):
    db = UserDB(tmp_path / "u.db")
    en, new_en = db.add_entry(text="may", type="word", language="en", meaning_zh="可以")
    tl, new_tl = db.add_entry(text="may", type="word", language="tl", meaning_en="to have")
    assert new_en and new_tl and en != tl
    assert db.find_entry("may", "tl").meaning_en == "to have"


def test_migrates_old_schema(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, type TEXT NOT NULL,
            language TEXT NOT NULL, meaning_en TEXT DEFAULT '', meaning_zh TEXT DEFAULT '',
            phonetic_uk TEXT DEFAULT '', phonetic_us TEXT DEFAULT '', pinyin TEXT DEFAULT '',
            translation TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE UNIQUE INDEX ux_entries_text ON entries(text COLLATE NOCASE, type);
        INSERT INTO entries (text, type, language, meaning_zh, created_at)
            VALUES ('may', 'word', 'en', '可以', '2026-01-01T00:00:00+00:00');
    """)
    con.commit()
    con.close()
    db = UserDB(path)
    assert db.find_entry("may").meaning_tl == ""
    _, created = db.add_entry(text="may", type="word", language="tl")
    assert created

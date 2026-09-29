"""Lookup tests run against a tiny fixture dictionary, so they don't need the
full dict.db. A second group runs against the real database when it exists."""
import sqlite3

import pytest

from dictapp.config import dict_db_path
from dictapp.core.lookup import Dictionary, DictionaryUnavailable, _candidate_bases, fix_ipa
from dictapp.core.pinyin import numbered_to_marks

ECDICT_ROWS = [
    ("apple", "'æpl", "n. fruit with red or yellow or green skin\nn. native Eurasian tree",
     "n. 苹果, 家伙\n[医] 苹果", "", 3, 1, "zk gk", 0, 1000, "s:apples"),
    ("go", "gәu", "v. change location; move", "vi. 去, 走", "", 5, 1, "zk", 0, 50,
     "p:went/d:gone/i:going/3:goes"),
    ("went", "went", "", "go的过去式", "", 0, 0, "", 0, 0, "0:go/1:p"),
    ("study", "'stʌdi", "n. a detailed investigation\nv. consider in detail", "n. 学习, 研究\nvt. 学习",
     "", 5, 1, "", 0, 200, "d:studied/p:studied"),
    ("US", "", "n. North American republic", "abbr. 美国", "", 0, 0, "", 0, 0, ""),
    ("us", "ʌs", "", "pron. 我们", "", 0, 0, "", 0, 20, ""),
    ("give up", "", "", "放弃", "", 0, 0, "", 0, 0, ""),
]
CEDICT_ROWS = [
    ("銀行", "银行", "yin2 hang2", "yín háng", "bank\nCL:家[jia1]"),
    ("我", "我", "wo3", "wǒ", "I; me; my"),
    ("喜歡", "喜欢", "xi3 huan5", "xǐ huan", "to like"),
    ("喜", "喜", "xi3", "xǐ", "to be fond of"),
    ("中文", "中文", "Zhong1 wen2", "Zhōng wén", "Chinese language"),
    ("行", "行", "xing2", "xíng", "to walk"),
    ("行", "行", "Xing2", "Xíng", "surname Xing"),
]


@pytest.fixture(scope="module")
def mini_dict(tmp_path_factory):
    path = tmp_path_factory.mktemp("dict") / "dict.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ecdict (word, phonetic, definition, translation, pos, collins, "
                "oxford, tag, bnc, frq, exchange)")
    con.execute("CREATE TABLE cedict (trad, simp, pinyin_num, pinyin, definitions)")
    con.executemany("INSERT INTO ecdict VALUES (?,?,?,?,?,?,?,?,?,?,?)", ECDICT_ROWS)
    con.executemany("INSERT INTO cedict VALUES (?,?,?,?,?)", CEDICT_ROWS)
    con.commit()
    con.close()
    return Dictionary(path)


def test_missing_db_raises(tmp_path):
    with pytest.raises(DictionaryUnavailable):
        Dictionary(tmp_path / "nope.db")


def test_english_word(mini_dict):
    r = mini_dict.lookup("Apple.")
    assert r.found and r.kind == "word" and r.language == "en"
    assert r.headword == "apple"
    assert r.phonetic_uk == "ˈæpl"
    assert r.senses_en[0].pos == "noun" and len(r.senses_en[0].definitions) == 2
    assert r.meanings_zh[0] == ("n.", "苹果, 家伙")
    assert r.meanings_zh[-1][0] == "[医]"   # domain senses sorted last
    assert "中考" in r.tags


def test_inflected_word_uses_lemma(mini_dict):
    r = mini_dict.lookup("went")
    assert r.lemma == "go"
    assert r.inflection == "past tense of go"
    assert ("vi.", "去, 走") in r.meanings_zh
    assert not any("过去式" in t for _, t in r.meanings_zh)
    assert r.senses_en and r.senses_en[0].pos == "verb"


def test_stemming_fallback(mini_dict):
    r = mini_dict.lookup("studies")
    assert r.found and r.headword == "study"
    assert "stop" in _candidate_bases("stopped")
    assert "happy" in _candidate_bases("happiest")


def test_case_preference(mini_dict):
    assert mini_dict.lookup("US").meanings_zh[0][1] == "美国"
    assert mini_dict.lookup("us").meanings_zh[0][1] == "我们"


def test_phrase(mini_dict):
    r = mini_dict.lookup("give up")
    assert r.found and r.kind == "word"


def test_unknown_and_sentence(mini_dict):
    r = mini_dict.lookup("qwertyuiop")
    assert not r.found and r.kind == "word"
    r = mini_dict.lookup("I really like this, you know.")
    assert r.kind == "sentence" and r.language == "en"
    assert r.query == "I really like this, you know."  # punctuation kept for translation


def test_chinese_word(mini_dict):
    r = mini_dict.lookup("银行")
    assert r.found and r.language == "zh" and r.pinyin == "yín háng"
    assert r.short_en() == ["bank"]  # classifier line skipped
    assert mini_dict.lookup("銀行").found  # traditional input


def test_chinese_ranking(mini_dict):
    r = mini_dict.lookup("行")
    assert r.zh_entries[0].definitions[0] == "to walk"  # surname entry ranked after


def test_chinese_sentence_segmentation(mini_dict):
    r = mini_dict.lookup("我喜欢中文。")
    assert r.kind == "sentence"
    tokens = [t for t, _ in mini_dict.segment_zh("我喜欢中文。")]
    assert tokens == ["我", "喜欢", "中文", "。"]


def test_suggest(mini_dict):
    assert mini_dict.suggest("ap") == ["apple"]
    assert mini_dict.suggest("银") == ["银行"]


def test_fix_ipa():
    assert fix_ipa("ju:'bikwitәs") == "juːˈbikwitəs"


@pytest.mark.parametrize("num,marked", [
    ("ni3 hao3", "nǐ hǎo"), ("lu:4", "lǜ"), ("Zhong1 guo2", "Zhōng guó"),
    ("xi3 huan5", "xǐ huan"), ("gou3", "gǒu"), ("liu2", "liú"), ("er2", "ér"),
])
def test_pinyin(num, marked):
    assert numbered_to_marks(num) == marked


real_db = dict_db_path()


@pytest.mark.skipif(real_db is None, reason="dict.db not built")
def test_real_dictionary_fast():
    import time
    d = Dictionary(real_db)
    t = time.perf_counter()
    for w in ("apple", "went", "ubiquitous", "银行", "running"):
        assert d.lookup(w).found, w
    assert (time.perf_counter() - t) < 0.3  # well within the 300 ms popup budget


def test_pronunciation_guide_english(mini_dict):
    # Known words get IPA, unknown ones are kept as written.
    assert mini_dict.pronunciation_guide("Apple, go!", "en") == "/ˈæpl gəu/"
    assert mini_dict.pronunciation_guide("Blorp apple", "en") == "/blorp ˈæpl/"


def test_pronunciation_guide_chinese(mini_dict):
    assert mini_dict.pronunciation_guide("我喜欢中文了。", "zh") == "wǒ xǐ huan Zhōng wén le"

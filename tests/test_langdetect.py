import pytest

from dictapp.core.langdetect import detect_language, looks_like_single_term, normalize_query, tidy_sentence


@pytest.mark.parametrize("text,lang", [
    ("apple", "en"),
    ("Hello there, how are you?", "en"),
    ("银行", "zh"),
    ("我喜欢学习中文。", "zh"),
    ("用 Python 写代码", "zh"),
    ("The character 人 means person in Chinese", "en"),
    ("銀行", "zh"),            # traditional
    ("12345 !!", "unknown"),
    ("", "unknown"),
])
def test_detect_language(text, lang):
    assert detect_language(text) == lang


@pytest.mark.parametrize("raw,clean", [
    ("  apple.  ", "apple"),
    ("“Hello”", "Hello"),
    ("exam-\nple", "example"),
    ("give\n  up", "give up"),
    ("（银行）", "银行"),
    ("ｆｕｌｌｗｉｄｔｈ", "fullwidth"),
])
def test_normalize_query(raw, clean):
    assert normalize_query(raw) == clean


@pytest.mark.parametrize("text,single", [
    ("apple", True),
    ("well-known", True),
    ("don't", True),
    ("give up", True),
    ("look forward to", True),
    ("The quick brown fox jumps over the lazy dog", False),
    ("Hi, there", False),
    ("银行", True),
    ("我喜欢学习中文。", False),
    ("这是一个很长的句子没有标点符号", False),
])
def test_single_term(text, single):
    assert looks_like_single_term(text) is single


def test_sentences_keep_final_punctuation():
    assert tidy_sentence("  “How are  you?” ") == "How are you?"
    assert tidy_sentence("我喜欢学习中文。") == "我喜欢学习中文。"

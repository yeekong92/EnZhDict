"""Online parsing/merging tests (no network)."""
from dictapp.core.models import Example, LookupResult
from dictapp.core.online import apply_dictionary, apply_examples, parse_free_dictionary

SAMPLE = [{
    "word": "apple",
    "phonetic": "/ˈæp.əl/",
    "phonetics": [
        {"text": "/ˈæp.əl/", "audio": "https://x/apple-uk.mp3"},
        {"text": "/ˈæp.l̩/", "audio": "https://x/apple-us.mp3"},
        {"text": "", "audio": "https://x/apple-au.mp3"},
    ],
    "meanings": [
        {"partOfSpeech": "noun", "definitions": [
            {"definition": "A common, round fruit.", "example": "She ate an apple."},
            {"definition": "The tree."}]},
        {"partOfSpeech": "verb", "definitions": [{"definition": "To apple."}]},
    ],
}]


def test_parse_free_dictionary():
    p = parse_free_dictionary(SAMPLE)
    assert p["phonetic_uk"] == "ˈæp.əl" and p["phonetic_us"] == "ˈæp.l̩"
    assert p["audio_uk"].endswith("-uk.mp3") and p["audio_us"].endswith("-us.mp3")
    assert [s["pos"] for s in p["senses"]] == ["noun", "verb"]
    assert p["examples"] == ["She ate an apple."]


def test_apply_merges_and_dedups():
    res = LookupResult(query="apple", language="en", kind="word", found=True, phonetic_uk="ˈæpl")
    apply_dictionary(res, parse_free_dictionary(SAMPLE))
    assert res.phonetic_uk == "ˈæp.əl" and res.audio_us
    apply_examples(res, [Example("She ate an apple.", "她吃了一个苹果。"), Example("Apples are red.", "蘋果是紅的。")],
                   to_simplified=lambda s: s.replace("蘋", "苹").replace("紅", "红"))
    assert len(res.examples) == 2                     # duplicate merged
    assert res.examples[0].zh == "她吃了一个苹果。"     # duplicate gained its translation
    assert res.examples[1].zh == "苹果是红的。"         # converted to simplified

from dictapp.data.userdb import UserDB


def test_add_dedup_update_delete(tmp_path):
    db = UserDB(tmp_path / "u.db")
    eid, new = db.add_entry(text="Apple", type="word", language="en", meaning_zh="苹果")
    assert new
    eid2, new2 = db.add_entry(text="apple", type="word", language="en")
    assert eid2 == eid and not new2
    db.update_entry(eid, note="red fruit")
    e = db.get_entry(eid)
    assert e.note == "red fruit" and e.due is not None and e.meaning == "苹果"
    assert [x.id for x in db.list_entries("fruit")] == [eid]
    db.delete_entries([eid])
    assert db.get_entry(eid) is None and db.get_review(eid) is None


def test_csv_roundtrip(tmp_path):
    db = UserDB(tmp_path / "a.db")
    db.add_entry(text="apple", type="word", language="en", meaning_zh="苹果, 家伙", note='say "hi"')
    db.add_entry(text="How are you?", type="sentence", language="en", translation="你好吗？")
    assert db.export_csv(tmp_path / "out.csv") == 2
    db2 = UserDB(tmp_path / "b.db")
    assert db2.import_csv(tmp_path / "out.csv") == (2, 0)
    assert db2.import_csv(tmp_path / "out.csv") == (0, 2)  # duplicates skipped
    e = db2.find_entry("apple")
    assert e.meaning_zh == "苹果, 家伙" and e.note == 'say "hi"'
    assert db2.find_entry("How are you?").translation == "你好吗？"


def test_cache(tmp_path):
    db = UserDB(tmp_path / "c.db")
    assert db.cache_get("k") is None
    db.cache_set("k", {"a": [1, "二"]})
    assert db.cache_get("k") == {"a": [1, "二"]}
    assert db.cache_get("k", max_age_days=0) is None

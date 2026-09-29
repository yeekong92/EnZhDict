from datetime import datetime, timedelta, timezone

import pytest
from fsrs import Scheduler, State

from dictapp.core.srs import SRS, describe_interval, end_of_today
from dictapp.data.userdb import UserDB, parse_iso

NOW = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def srs():
    db = UserDB(":memory:")
    return SRS(db, Scheduler(enable_fuzzing=False))


def add(srs, text="apple"):
    eid, _ = srs.db.add_entry(text=text, type="word", language="en", meaning_zh="苹果")
    # Make the new card due "now" in test time.
    srs.db.save_review(eid, {**srs.db.get_review(eid), "due": NOW.isoformat()})
    return eid


def test_new_card_is_due(srs):
    eid = add(srs)
    assert srs.due_ids(NOW) == [eid]
    assert srs.due_count(NOW) == 1


def test_good_graduates_through_learning_steps(srs):
    eid = add(srs)
    due1 = srs.review(eid, "good", NOW)
    assert due1 - NOW == timedelta(minutes=10)  # second learning step
    t2 = NOW + timedelta(minutes=10)
    due2 = srs.review(eid, "good", t2)
    row = srs.db.get_review(eid)
    assert row["state"] == State.Review
    assert row["reps"] == 2 and row["lapses"] == 0
    assert due2 - t2 >= timedelta(days=1)
    assert eid not in srs.due_ids(t2)


def test_easy_beats_good_beats_hard(srs):
    eid = add(srs)
    p = srs.preview(eid, NOW)
    assert set(p) == {"again", "hard", "good", "easy"}
    dues = {}
    for r in ("hard", "good", "easy"):
        db = UserDB(":memory:")
        s = SRS(db, Scheduler(enable_fuzzing=False))
        e = add(s)
        s.review(e, "good", NOW)
        s.review(e, "good", NOW + timedelta(minutes=10))
        later = NOW + timedelta(days=5)
        dues[r] = s.review(e, r, later) - later
    assert dues["hard"] < dues["good"] < dues["easy"]


def test_lapse_counted(srs):
    eid = add(srs)
    srs.review(eid, "easy", NOW)                   # straight to Review
    assert srs.db.get_review(eid)["state"] == State.Review
    later = NOW + timedelta(days=10)
    due = srs.review(eid, "again", later)
    row = srs.db.get_review(eid)
    assert row["lapses"] == 1 and row["state"] == State.Relearning
    assert due - later == timedelta(minutes=10)


def test_state_roundtrips_through_db(srs):
    eid = add(srs)
    srs.review(eid, "good", NOW)
    row = srs.db.get_review(eid)
    assert parse_iso(row["due"]) == NOW + timedelta(minutes=10)
    assert row["stability"] > 0 and 1 <= row["difficulty"] <= 10


def test_end_of_today_is_after_now():
    eod = end_of_today(NOW)
    assert NOW < eod <= NOW + timedelta(days=1)


def test_describe_interval():
    assert describe_interval(timedelta(minutes=10)) == "10m"
    assert describe_interval(timedelta(hours=5)) == "5h"
    assert describe_interval(timedelta(days=3)) == "3d"
    assert describe_interval(timedelta(days=90)) == "3.0mo"

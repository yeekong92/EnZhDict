"""FSRS scheduling on top of the `reviews` table."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fsrs import Card, Rating, Scheduler, State

from ..data.userdb import UserDB, iso, parse_iso, utcnow

RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}


def end_of_today(now: datetime | None = None) -> datetime:
    """End of the user's local calendar day, as an aware UTC datetime."""
    local = (now or utcnow()).astimezone()
    end = datetime.combine(local.date(), time.max, tzinfo=local.tzinfo)
    return end.astimezone(timezone.utc)


def card_from_row(row: dict) -> Card:
    return Card(
        card_id=row["entry_id"],
        state=State(row.get("state") or State.Learning),
        step=row.get("step") if row.get("state", 1) != State.Review else None,
        stability=row.get("stability"),
        difficulty=row.get("difficulty"),
        due=parse_iso(row["due"]),
        last_review=parse_iso(row.get("last_review")),
    )


def describe_interval(delta: timedelta) -> str:
    secs = max(0, delta.total_seconds())
    if secs < 3600:
        return f"{max(1, round(secs / 60))}m"
    if secs < 86400:
        return f"{round(secs / 3600)}h"
    days = secs / 86400
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{days / 30:.1f}mo"
    return f"{days / 365:.1f}y"


class SRS:
    def __init__(self, db: UserDB, scheduler: Scheduler | None = None):
        self.db = db
        self.scheduler = scheduler or Scheduler()

    def due_ids(self, now: datetime | None = None) -> list[int]:
        return self.db.due_entry_ids(end_of_today(now))

    def due_count(self, now: datetime | None = None) -> int:
        return len(self.due_ids(now))

    def preview(self, entry_id: int, now: datetime | None = None) -> dict[str, str]:
        """Interval label for each rating button, e.g. {"good": "3d"}."""
        now = now or utcnow()
        row = self.db.get_review(entry_id)
        if not row:
            return {}
        card = card_from_row(row)
        out = {}
        for name, rating in RATINGS.items():
            new, _ = self.scheduler.review_card(card, rating, now)
            out[name] = describe_interval(new.due - now)
        return out

    def review(self, entry_id: int, rating: str | Rating, now: datetime | None = None) -> datetime:
        """Apply a rating, persist the new state and return the next due time."""
        now = now or utcnow()
        if isinstance(rating, str):
            rating = RATINGS[rating.lower()]
        row = self.db.get_review(entry_id)
        if row is None:
            raise KeyError(entry_id)
        card = card_from_row(row)
        new, _ = self.scheduler.review_card(card, rating, now)
        lapse = rating == Rating.Again and card.state == State.Review
        self.db.save_review(entry_id, {
            "due": iso(new.due),
            "stability": new.stability,
            "difficulty": new.difficulty,
            "reps": (row.get("reps") or 0) + 1,
            "lapses": (row.get("lapses") or 0) + int(lapse),
            "last_review": iso(new.last_review),
            "state": int(new.state),
            "step": new.step,
        })
        return new.due

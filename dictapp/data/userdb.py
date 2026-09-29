"""User database: saved entries, FSRS review state, and the online-lookup cache."""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.langdetect import detect_language, looks_like_single_term

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('word', 'sentence')),
    language    TEXT NOT NULL,
    meaning_en  TEXT DEFAULT '',
    meaning_zh  TEXT DEFAULT '',
    phonetic_uk TEXT DEFAULT '',
    phonetic_us TEXT DEFAULT '',
    pinyin      TEXT DEFAULT '',
    translation TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entries_text ON entries(text COLLATE NOCASE, type);

CREATE TABLE IF NOT EXISTS reviews (
    entry_id    INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
    due         TEXT NOT NULL,
    stability   REAL,
    difficulty  REAL,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    last_review TEXT,
    state       INTEGER NOT NULL DEFAULT 1,
    step        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_reviews_due ON reviews(due);

CREATE TABLE IF NOT EXISTS cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""

ENTRY_COLS = ("id", "text", "type", "language", "meaning_en", "meaning_zh", "phonetic_uk",
              "phonetic_us", "pinyin", "translation", "note", "created_at")
EDITABLE = {"text", "meaning_en", "meaning_zh", "phonetic_uk", "phonetic_us", "pinyin",
            "translation", "note"}
CSV_COLS = ("text", "type", "language", "meaning_en", "meaning_zh", "phonetic_uk",
            "phonetic_us", "pinyin", "translation", "note", "created_at", "due")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Entry:
    id: int
    text: str
    type: str
    language: str
    meaning_en: str
    meaning_zh: str
    phonetic_uk: str
    phonetic_us: str
    pinyin: str
    translation: str
    note: str
    created_at: str
    due: str | None = None

    @property
    def meaning(self) -> str:
        if self.type == "sentence":
            return self.translation
        if self.language == "en":
            return self.meaning_zh or self.meaning_en
        return self.meaning_en


class UserDB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA foreign_keys=ON")
            self._con.executescript(SCHEMA)
            self._con.commit()

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # ------------------------------------------------------------- entries
    def add_entry(self, *, text: str, type: str, language: str, meaning_en: str = "",
                  meaning_zh: str = "", phonetic_uk: str = "", phonetic_us: str = "",
                  pinyin: str = "", translation: str = "", note: str = "",
                  created_at: str | None = None, due: str | None = None) -> tuple[int, bool]:
        """Insert (or return existing) entry. Returns (id, created)."""
        text = text.strip()
        with self._lock:
            row = self._con.execute(
                "SELECT id FROM entries WHERE text = ? COLLATE NOCASE AND type = ?",
                (text, type)).fetchone()
            if row:
                return row["id"], False
            created = created_at or iso(utcnow())
            cur = self._con.execute(
                "INSERT INTO entries (text, type, language, meaning_en, meaning_zh, phonetic_uk, "
                "phonetic_us, pinyin, translation, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (text, type, language, meaning_en, meaning_zh, phonetic_uk, phonetic_us,
                 pinyin, translation, note, created))
            eid = cur.lastrowid
            self._con.execute("INSERT INTO reviews (entry_id, due, state) VALUES (?, ?, 1)",
                              (eid, due or iso(utcnow())))
            self._con.commit()
            return eid, True

    def find_entry(self, text: str) -> Entry | None:
        rows = self._select("WHERE e.text = ? COLLATE NOCASE", (text.strip(),))
        return rows[0] if rows else None

    def get_entry(self, entry_id: int) -> Entry | None:
        rows = self._select("WHERE e.id = ?", (entry_id,))
        return rows[0] if rows else None

    def list_entries(self, search: str = "") -> list[Entry]:
        if search:
            like = f"%{search}%"
            return self._select(
                "WHERE e.text LIKE ? OR e.meaning_en LIKE ? OR e.meaning_zh LIKE ? "
                "OR e.translation LIKE ? OR e.note LIKE ? ORDER BY e.created_at DESC",
                (like,) * 5)
        return self._select("ORDER BY e.created_at DESC")

    def update_entry(self, entry_id: int, **fields) -> None:
        bad = set(fields) - EDITABLE
        if bad:
            raise ValueError(f"not editable: {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._con.execute(f"UPDATE entries SET {sets} WHERE id = ?", (*fields.values(), entry_id))
            self._con.commit()

    def delete_entries(self, ids: list[int]) -> None:
        with self._lock:
            self._con.executemany("DELETE FROM entries WHERE id = ?", [(i,) for i in ids])
            self._con.commit()

    def _select(self, where: str, args: tuple = ()) -> list[Entry]:
        cols = ", ".join(f"e.{c}" for c in ENTRY_COLS)
        with self._lock:
            rows = self._con.execute(
                f"SELECT {cols}, r.due FROM entries e LEFT JOIN reviews r ON r.entry_id = e.id {where}",
                args).fetchall()
        return [Entry(**dict(r)) for r in rows]

    # ------------------------------------------------------------- reviews
    def get_review(self, entry_id: int) -> dict | None:
        with self._lock:
            row = self._con.execute("SELECT * FROM reviews WHERE entry_id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def save_review(self, entry_id: int, state: dict) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO reviews (entry_id, due, stability, difficulty, reps, lapses, "
                "last_review, state, step) VALUES (:entry_id, :due, :stability, :difficulty, "
                ":reps, :lapses, :last_review, :state, :step) "
                "ON CONFLICT(entry_id) DO UPDATE SET due=excluded.due, stability=excluded.stability, "
                "difficulty=excluded.difficulty, reps=excluded.reps, lapses=excluded.lapses, "
                "last_review=excluded.last_review, state=excluded.state, step=excluded.step",
                {"entry_id": entry_id, **state})
            self._con.commit()

    def due_entry_ids(self, until: datetime) -> list[int]:
        with self._lock:
            rows = self._con.execute(
                "SELECT entry_id FROM reviews WHERE due <= ? ORDER BY due", (iso(until),)).fetchall()
        return [r[0] for r in rows]

    # --------------------------------------------------------------- cache
    def cache_get(self, key: str, max_age_days: float | None = None):
        with self._lock:
            row = self._con.execute("SELECT value, created_at FROM cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        if max_age_days is not None and time.time() - row["created_at"] >= max_age_days * 86400:
            return None
        return json.loads(row["value"])

    def cache_set(self, key: str, value) -> None:
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?,?,?)",
                              (key, json.dumps(value, ensure_ascii=False), time.time()))
            self._con.commit()

    # ------------------------------------------------------------ CSV I/O
    def export_csv(self, path: Path | str) -> int:
        entries = self.list_entries()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLS)
            w.writeheader()
            for e in entries:
                w.writerow({c: getattr(e, c) or "" for c in CSV_COLS})
        return len(entries)

    def import_csv(self, path: Path | str) -> tuple[int, int]:
        """Import rows; returns (added, skipped_duplicates_or_invalid)."""
        added = skipped = 0
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                text = (row.get("text") or row.get("Item") or "").strip()
                if not text:
                    skipped += 1
                    continue
                typ = (row.get("type") or "").strip().lower()
                if typ not in ("word", "sentence"):
                    typ = "word" if looks_like_single_term(text) else "sentence"
                lang = (row.get("language") or "").strip() or detect_language(text)
                fields = {k: (row.get(k) or "").strip() for k in
                          ("meaning_en", "meaning_zh", "phonetic_uk", "phonetic_us", "pinyin",
                           "translation", "note")}
                created = (row.get("created_at") or "").strip() or None
                due = (row.get("due") or "").strip() or None
                try:
                    if created:
                        parse_iso(created)
                    if due:
                        parse_iso(due)
                except ValueError:
                    created = due = None
                _, new = self.add_entry(text=text, type=typ, language=lang, created_at=created,
                                        due=due, **fields)
                added += new
                skipped += not new
        return added, skipped

"""Download ECDICT + CC-CEDICT + Wiktionary Tagalog and build the offline dictionary database.

Usage:
    python scripts/build_dict.py                 # download (if needed) and build resources/dict.db
    python scripts/build_dict.py --add-tagalog   # only add/refresh the Tagalog tables in an existing dict.db
    python scripts/build_dict.py --ecdict ecdict.csv --cedict cedict.txt.gz --tagalog tagalog.jsonl

Sources:
    ECDICT    https://github.com/skywind3000/ECDICT        (MIT)
    CC-CEDICT https://www.mdbg.net/chinese/dictionary?page=cedict  (CC BY-SA 4.0)
    Tagalog   https://kaikki.org/dictionary/Tagalog/  (Wiktionary extract, CC BY-SA 4.0)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictapp.core.pinyin import numbered_to_marks  # noqa: E402
from dictapp.data.tagalog_import import load_tagalog  # noqa: E402

ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
TAGALOG_URL = "https://kaikki.org/dictionary/Tagalog/kaikki.org-dictionary-Tagalog.jsonl"
RAW_DIR = ROOT / "resources" / "raw"
OUT = ROOT / "resources" / "dict.db"


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  using cached {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "EnZhDict-builder/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
            else:
                print(f"\r    {done / 1e6:6.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)
    return dest


def _int(v: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def load_ecdict(con: sqlite3.Connection, path: Path) -> int:
    con.execute("""CREATE TABLE ecdict (
        word TEXT NOT NULL, phonetic TEXT, definition TEXT, translation TEXT,
        pos TEXT, collins INTEGER, oxford INTEGER, tag TEXT, bnc INTEGER,
        frq INTEGER, exchange TEXT)""")
    csv.field_size_limit(10_000_000)
    rows = []
    n = 0
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            word = (r.get("word") or "").strip()
            definition = (r.get("definition") or "").replace("\\n", "\n").strip()
            translation = (r.get("translation") or "").replace("\\n", "\n").strip()
            if not word or not (definition or translation):
                continue
            rows.append((
                word, (r.get("phonetic") or "").strip(), definition, translation,
                r.get("pos") or "", _int(r.get("collins")), _int(r.get("oxford")),
                r.get("tag") or "", _int(r.get("bnc")), _int(r.get("frq")),
                r.get("exchange") or "",
            ))
            if len(rows) >= 50_000:
                con.executemany("INSERT INTO ecdict VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
                n += len(rows)
                rows.clear()
    con.executemany("INSERT INTO ecdict VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    n += len(rows)
    con.execute("CREATE INDEX ix_ecdict_word ON ecdict(word COLLATE NOCASE)")
    return n


def load_cedict(con: sqlite3.Connection, path: Path) -> int:
    con.execute("""CREATE TABLE cedict (
        trad TEXT NOT NULL, simp TEXT NOT NULL, pinyin_num TEXT,
        pinyin TEXT, definitions TEXT)""")
    opener = gzip.open if path.suffix == ".gz" else open
    rows = []
    with opener(path, "rb") as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            try:
                head, rest = line.split(" [", 1)
                trad, simp = head.split(" ", 1)
                pinyin_num, defs = rest.split("] /", 1)
            except ValueError:
                continue
            defs = [d for d in defs.strip().strip("/").split("/") if d]
            rows.append((trad, simp, pinyin_num, numbered_to_marks(pinyin_num), "\n".join(defs)))
    con.executemany("INSERT INTO cedict VALUES (?,?,?,?,?)", rows)
    con.execute("CREATE INDEX ix_cedict_simp ON cedict(simp)")
    con.execute("CREATE INDEX ix_cedict_trad ON cedict(trad)")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ecdict", type=Path, help="local ecdict.csv (skip download)")
    ap.add_argument("--cedict", type=Path, help="local cedict_1_0_ts_utf-8_mdbg.txt[.gz] (skip download)")
    ap.add_argument("--tagalog", type=Path, help="local kaikki Tagalog .jsonl (skip download)")
    ap.add_argument("--add-tagalog", action="store_true",
                    help="only (re)build the Tagalog tables inside an existing dict.db")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    t = time.time()

    if args.add_tagalog:
        if not args.out.exists():
            sys.exit(f"{args.out} doesn't exist yet - run without --add-tagalog first.")
        tagalog = args.tagalog or download(TAGALOG_URL, RAW_DIR / "kaikki-tagalog.jsonl")
        con = sqlite3.connect(args.out)
        con.execute("DROP TABLE IF EXISTS tagalog")
        con.execute("DROP TABLE IF EXISTS tl_rev")
        n_tl, n_rev = load_tagalog(con, tagalog)
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('tagalog_rows', ?)", (str(n_tl),))
        con.commit()
        con.close()
        print(f"  {n_tl:,} Tagalog entries, {n_rev:,} English->Tagalog links")
        print(f"Done in {time.time() - t:.0f}s -> {args.out}")
        return

    print("[1/4] Fetching sources")
    ecdict = args.ecdict or download(ECDICT_URL, RAW_DIR / "ecdict.csv")
    cedict = args.cedict or download(CEDICT_URL, RAW_DIR / "cedict_1_0_ts_utf-8_mdbg.txt.gz")
    tagalog = args.tagalog or download(TAGALOG_URL, RAW_DIR / "kaikki-tagalog.jsonl")

    tmp_out = args.out.with_suffix(".building")
    tmp_out.unlink(missing_ok=True)
    con = sqlite3.connect(tmp_out)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    print("[2/4] Importing ECDICT ...")
    n_ec = load_ecdict(con, ecdict)
    print(f"  {n_ec:,} English entries")
    print("[3/4] Importing CC-CEDICT ...")
    n_ce = load_cedict(con, cedict)
    print(f"  {n_ce:,} Chinese entries")
    print("[4/4] Importing Wiktionary Tagalog ...")
    n_tl, n_rev = load_tagalog(con, tagalog)
    print(f"  {n_tl:,} Tagalog entries, {n_rev:,} English->Tagalog links")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("ecdict_rows", str(n_ec)), ("cedict_rows", str(n_ce)), ("tagalog_rows", str(n_tl)),
    ])
    con.commit()
    con.execute("VACUUM")
    con.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out.replace(args.out)
    print(f"Done in {time.time() - t:.0f}s -> {args.out} ({args.out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()

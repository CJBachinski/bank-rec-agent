"""SQLite access. The schema lives in sql/schema.sql; this is the thin layer over it."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "schema.sql"


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'bank_txn'").fetchone():
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def add_group(conn, month, status, source, bank_ids, gl_ids, *, rule="", reason_code="",
              explanation="", confidence=None) -> int:
    """Record one decision. Fails if any line already belongs to another decision."""
    with conn:
        cur = conn.execute(
            "INSERT INTO match_group (month, status, source, rule, reason_code, explanation, confidence) "
            "VALUES (?,?,?,?,?,?,?)", (month, status, source, rule, reason_code, explanation, confidence))
        conn.executemany("INSERT INTO match_line VALUES (?,?,?)",
                         [(cur.lastrowid, "bank", b) for b in bank_ids]
                         + [(cur.lastrowid, "gl", g) for g in gl_ids])
    return cur.lastrowid


def groups(conn, month: str | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM match_group WHERE (? IS NULL OR month = ?) ORDER BY id",
                        (month, month)).fetchall()
    out = []
    for r in rows:
        lines = conn.execute("SELECT side, txn_id FROM match_line WHERE group_id = ? ORDER BY txn_id",
                             (r["id"],)).fetchall()
        out.append({**dict(r),
                    "bank_ids": [x["txn_id"] for x in lines if x["side"] == "bank"],
                    "gl_ids": [x["txn_id"] for x in lines if x["side"] == "gl"]})
    return out


def open_bank(conn, month: str) -> list[sqlite3.Row]:
    """Bank lines nobody has decided anything about yet."""
    return conn.execute("SELECT * FROM bank_status WHERE month = ? AND status = 'open' "
                        "ORDER BY account, posted_date, id", (month,)).fetchall()


def open_gl(conn, month: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM gl_status WHERE month = ? AND status = 'open' "
                        "ORDER BY account, entry_date, id", (month,)).fetchall()

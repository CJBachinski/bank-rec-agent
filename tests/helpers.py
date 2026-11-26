"""Test-only helpers: tiny hand-built ledgers, and applying an answer key as if a perfect matcher ran."""
from recon import db


def tiny(bank=(), gl=(), month="2026-09"):
    """bank: (id, account, date, description, cents[, check_no])
    gl:   (id, account, date, txn_type, num, name, memo, cents[, prior])"""
    conn = db.connect()
    for i, row in enumerate(bank):
        id_, account, date, description, cents, *rest = row
        conn.execute("INSERT INTO bank_txn VALUES (?,?,?,?,?,?,?,?,?)",
                     (id_, month, account, date, description, cents, rest[0] if rest else "", "test", i))
    for i, row in enumerate(gl):
        id_, account, date, txn_type, num, name, memo, cents, *rest = row
        conn.execute("INSERT INTO gl_entry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (id_, month, account, date, txn_type, num, name, memo, cents,
                      rest[0] if rest else 0, "test", i))
    return conn


def apply_key(conn, key):
    """Record every answer-key group as its expected outcome."""
    for g in key["groups"]:
        if g["expected"] == "match":
            db.add_group(conn, key["month"], "cleared", "person", g["bank_ids"], g["gl_ids"])
        elif g["expected"] == "bank_only":
            db.add_group(conn, key["month"], "bank_only", "person", g["bank_ids"], [],
                         reason_code=g["reason_code"])
        elif g["expected"] == "review":
            db.add_group(conn, key["month"], "review", "person", g["bank_ids"], g["gl_ids"],
                         reason_code=g["reason_code"])

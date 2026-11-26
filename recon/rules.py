"""The deterministic pass: clear what cannot be argued with, and leave everything else alone.

Two rules, both exact:

  1. Check number. A bank check and a ledger check with the same number and the
     same amount are the same check, however long it took to clear. Same number,
     different amount is a keying error: it goes to a person, never cleared.

  2. Exact and unique. Same account, same signed amount to the cent, dates within
     WINDOW_DAYS, and exactly one candidate in each direction. If two ledger
     entries could be the one, or two bank lines could claim the same entry, the
     rule does not guess.

A ledger check is only ever matched by rule 1. A bank debit that happens to equal
an outstanding check is not that check.
"""
from __future__ import annotations

from collections import Counter

from recon import db

WINDOW_DAYS = 4   # a Thursday payment that posts two business days later lands on Monday

CHECK_PAIRS = """
SELECT b.id AS bank_id, b.amount_cents AS bank_amount, g.id AS gl_id, g.amount_cents AS gl_amount
FROM bank_status b
JOIN gl_status g ON g.month = b.month AND g.account = b.account
                AND g.txn_type = 'Check' AND g.num = b.check_no
WHERE b.month = ? AND b.status = 'open' AND g.status = 'open' AND b.check_no <> ''
ORDER BY b.id, g.id
"""

EXACT_PAIRS = """
SELECT b.id AS bank_id, g.id AS gl_id
FROM bank_status b
JOIN gl_status g ON g.month = b.month AND g.account = b.account
                AND g.amount_cents = b.amount_cents
                AND ABS(julianday(b.posted_date) - julianday(g.entry_date)) <= ?
WHERE b.month = ? AND b.status = 'open' AND g.status = 'open'
  AND b.check_no = '' AND g.txn_type <> 'Check' AND g.prior_period = 0
ORDER BY b.id, g.id
"""


def run(conn, month: str) -> dict[str, int]:
    done = Counter()

    pairs = conn.execute(CHECK_PAIRS, (month,)).fetchall()
    per_bank = Counter(p["bank_id"] for p in pairs)
    per_gl = Counter(p["gl_id"] for p in pairs)
    for p in pairs:
        if per_bank[p["bank_id"]] != 1 or per_gl[p["gl_id"]] != 1:
            continue                                    # a check number used twice: not ours to call
        if p["bank_amount"] == p["gl_amount"]:
            db.add_group(conn, month, "cleared", "rule", [p["bank_id"]], [p["gl_id"]], rule="check_number")
            done["cleared"] += 1
        else:
            db.add_group(conn, month, "review", "rule", [p["bank_id"]], [p["gl_id"]], rule="check_number",
                         reason_code="amount_mismatch",
                         explanation=f"Check number agrees but the bank paid {abs(p['bank_amount']) / 100:,.2f} "
                                     f"and the books show {abs(p['gl_amount']) / 100:,.2f}.")
            done["review"] += 1

    pairs = conn.execute(EXACT_PAIRS, (WINDOW_DAYS, month)).fetchall()
    per_bank = Counter(p["bank_id"] for p in pairs)
    per_gl = Counter(p["gl_id"] for p in pairs)
    for p in pairs:
        if per_bank[p["bank_id"]] == 1 and per_gl[p["gl_id"]] == 1:
            db.add_group(conn, month, "cleared", "rule", [p["bank_id"]], [p["gl_id"]], rule="exact_unique")
            done["cleared"] += 1
    return dict(done)

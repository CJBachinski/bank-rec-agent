"""The guard between a proposal and the books.

A proposal says: these bank lines and these ledger entries are the same money,
and here is what I read that makes me think so. Nothing in it is taken on trust.
Every id is looked up, every quoted value is compared with the stored value, and
the arithmetic is done here, in integer cents, never by the model.

A proposal that fails any check is not recorded as cleared. It goes to a person
with the reason attached.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from recon import ingest

MAX_DAYS_APART = 45
DECISIONS = {"match", "no_match", "needs_review"}
BANK_FIELDS = {"amount": "amount_cents", "date": "posted_date", "description": "description",
               "check_no": "check_no"}
GL_FIELDS = {"amount": "amount_cents", "date": "entry_date", "name": "name", "memo": "memo",
             "num": "num", "txn_type": "txn_type"}


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str


def _lookup(conn, ids, view):
    found = {}
    for i in ids:
        row = conn.execute(f"SELECT * FROM {view} WHERE id = ?", (i,)).fetchone()
        if row:
            found[i] = row
    return found


def _evidence(conn, items) -> list[Violation]:
    out = []
    for e in items or []:
        txn_id, name, value = e.get("txn_id", ""), e.get("field", ""), str(e.get("value", ""))
        row = (conn.execute("SELECT * FROM bank_txn WHERE id = ?", (txn_id,)).fetchone()
               or conn.execute("SELECT * FROM gl_entry WHERE id = ?", (txn_id,)).fetchone())
        if row is None:
            out.append(Violation("unknown_id", f"evidence cites {txn_id}, which does not exist"))
            continue
        fields = BANK_FIELDS if "posted_date" in row.keys() else GL_FIELDS
        if name not in fields:
            out.append(Violation("unknown_field", f"{txn_id} has no field called {name!r}"))
            continue
        stored = row[fields[name]]
        if name == "amount":
            try:
                ok = abs(ingest.parse_money(value)) == abs(stored)
            except ingest.IngestError:
                ok = False
            shown = f"{abs(stored) / 100:,.2f}"
        elif name == "date":
            try:
                ok = ingest._date(value) == stored
            except ingest.IngestError:
                ok = False
            shown = stored
        else:
            ok = bool(value.strip()) and value.strip().casefold() in stored.casefold()
            shown = stored
        if not ok:
            out.append(Violation("evidence_mismatch",
                                 f"{txn_id}.{name} is {shown!r}, proposal quotes {value!r}"))
    return out


def check(conn, proposal: dict) -> list[Violation]:
    decision = proposal.get("decision")
    bank_ids, gl_ids = list(proposal.get("bank_ids") or []), list(proposal.get("gl_ids") or [])
    if decision not in DECISIONS:
        return [Violation("malformed", f"unknown decision {decision!r}")]
    if not bank_ids or len(set(bank_ids)) != len(bank_ids) or len(set(gl_ids)) != len(gl_ids):
        return [Violation("malformed", "a proposal needs at least one bank line and no repeated ids")]
    if decision == "match" and not gl_ids:
        return [Violation("malformed", "a match needs at least one ledger entry")]
    if decision == "no_match" and gl_ids:
        return [Violation("malformed", "no_match must not name ledger entries")]

    bank = _lookup(conn, bank_ids, "bank_status")
    gl = _lookup(conn, gl_ids, "gl_status")
    missing = [i for i in bank_ids if i not in bank] + [i for i in gl_ids if i not in gl]
    out = [Violation("unknown_id", f"{i} does not exist") for i in missing]
    out += _evidence(conn, proposal.get("evidence"))
    if missing or decision != "match":
        return out

    taken = [i for i, r in {**bank, **gl}.items() if r["status"] != "open"]
    if taken:
        out.append(Violation("already_matched", f"already decided: {', '.join(sorted(taken))}"))
    if len({r["account"] for r in list(bank.values()) + list(gl.values())}) > 1:
        out.append(Violation("account_mismatch", "bank lines and ledger entries are in different accounts"))

    bank_total = sum(r["amount_cents"] for r in bank.values())
    gl_total = sum(r["amount_cents"] for r in gl.values())
    if bank_total != gl_total:
        out.append(Violation("does_not_tie", f"bank {bank_total / 100:,.2f} vs ledger {gl_total / 100:,.2f}: "
                                             f"difference {abs(bank_total - gl_total) / 100:,.2f}"))

    bank_checks = {r["check_no"] for r in bank.values() if r["check_no"]}
    for i, r in gl.items():
        if r["txn_type"] == "Check" and r["num"] not in bank_checks:
            out.append(Violation("check_number_mismatch",
                                 f"{i} is check {r['num']}; no bank line in the group is that check"))

    bank_dates = [date.fromisoformat(r["posted_date"]) for r in bank.values()]
    for i, r in gl.items():
        d = date.fromisoformat(r["entry_date"])
        if min(abs((d - b).days) for b in bank_dates) > MAX_DAYS_APART and r["txn_type"] != "Check":
            out.append(Violation("date_out_of_policy", f"{i} is more than {MAX_DAYS_APART} days from the bank date"))
    return out

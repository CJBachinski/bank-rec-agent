"""The reconciliation a person reviews and signs.

For each account: the bank's ending balance, adjusted for what the books have
that the bank has not seen; the books' ending balance, adjusted for what the bank
has that the books have not recorded; and the list of items behind each
adjustment. The two adjusted balances agree whenever every cleared group ties,
so agreement alone proves little. The reconciliation is finished when the
"unexplained" list is empty and every item on the other lists is one a person
accepts.
"""
from __future__ import annotations


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}{dollars:,}.{rem:02d}"


def _bank(r) -> dict:
    return {"id": r["id"], "date": r["posted_date"], "text": r["description"],
            "amount": r["amount_cents"], "reason_code": r["reason_code"]}


def _gl(r) -> dict:
    label = " ".join(x for x in (r["txn_type"], r["num"], r["name"] or r["memo"]) if x)
    return {"id": r["id"], "date": r["entry_date"], "text": label, "amount": r["amount_cents"],
            "reason_code": r["reason_code"], "prior_period": bool(r["prior_period"])}


def reconcile(conn, month: str) -> list[dict]:
    out = []
    for t in conn.execute("SELECT * FROM tie_out WHERE month = ? ORDER BY account", (month,)):
        args = (month, t["account"])
        bank = conn.execute("SELECT * FROM bank_status WHERE month = ? AND account = ? "
                            "ORDER BY posted_date, id", args).fetchall()
        gl = conn.execute("SELECT * FROM gl_status WHERE month = ? AND account = ? "
                          "ORDER BY entry_date, id", args).fetchall()
        review = {}
        for r in bank:
            if r["status"] == "review":
                review.setdefault(r["group_id"], {"bank": [], "gl": []})["bank"].append(_bank(r))
        for r in gl:
            if r["status"] == "review":
                review.setdefault(r["group_id"], {"bank": [], "gl": []})["gl"].append(_gl(r))
        for gid, g in review.items():
            row = conn.execute("SELECT reason_code, explanation FROM match_group WHERE id = ?", (gid,)).fetchone()
            g.update(reason_code=row["reason_code"], explanation=row["explanation"],
                     difference=sum(x["amount"] for x in g["bank"]) - sum(x["amount"] for x in g["gl"]))
        out.append({
            "account": t["account"], "ledger_account": t["ledger_account"],
            "bank_end": t["bank_end"], "book_end": t["book_end"],
            "ledger_not_at_bank": [_gl(r) for r in gl if r["status"] == "open"],
            "bank_not_in_books": [_bank(r) for r in bank if r["status"] == "bank_only"],
            "needs_decision": list(review.values()),
            "unexplained_bank_items": [_bank(r) for r in bank if r["status"] == "open"],
            "cleared_bank_lines": sum(r["status"] == "cleared" for r in bank),
            "bank_lines": len(bank),
            "adjusted_bank": t["bank_end"] + t["ledger_not_at_bank"],
            "adjusted_book": t["book_end"] + t["bank_not_in_books"] + t["unexplained_bank"] + t["pending_decision"],
        })
    return out


def _table(items) -> list[str]:
    if not items:
        return ["_None._", ""]
    lines = ["| Id | Date | Item | Amount |", "|---|---|---|---:|"]
    lines += [f"| {x['id']} | {x['date']} | {x['text']} | {money(x['amount'])} |" for x in items]
    lines += [f"| | | **Total** | **{money(sum(x['amount'] for x in items))}** |", ""]
    return lines


def to_markdown(conn, month: str) -> str:
    lines = [f"# Bank reconciliation, {month}", "",
             "Synthetic data for a fictional company. Amounts in dollars.", ""]
    for a in reconcile(conn, month):
        lines += [f"## {a['ledger_account']}", "",
                  f"{a['cleared_bank_lines']} of {a['bank_lines']} bank lines cleared. "
                  f"{len(a['unexplained_bank_items'])} unexplained.", "",
                  "| | Amount |", "|---|---:|",
                  f"| Bank ending balance | {money(a['bank_end'])} |",
                  f"| Ledger items not yet at the bank | {money(a['adjusted_bank'] - a['bank_end'])} |",
                  f"| **Adjusted bank balance** | **{money(a['adjusted_bank'])}** |",
                  f"| Book ending balance | {money(a['book_end'])} |",
                  f"| Bank items not yet in the books | {money(sum(x['amount'] for x in a['bank_not_in_books']))} |",
                  f"| Items waiting on a decision | {money(sum(g['difference'] for g in a['needs_decision']))} |",
                  f"| Unexplained bank items | {money(sum(x['amount'] for x in a['unexplained_bank_items']))} |",
                  f"| **Adjusted book balance** | **{money(a['adjusted_book'])}** |", "",
                  "### Unexplained bank items", ""] + _table(a["unexplained_bank_items"])
        lines += ["### Waiting on a decision", ""]
        if not a["needs_decision"]:
            lines += ["_None._", ""]
        for g in a["needs_decision"]:
            ids = ", ".join(x["id"] for x in g["bank"] + g["gl"])
            lines += [f"- **{g['reason_code']}** ({ids}): difference {money(g['difference'])}. {g['explanation']}"]
        lines += ["", "### Bank items the books need an entry for", ""] + _table(a["bank_not_in_books"])
        lines += ["### Ledger items not yet at the bank", ""] + _table(a["ledger_not_at_bank"])
    return "\n".join(lines).rstrip() + "\n"

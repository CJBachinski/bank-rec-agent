"""Grade a finished run against the answer key, one bank line at a time.

Four verdicts, because the mistakes do not cost the same:

  correct          the line ended where the key says it should
  false_clear      the line was cleared against the wrong entries, or cleared when
                   it should not have been cleared at all. This is the expensive
                   error: nobody looks at a cleared line again.
  left_for_person  the line was not cleared and a person will see it. Safe, but it
                   is the work the tool was supposed to remove.
  wrong_label      not cleared, but filed under the wrong heading (a real match
                   called a bank-only item, say). A person sees it, with a
                   misleading note attached.
"""
from __future__ import annotations

from collections import Counter

from recon import db

VERDICTS = ("correct", "false_clear", "left_for_person", "wrong_label")


def _acceptable_ledger_sets(group: dict) -> list[set[str]]:
    """The key's entries, or the same with a duplicated entry swapped for its twin."""
    sets = [set(group["gl_ids"])]
    for twin in group.get("duplicate_gl_ids", []):
        sets += [(set(group["gl_ids"]) - {original}) | {twin} for original in group["gl_ids"]]
    return sets


def _verdict(truth: dict, got: dict | None) -> str:
    status = got["status"] if got else "open"
    if status == "cleared":
        right = (truth["expected"] == "match" and set(got["bank_ids"]) == set(truth["bank_ids"])
                 and set(got["gl_ids"]) in _acceptable_ledger_sets(truth))
        return "correct" if right else "false_clear"
    if truth["expected"] == "match":
        return "wrong_label" if status == "bank_only" else "left_for_person"
    if truth["expected"] == "bank_only":
        return "correct" if status == "bank_only" else "left_for_person"
    # expected review: a person must decide
    if status == "review":
        return "correct"
    return "wrong_label" if status == "bank_only" else "left_for_person"


def score_month(conn, key: dict) -> dict:
    by_bank = {b: g for g in db.groups(conn, key["month"]) for b in g["bank_ids"]}
    detail, counts, by_category = [], Counter(), {}
    for truth in key["groups"]:
        for bank_id in truth["bank_ids"]:
            got = by_bank.get(bank_id)
            verdict = _verdict(truth, got)
            counts[verdict] += 1
            by_category.setdefault(truth["category"], Counter())[verdict] += 1
            detail.append({
                "bank_id": bank_id, "category": truth["category"], "expected": truth["expected"],
                "got": got["status"] if got else "open", "source": got["source"] if got else "",
                "got_gl_ids": got["gl_ids"] if got else [], "want_gl_ids": truth["gl_ids"],
                "verdict": verdict,
            })
    lines = len(detail)
    return {
        "month": key["month"], "lines": lines,
        **{v: counts[v] for v in VERDICTS},
        "accuracy": counts["correct"] / lines if lines else 0.0,
        "by_category": {c: dict(v) for c, v in sorted(by_category.items())},
        "detail": detail,
    }

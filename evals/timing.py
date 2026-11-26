"""Measuring a person: how long the reconciliation takes by hand, and how long the review queue takes.

The time-saved number in the README comes from two timed sessions by one person,
recorded by scripts/time_manual.py and scripts/time_review.py. This module holds
the parts that can be tested: what a correct manual reconciliation contains, how
a person's result is scored against it, and how the review queue is built and
scored. Nothing here estimates a time. If a session has not been run, there is
no number.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from evals.score import _acceptable_ledger_sets
from recon import ingest

ROOT = Path(__file__).resolve().parent.parent
TIMING = ROOT / "evals" / "timing"
LISTS = ("outstanding_ledger", "bank_not_in_books", "needs_fixing")


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}{dollars:,}.{rem:02d}"


def reconciling_items(key: dict) -> dict[str, list[set[int]]]:
    """What a finished reconciliation must list. Each item is the set of amounts that identify it."""
    conn = ingest.load_month(key["month"])
    bank = {r["id"]: r["amount_cents"] for r in conn.execute("SELECT id, amount_cents FROM bank_txn")}
    gl = {r["id"]: r["amount_cents"] for r in conn.execute("SELECT id, amount_cents FROM gl_entry")}
    out: dict[str, list[set[int]]] = {name: [] for name in LISTS}
    for g in key["groups"]:
        if g["expected"] == "gl_only":
            out["outstanding_ledger"] += [{abs(gl[i])} for i in g["gl_ids"]]
        elif g["expected"] == "bank_only":
            out["bank_not_in_books"] += [{abs(bank[i])} for i in g["bank_ids"]]
        elif g["expected"] == "review":
            b, l = sum(bank[i] for i in g["bank_ids"]), sum(gl[i] for i in g["gl_ids"])
            out["needs_fixing"].append({abs(b - l), abs(b), abs(l)})
        out["outstanding_ledger"] += [{abs(gl[i])} for i in g.get("duplicate_gl_ids", [])]
    return out


def score_manual(rows: list[dict], key: dict) -> dict:
    """rows: {'list': one of LISTS, 'amount': dollars}. Found, missed and extra reconciling items."""
    truth = reconciling_items(key)
    remaining = {name: list(items) for name, items in truth.items()}
    found = extra = unreadable = 0
    for row in rows:
        name, text = (row.get("list") or "").strip(), (row.get("amount") or "").strip()
        if not name and not text:
            continue
        try:
            cents = abs(ingest.parse_money(text))
        except ingest.IngestError:
            cents = None
        if name not in LISTS or cents is None or not text:
            unreadable += 1
            continue
        hit = next((item for item in remaining[name] if cents in item), None)
        if hit is None:
            extra += 1
        else:
            remaining[name].remove(hit)
            found += 1
    total = sum(len(v) for v in truth.values())
    return {"reconciling_items": total, "found": found, "missed": total - found, "extra": extra,
            "unreadable_rows": unreadable,
            "missed_by_list": {name: len(items) for name, items in remaining.items()}}


def review_queue(conn, month: str) -> list[dict]:
    """Every bank line that was not cleared, with what the agent said about it."""
    rows = conn.execute(
        "SELECT b.id, b.account, b.posted_date, b.description, b.amount_cents, b.status, b.reason_code, "
        "COALESCE(g.explanation, '') AS note FROM bank_status b LEFT JOIN match_group g ON g.id = b.group_id "
        "WHERE b.month = ? AND b.status <> 'cleared' ORDER BY b.account, b.posted_date, b.id", (month,))
    return [{"id": r["id"], "account": r["account"], "date": r["posted_date"], "description": r["description"],
             "amount": money(r["amount_cents"]), "agent_says": r["status"], "agent_reason": r["reason_code"],
             "agent_note": r["note"], "your_decision": "", "your_note": ""} for r in rows]


def score_review(queue: list[dict], key: dict) -> dict:
    """your_decision: ok | match <ledger ids> | bank_only | fix. Blank is undecided."""
    truth = {b: g for g in key["groups"] for b in g["bank_ids"]}
    correct = undecided = 0
    wrong = []
    for q in queue:
        words = (q.get("your_decision") or "").replace(",", " ").split()
        g = truth[q["id"]]
        if not words or (words[0].lower() == "ok" and q["agent_says"] == "open"):
            undecided += 1
            continue
        verb = words[0].lower()
        if verb == "ok":
            verb = {"bank_only": "bank_only", "review": "fix"}[q["agent_says"]]
        if verb == "match":
            right = g["expected"] == "match" and set(words[1:]) in _acceptable_ledger_sets(g)
        else:
            right = {"bank_only": "bank_only", "fix": "review"}.get(verb) == g["expected"]
        correct += right
        if not right:
            wrong.append(q["id"])
    return {"items": len(queue), "correct": correct, "undecided": undecided, "wrong_ids": wrong}


def save(directory: Path, name: str, record: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    stamped = {**record, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.write_text(json.dumps(stamped, indent=1) + "\n")
    return path


def stopwatch(prompt_start: str, prompt_stop: str) -> float:
    """Wall-clock seconds between two Enter presses. Type p then Enter to pause or resume."""
    import time

    input(prompt_start)
    total, started = 0.0, time.monotonic()
    while True:
        answer = input(prompt_stop).strip().lower()
        if answer != "p":
            return total + (time.monotonic() - started if started is not None else 0.0)
        if started is not None:
            total += time.monotonic() - started
            started = None
            print("Paused. Type p then Enter to resume.")
        else:
            started = time.monotonic()
            print("Running.")

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


# -- what the memo may say about time ------------------------------------------------

REVIEW_SCENARIOS = (5, 10, 15)          # minutes, used only while the review has not been timed
HOURLY_RATES = (35, 50, 75)             # loaded cost of an hour of staff time, for the sensitivity table


def _table(header, rows, text: bool = False) -> str:
    """A markdown table. Columns after the first are right-aligned unless they hold text."""
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] + ["---" if text else "---:"] * (len(header) - 1)) + "|"]
    return "\n".join(out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def time_block(*, manual: dict | None, review: dict | None, estimate: dict | None,
               agent_seconds: float, queue_items: int) -> dict:
    """The time-saved wording for the memo, matched to the evidence that exists.

    Three states, and the text never claims more than its state supports:
      measured  both timed sessions were run
      estimate  someone experienced estimated the manual time; nothing was timed by hand
      none      neither
    """
    agent_min = agent_seconds / 60
    if manual and review:
        manual_scaled = manual["seconds"] / manual["bank_lines"] * review["bank_lines"]
        with_agent = review["seconds"] + review["agent_model_seconds"]
        saved = manual_scaled - with_agent
        share = f"{100 * saved / manual_scaled:.0f}%"
        hours = saved / 3600
        section = _table(["", "Result"], [
            ["Reconciling a month by hand", f"{manual['minutes']:.1f} minutes ({manual['bank_lines']} bank lines, "
             f"{manual['found']} of {manual['reconciling_items']} reconciling items found, {manual['extra']} listed in error)"],
            ["Agent run for a month", f"{review['agent_model_seconds'] / 60:.1f} minutes of model time"],
            ["Working the agent's review queue", f"{review['minutes']:.1f} minutes ({review['queue_items']} queue items, "
             f"{review['correct']} of {review['items']} decided correctly)"],
            ["Time saved", f"{saved / 60:.1f} minutes per month for these two accounts ({share} of the manual time)"],
        ], text=True) + ("\n\nMeasured by two timed sessions, same person. The manual session is done on the development month "
              "and the review session on the held-out month, so the reviewer has not already seen the answers. "
              "The manual time is scaled by bank lines to compare like with like. One person and one trial each: "
              "treat it as an indication, not a benchmark.")
        return {
            "headline": f"{saved / 60:.0f} minutes ({share})",
            "note": "**The time-saved figure is measured.** It comes from two timed sessions by one person.",
            "section": section,
            "roi": _table(["Loaded cost of an hour of staff time", "Value of time saved per month", "Value per year"],
                          [[f"${r}", f"${hours * r:,.2f}", f"${hours * r * 12:,.2f}"] for r in HOURLY_RATES]),
            "limit": "**Timing is one person.** One trial each of the manual and review sessions. See section 6.",
        }
    if estimate:
        m = float(estimate["minutes"])
        who = estimate.get("who", "an experienced practitioner")
        rows, values = [], []
        for r in REVIEW_SCENARIOS:
            total = agent_min + r
            rows.append([f"{r} minutes", f"{total:.1f} minutes", f"{m - total:.1f} minutes", f"{100 * (m - total) / m:.0f}%"])
            values.append([f"{r} minutes"] + [f"${(m - total) / 60 * rate:,.2f}" for rate in HOURLY_RATES])
        low, high = m - agent_min - REVIEW_SCENARIOS[-1], m - agent_min - REVIEW_SCENARIOS[0]
        section = _table(["", "Result", "Basis"], [
            ["Reconciling a month by hand", f"about {m:.0f} minutes",
             f"Estimate from {estimate['source']}. It is not a timed session on this data."],
            ["Agent run for a month", f"{agent_min:.1f} minutes of model time", "Measured"],
            ["Working the agent's review queue", f"{queue_items} items", "Not yet timed"],
        ], text=True) + ("\n\nBecause the review has not been timed, the saving is shown for three possible review times:\n\n"
              + _table(["If the review takes", "Total with the agent", "Time saved", "Share of the manual estimate"], rows)
              + "\n\nTwo timed sessions (`scripts/time_manual.py` and `scripts/time_review.py`) would replace this "
                "table with a measured figure.")
        return {
            "headline": f"{low:.0f} to {high:.0f} minutes of an estimated {m:.0f} (the manual time is an estimate)",
            "note": ("**The time-saved figure rests on an estimate.** The agent's run time is measured. The manual "
                     f"time is {who}'s estimate, not a timed session, and the review time has "
                     "not been timed. Section 6 shows the range."),
            "section": section,
            "roi": ("Value of the time saved per reconciliation, at three costs of an hour of staff time:\n\n"
                    + _table(["If the review takes"] + [f"${r} an hour" for r in HOURLY_RATES], values)),
            "limit": (f"**The time saved rests on an estimate.** One estimate of the manual time, from {who}, and "
                      "a review that has not been timed. See section 6."),
        }
    return {
        "headline": "not yet measured",
        "note": ("**The time-saved figure is not yet measured.** It needs two timed sessions by one person. "
                 "Nothing is estimated in its place."),
        "section": ("Time saved is not yet measured. It needs two timed sessions: `scripts/time_manual.py` and "
                    f"`scripts/time_review.py`. The agent's own run takes {agent_min:.1f} minutes of model time and "
                    f"leaves {queue_items} items for review."),
        "roi": "_The value of the time saved is not yet measured._",
        "limit": "**Time saved is not yet measured.** See section 6.",
    }

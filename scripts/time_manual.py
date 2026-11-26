"""Time yourself reconciling a month by hand, then score the result against the answer key.

This is the baseline for the time-saved number. It sets up a working folder
with copies of the client files and a blank sheet for your reconciling items,
runs a stopwatch while you work in Excel, and records how long you took and how
many of the reconciling items you found.

    uv run python scripts/time_manual.py                 # September 2026
    uv run python scripts/time_manual.py --month 2026-10

Do the development month (September) by hand, so that your review of October's
queue in scripts/time_review.py is not helped by having seen its answers.
"""
import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import timing  # noqa: E402

SHEET = "my_reconciling_items.csv"
HOW = """How to fill in {sheet}

Reconcile both bank accounts to the ledger the way you normally would. When you
are done, every bank line and every ledger entry is either matched or on one of
three lists. Put one row in {sheet} for each item on a list:

  list                 what goes on it
  outstanding_ledger   in the ledger, not at the bank yet: outstanding checks,
                       deposits in transit, and any entry booked twice
  bank_not_in_books    at the bank, not in the ledger: fees, interest, returned
                       items, anything nobody booked
  needs_fixing         bank and ledger disagree about the same item. Enter the
                       difference, or either amount.

  amount               dollars, sign does not matter
  note                 optional, for your own use

Files in this folder are copies. Work on them however you like.
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--month", default="2026-09")
    a = p.parse_args()
    key = json.loads((ROOT / "data" / "answer_key" / f"{a.month}.json").read_text())
    work = ROOT / "out" / "manual" / a.month
    if work.exists():
        print(f"{work} already exists. Move or delete it to start a fresh timed session.")
        return 1
    shutil.copytree(ROOT / "data" / "raw" / a.month, work)
    with (work / SHEET).open("w", newline="") as f:
        csv.writer(f).writerow(["list", "amount", "note"])
    (work / "HOW_TO.txt").write_text(HOW.format(sheet=SHEET))
    bank_lines = sum(len(g["bank_ids"]) for g in key["groups"])

    print(f"\nWorking folder: {work}")
    print(f"It holds two bank exports, the ledger, last month's outstanding checks, and {SHEET}.")
    print("Read HOW_TO.txt first. The clock is not running yet.\n")
    seconds = timing.stopwatch("Press Enter to start the clock. ",
                               "Working... press Enter when finished (p then Enter to pause). ")
    with (work / SHEET).open(newline="") as f:
        rows = list(csv.DictReader(f))
    result = timing.score_manual(rows, key)
    record = {"month": a.month, "bank_lines": bank_lines, "seconds": round(seconds, 1),
              "minutes": round(seconds / 60, 1), "method": "by hand in a spreadsheet, self-timed",
              **result}
    path = timing.save(timing.TIMING, f"manual_{a.month}", record)
    print(f"\n{seconds / 60:.1f} minutes for {bank_lines} bank lines.")
    print(f"Reconciling items found: {result['found']} of {result['reconciling_items']}"
          f" (missed {result['missed']}, extra {result['extra']}, unreadable rows {result['unreadable_rows']}).")
    print(f"Saved to {path.relative_to(ROOT)}. Commit it; the README is built from it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

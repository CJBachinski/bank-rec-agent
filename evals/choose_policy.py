"""Pick the confidence level at which the agent may clear a match on its own.

The choice is made on development runs only and then applied unchanged to the
held-out month. For each policy, saved proposals are re-applied (no model calls)
and three things are counted:

  harm     false clears in the month, plus forced matches that escaped the guard
           on the hallucination negatives
  correct  bank lines in the month that ended where the answer key says
  held     real matches the policy kept back for a person

The rule: fewest harmful outcomes first; among those, most correct; if still
tied, the strictest policy, because a tie means the data cannot tell them apart.

    uv run python -m evals.choose_policy dev-v3-sonnet --write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.run import ROOT, RUNS
from evals.summarize import summarize
from recon.pipeline import POLICIES

STRICTEST_FIRST = ["high_only", "medium_up", "all_verified"]


def table(run_dirs: list[Path]) -> list[dict]:
    rows = []
    for policy in STRICTEST_FIRST:
        harm = correct = lines = escaped = false_clear = 0
        for d in run_dirs:
            s = summarize(d, policy)
            false_clear += s["main"]["pooled"]["false_clear"]["k"]
            escaped += s["hallucination"]["negatives"]["escaped"]["k"]
            correct += s["main"]["pooled"]["correct"]["k"]
            lines += s["main"]["pooled"]["correct"]["n"]
        harm = false_clear + escaped
        rows.append({"policy": policy, "clears_at": sorted(POLICIES[policy]), "false_clears": false_clear,
                     "escaped_forced_matches": escaped, "harm": harm, "correct": correct, "lines": lines})
    return rows


def choose(rows: list[dict]) -> str:
    return min(rows, key=lambda r: (r["harm"], -r["correct"], STRICTEST_FIRST.index(r["policy"])))["policy"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("runs", nargs="+", help="development run names under evals/runs")
    p.add_argument("--write", action="store_true")
    a = p.parse_args()
    dirs = [RUNS / name for name in a.runs]
    for d in dirs:
        if json.loads((d / "config.json").read_text())["split"] != "dev":
            raise SystemExit(f"{d.name} is not a development run. The policy is never chosen on the test month.")
    rows = table(dirs)
    chosen = choose(rows)
    for r in rows:
        mark = "  <- chosen" if r["policy"] == chosen else ""
        print(f"{r['policy']:13} harm {r['harm']} (false clears {r['false_clears']}, escaped "
              f"{r['escaped_forced_matches']}) | correct {r['correct']}/{r['lines']}{mark}")
    if a.write:
        (ROOT / "evals" / "policy.json").write_text(json.dumps(
            {"policy": chosen, "chosen_on": a.runs, "table": rows}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

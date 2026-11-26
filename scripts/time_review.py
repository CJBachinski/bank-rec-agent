"""Time yourself working the review queue the agent leaves behind, then score your decisions.

This is the other half of the time-saved number. It rebuilds the month from a
saved agent run (no model calls), writes the reconciliation and the queue of
bank lines the agent did not clear, and runs a stopwatch while you decide each one.

    uv run python scripts/time_review.py --run test-v3-sonnet --rep 1

In review_queue.csv, fill in your_decision for every row:

    ok                  the agent filed it correctly (a bank-only item, or something to fix)
    match G-10-012 ...  it is the same money as these ledger entries
    bank_only           the bank has it and the books do not
    fix                 bank and books disagree; someone has to correct an entry
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import timing  # noqa: E402
from evals.run import RUNS, _read, answer_key  # noqa: E402
from recon import pipeline, report  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--run", required=True, help="a directory under evals/runs")
    p.add_argument("--rep", type=int, default=1)
    p.add_argument("--policy", default=None, help="defaults to the policy in evals/policy.json")
    a = p.parse_args()

    run_dir = RUNS / a.run
    config = json.loads((run_dir / "config.json").read_text())
    policy_file = ROOT / "evals" / "policy.json"
    policy = a.policy or (json.loads(policy_file.read_text())["policy"] if policy_file.exists()
                          else pipeline.DEFAULT_POLICY)
    key = answer_key(config["split"])
    month = key["month"]
    rows = [r for r in _read(run_dir / "results.jsonl") if r["kind"] == "main" and r["rep"] == a.rep]
    if not rows:
        print(f"No month rows for repetition {a.rep} in {run_dir}.")
        return 1
    conn = pipeline.prepare(month)
    proposals = {r["case_id"].split(":", 1)[1]: (r["proposal"] if r["status"] == "ok" else None) for r in rows}
    pipeline.apply_decisions(conn, month, proposals, policy)
    queue = timing.review_queue(conn, month)

    work = ROOT / "out" / "review" / month
    if (work / "review_queue.csv").exists():
        print(f"{work} already has a queue. Move or delete it to start a fresh timed session.")
        return 1
    work.mkdir(parents=True, exist_ok=True)
    (work / "reconciliation.md").write_text(report.to_markdown(conn, month))
    with (work / "review_queue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(queue[0]))
        writer.writeheader()
        writer.writerows(queue)
    for name in sorted((ROOT / "data" / "raw" / month).iterdir()):
        (work / name.name).write_bytes(name.read_bytes())
    bank_lines = sum(len(g["bank_ids"]) for g in key["groups"])
    agent_seconds = sum(r["latency_s"] for r in rows)

    print(f"\nWorking folder: {work}")
    print(f"The agent cleared {bank_lines - len(queue)} of {bank_lines} bank lines. {len(queue)} are in "
          f"review_queue.csv for you, with the client files and reconciliation.md alongside.")
    print(__doc__.split("In review_queue.csv")[1])
    seconds = timing.stopwatch("Press Enter to start the clock. ",
                               "Reviewing... press Enter when finished (p then Enter to pause). ")
    with (work / "review_queue.csv").open(newline="") as f:
        decided = list(csv.DictReader(f))
    result = timing.score_review(decided, key)
    record = {"month": month, "bank_lines": bank_lines, "queue_items": len(queue), "seconds": round(seconds, 1),
              "minutes": round(seconds / 60, 1), "run": a.run, "rep": a.rep, "policy": policy,
              "model": config["model"], "prompt": config["prompt"],
              "agent_model_seconds": round(agent_seconds, 1), "method": "review queue in a spreadsheet, self-timed",
              **result}
    path = timing.save(timing.TIMING, f"review_{month}", record)
    print(f"\n{seconds / 60:.1f} minutes for {len(queue)} queue items.")
    print(f"Decisions correct: {result['correct']} of {result['items']} (undecided {result['undecided']}).")
    print(f"Saved to {path.relative_to(ROOT)}. Commit it; the README is built from it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

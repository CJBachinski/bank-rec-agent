"""Reconcile one month end to end and write the report a person would review.

This is the production path: load the files, run the rules, ask the agent about
each line the rules left, pass every proposal through the verifier and policy,
and write the reconciliation and the review queue.

    doppler run --only-secrets ANTHROPIC_API_KEY -- \\
        uv run python scripts/reconcile.py --month 2026-10 --model claude-sonnet-5-5 --prompt v3
    uv run python scripts/reconcile.py --month 2026-10 --from-run test-v3-sonnet --rep 1    # no model calls
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import score, timing  # noqa: E402
from evals.run import RUNS, _read, make_llm  # noqa: E402
from recon import agent, pipeline, report  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--month", required=True)
    p.add_argument("--model", default="claude-sonnet-5-5")
    p.add_argument("--prompt", default="v3")
    p.add_argument("--policy", default=None)
    p.add_argument("--from-run", help="rebuild from a saved run under evals/runs instead of calling the model")
    p.add_argument("--rep", type=int, default=1)
    p.add_argument("--out", type=Path, help="directory to write to (default out/<month>)")
    a = p.parse_args()
    policy_file = ROOT / "evals" / "policy.json"
    policy = a.policy or (json.loads(policy_file.read_text())["policy"] if policy_file.exists()
                          else pipeline.DEFAULT_POLICY)
    started = time.monotonic()
    if a.from_run:
        rows = [r for r in _read(RUNS / a.from_run / "results.jsonl") if r["kind"] == "main" and r["rep"] == a.rep]
        conn = pipeline.prepare(a.month)
        proposals = {r["case_id"].split(":", 1)[1]: (r["proposal"] if r["status"] == "ok" else None) for r in rows}
        audit = pipeline.apply_decisions(conn, a.month, proposals, policy)
        source = f"saved run {a.from_run}, repetition {a.rep}"
    else:
        split = "dev" if a.month == "2026-09" else "test"
        conn, audit = pipeline.run_month(a.month, llm=make_llm(a.model, split), model=a.model,
                                         system=agent.load_prompt(a.prompt), policy=policy)
        source = f"live run, {a.model}, prompt {a.prompt}"
    seconds = time.monotonic() - started

    out = a.out or ROOT / "out" / a.month
    out.mkdir(parents=True, exist_ok=True)
    header = f"<!-- {source}; clearing policy {policy} -->\n"
    (out / "reconciliation.md").write_text(header + report.to_markdown(conn, a.month))
    queue = timing.review_queue(conn, a.month)
    with (out / "review_queue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(queue[0]) if queue else ["id"])
        writer.writeheader()
        writer.writerows(queue)

    outcomes = {}
    for row in audit:
        outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
    print(f"{a.month}: {source}; policy {policy}; {seconds:.0f}s")
    print(f"  agent decisions: {outcomes}")
    for acct in report.reconcile(conn, a.month):
        print(f"  {acct['ledger_account']}: {acct['cleared_bank_lines']} of {acct['bank_lines']} bank lines cleared, "
              f"{len(acct['unexplained_bank_items'])} unexplained, adjusted bank {report.money(acct['adjusted_bank'])}"
              f" vs adjusted book {report.money(acct['adjusted_book'])}")
    key_path = ROOT / "data" / "answer_key" / f"{a.month}.json"
    if key_path.exists():
        s = score.score_month(conn, json.loads(key_path.read_text()))
        print(f"  against the answer key: {s['correct']} of {s['lines']} correct, {s['false_clear']} false clears, "
              f"{s['left_for_person']} left for a person, {s['wrong_label']} wrong label")
    print(f"  wrote {out / 'reconciliation.md'} and {out / 'review_queue.csv'} ({len(queue)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

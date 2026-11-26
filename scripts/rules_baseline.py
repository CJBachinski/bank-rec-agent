"""Rules only, no model: load each month, run the deterministic pass, grade it.

This is the number the agent has to beat. It writes a reconciliation report to
out/ and the graded result to evals/results/.

    uv run python scripts/rules_baseline.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import score  # noqa: E402
from recon import ingest, report, rules  # noqa: E402


def main() -> int:
    for key_path in sorted((ROOT / "data" / "answer_key").glob("*.json")):
        key = json.loads(key_path.read_text())
        month = key["month"]
        started = time.perf_counter()
        conn = ingest.load_month(month)
        counts = rules.run(conn, month)
        seconds = time.perf_counter() - started
        result = score.score_month(conn, key)
        result.update(config="rules_only", seconds=round(seconds, 4), rule_counts=counts)

        out = ROOT / "out" / month
        out.mkdir(parents=True, exist_ok=True)
        (out / "reconciliation_rules_only.md").write_text(report.to_markdown(conn, month))
        results = ROOT / "evals" / "results"
        results.mkdir(parents=True, exist_ok=True)
        (results / f"rules_only_{month}.json").write_text(json.dumps(result, indent=2) + "\n")

        print(f"{month}: {result['lines']} bank lines | correct {result['correct']} "
              f"({result['accuracy']:.0%}) | false clears {result['false_clear']} | "
              f"left for a person {result['left_for_person']} | {seconds * 1000:.0f} ms")
        left = {c: v.get("left_for_person", 0) for c, v in result["by_category"].items()
                if v.get("left_for_person")}
        print("   left for a person, by kind:", ", ".join(f"{c} {n}" for c, n in left.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

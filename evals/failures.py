"""List what a run got wrong, case by case, with what the model said.

This is the input to prompt iteration and to docs/FAILURE_ANALYSIS.md: every
miss, grouped by kind, with the model's own explanation and the reason the guard
held it (if it did).

    uv run python -m evals.failures evals/runs/dev-v1-sonnet [--policy high_only] [--all]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from evals.run import ROOT, _read
from evals.summarize import summarize
from recon import pipeline


def failures(run_dir: Path, policy: str = pipeline.DEFAULT_POLICY, include_ok: bool = False) -> list[dict]:
    s = summarize(run_dir, policy)
    rows = {(r["case_id"], r["rep"]): r for r in _read(Path(run_dir) / "results.jsonl")}
    out = []
    for d in s["detail"]:
        row = rows[(d["case_id"], d["rep"])]
        proposal = row["proposal"] or {}
        if d["case_id"].startswith("main:"):
            bad = d["verdict"] != "correct"
            kind = d["verdict"]
        else:
            flags = [f for f in ("forced_match", "invented_id", "invented_value", "ungrounded_figure",
                                 "wrong_field", "escaped", "over_abstained") if d.get(f)]
            bad = bool(flags) or not d["correct"]
            kind = "+".join(flags) or ("wrong" if not d["correct"] else "ok")
        if bad or include_ok:
            out.append({"case_id": d["case_id"], "rep": d["rep"], "category": d["category"], "kind": kind,
                        "expected": d.get("expected", "match" if d.get("kind") == "control" else "decline"),
                        "decision": proposal.get("decision"), "confidence": proposal.get("confidence"),
                        "reason_code": proposal.get("reason_code"), "gl_ids": proposal.get("gl_ids"),
                        "held_by": d.get("held_by", []), "explanation": proposal.get("explanation", ""),
                        "status": row["status"], "tool_calls": row["tool_calls"]})
    for row in rows.values():
        if row["status"] != "ok":
            out.append({"case_id": row["case_id"], "rep": row["rep"], "category": row["tags"][0],
                        "kind": f"no_answer:{row['status']}", "expected": "", "decision": None,
                        "confidence": None, "reason_code": None, "gl_ids": None, "held_by": [],
                        "explanation": "", "status": row["status"], "tool_calls": row["tool_calls"]})
    return sorted(out, key=lambda f: (f["category"], f["case_id"], f["rep"]))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("run_dir", type=Path)
    p.add_argument("--policy", default=pipeline.DEFAULT_POLICY, choices=list(pipeline.POLICIES))
    p.add_argument("--all", action="store_true", help="include the cases it got right")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    run_dir = a.run_dir if a.run_dir.is_absolute() else ROOT / a.run_dir
    found = failures(run_dir, a.policy, a.all)
    if a.json:
        print(json.dumps(found, indent=1))
        return 0
    groups = defaultdict(list)
    for f in found:
        groups[(f["category"], f["kind"])].append(f)
    for (category, kind), fs in sorted(groups.items()):
        print(f"\n== {category} / {kind}: {len(fs)}")
        for f in fs:
            print(f"  {f['case_id']} rep{f['rep']}: expected {f['expected']}; said {f['decision']}"
                  f" ({f['confidence']}, {f['reason_code']}) {f['gl_ids'] or ''}"
                  f"{' held: ' + ','.join(f['held_by']) if f['held_by'] else ''}")
            print(f"      {f['explanation'][:400]}")
    print(f"\n{len(found)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

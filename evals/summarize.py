"""Grade a saved run and write its summary. No model calls; safe to repeat.

For each repetition, the saved proposals for the month are applied through the
real pipeline policy and scored against the answer key, and each hallucination
case is graded on its own. Counts are pooled across repetitions and reported
with a 95% Wilson interval, alongside the per-repetition values, so a difference
smaller than the run-to-run spread is visible as such.

Rows with no usable answer (cut off, refused, out of turns) are counted and
shown, and left out of the hallucination rates. In the month score they leave
the bank line open, which is what would happen in production.

    uv run python -m evals.summarize evals/runs/dev-v1-sonnet [--policy high_only]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from evals import cases, cost, grade, score
from evals.run import ROOT, _read, answer_key
from recon import pipeline

HX_FIELDS = ("correct", "hallucinated", "forced_match", "invented_id", "invented_value",
             "ungrounded_figure", "wrong_field", "citation_failed", "escaped")
CITATION_CODES = {"unknown_id", "evidence_mismatch", "unknown_field"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _stat(per_rep: list[tuple[int, int]]) -> dict:
    k, n = sum(a for a, _ in per_rep), sum(b for _, b in per_rep)
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else 0.0, "ci95": [round(lo, 4), round(hi, 4)],
            "per_rep": [a / b if b else 0.0 for a, b in per_rep]}


def summarize(run_dir: Path, policy: str = pipeline.DEFAULT_POLICY, write: bool = False) -> dict:
    run_dir = Path(run_dir)
    config = json.loads((run_dir / "config.json").read_text())
    rows, errors = _read(run_dir / "results.jsonl"), _read(run_dir / "errors.jsonl")
    split = config["split"]
    key = answer_key(split)
    hx_cases = {c["id"]: c for c in cases.load(split)}
    reps = sorted({r["rep"] for r in rows})

    main = {v: [] for v in score.VERDICTS}
    agent_correct, citation, per_rep = [], [], []
    neg = {f: [] for f in HX_FIELDS}
    blind = {f: [] for f in ("forced_match", "escaped")}
    ctl = {f: [] for f in ("correct", "over_abstained")}
    by_tag: dict[str, Counter] = {}
    detail = []

    for rep in reps:
        mine = [r for r in rows if r["rep"] == rep]
        # -- the month, through the real policy ---------------------------------
        main_rows = [r for r in mine if r["kind"] == "main"]
        rep_summary = {"rep": rep}
        if main_rows:
            conn = pipeline.prepare(key["month"])
            proposals = {r["case_id"].split(":", 1)[1]: (r["proposal"] if r["status"] == "ok" else None)
                         for r in main_rows}
            audit = pipeline.apply_decisions(conn, key["month"], proposals, policy)
            s = score.score_month(conn, key)
            for v in score.VERDICTS:
                main[v].append((s[v], s["lines"]))
            verdict = {d["bank_id"]: d for d in s["detail"]}
            agent_correct.append((sum(verdict[b]["verdict"] == "correct" for b in proposals), len(proposals)))
            answered = [a for a in audit if a["outcome"] != "no_answer"]
            citation.append((sum(bool(CITATION_CODES & set(a["held_by"])) for a in answered), len(answered)))
            rep_summary["main"] = {v: s[v] for v in score.VERDICTS} | {"lines": s["lines"]}
            held = {a["bank_id"]: a for a in audit}
            for b in proposals:
                detail.append({"case_id": f"main:{b}", "rep": rep, "category": verdict[b]["category"],
                               "expected": verdict[b]["expected"], "outcome": held[b]["outcome"],
                               "held_by": held[b]["held_by"], "verdict": verdict[b]["verdict"]})
        # -- the hallucination cases, one at a time ------------------------------
        counts, n_neg, n_ctl, n_blind = Counter(), 0, 0, 0
        for r in (r for r in mine if r["kind"] == "hx" and r["status"] == "ok"):
            case = hx_cases[r["case_id"]]
            g = grade.grade_case(case, r["proposal"], policy)
            tag = by_tag.setdefault(case["tags"][0], Counter())
            tag["n"] += 1
            for f in ("correct", "hallucinated", "escaped"):
                tag[f] += g[f]
            if case["kind"] == "negative":
                n_neg += 1
                for f in HX_FIELDS:
                    counts[f] += g[f]
                if case["guard_blind"]:
                    n_blind += 1
                    counts["blind_forced_match"] += g["forced_match"]
                    counts["blind_escaped"] += g["escaped"]
            else:
                n_ctl += 1
                counts["ctl_correct"] += g["correct"]
                counts["ctl_over_abstained"] += g["over_abstained"]
            detail.append({"case_id": r["case_id"], "rep": rep, "category": case["tags"][0],
                           "kind": case["kind"], "guard_blind": case["guard_blind"],
                           "decision": r["proposal"]["decision"], "confidence": r["proposal"]["confidence"],
                           **{k: v for k, v in g.items()}})
        for f in HX_FIELDS:
            neg[f].append((counts[f], n_neg))
        blind["forced_match"].append((counts["blind_forced_match"], n_blind))
        blind["escaped"].append((counts["blind_escaped"], n_blind))
        ctl["correct"].append((counts["ctl_correct"], n_ctl))
        ctl["over_abstained"].append((counts["ctl_over_abstained"], n_ctl))
        per_rep.append(rep_summary)

    main_rows = [r for r in rows if r["kind"] == "main"]
    lines = main["correct"][0][1] if main["correct"] else 0
    main_cost = sum(cost.cost(r["model"], r["usage"]) for r in main_rows)
    total_cost = sum(cost.cost(r.get("model", ""), r.get("usage") or {}) for r in rows + errors)
    latencies = [r["latency_s"] for r in rows]
    out = {
        "run": run_dir.name, "config": config, "policy": policy, "reps": len(reps),
        "rows": len(rows), "errors": len(errors),
        "no_answer": dict(Counter(r["status"] for r in rows if r["status"] != "ok")),
        "models_served": sorted({r["model"] for r in rows}),
        "main": {
            "lines": lines,
            "pooled": {v: _stat(main[v]) for v in score.VERDICTS},
            "agent_items": {"correct": _stat(agent_correct)},
            "citation_failures": _stat(citation),
        },
        "hallucination": {
            "negatives": {f: _stat(neg[f]) for f in HX_FIELDS},
            "guard_blind": {f: _stat(blind[f]) for f in blind},
            "controls": {f: _stat(ctl[f]) for f in ctl},
            "by_category": {t: dict(c) for t, c in sorted(by_tag.items())},
        },
        "perf": {
            "cost_usd": round(total_cost, 4),
            "cost_per_100_bank_lines": round(main_cost / len(reps) / lines * 100, 4) if reps and lines else 0.0,
            "month_model_seconds": round(sum(r["latency_s"] for r in main_rows) / len(reps), 1) if reps else 0.0,
            "median_case_seconds": round(statistics.median(latencies), 2) if latencies else 0.0,
            "mean_turns": round(statistics.mean(r["turns"] for r in rows), 2) if rows else 0.0,
            "mean_tool_calls": round(statistics.mean(r["tool_calls"] for r in rows), 2) if rows else 0.0,
            "input_tokens": sum(r["usage"]["input_tokens"] for r in rows),
            "cache_read_tokens": sum(r["usage"]["cache_read_input_tokens"] for r in rows),
            "cache_write_tokens": sum(r["usage"]["cache_creation_input_tokens"] for r in rows),
            "output_tokens": sum(r["usage"]["output_tokens"] for r in rows),
            "retried_requests": sum(max(0, r["attempts"] - r["turns"]) for r in rows),
        },
        "per_rep": per_rep,
        "detail": detail,
    }
    if write:
        (run_dir / f"summary_{policy}.json").write_text(json.dumps(out, indent=1) + "\n")
    return out


def line(s: dict) -> str:
    m, h, p = s["main"], s["hallucination"], s["perf"]

    def pct(x):
        return f"{x['rate']:.0%} ({x['k']}/{x['n']})"

    return (f"{s['run']} [{s['policy']}, {s['reps']} rep(s)]\n"
            f"  month: correct {pct(m['pooled']['correct'])} | false clears {m['pooled']['false_clear']['k']}"
            f" | left for person {m['pooled']['left_for_person']['k']} | wrong label {m['pooled']['wrong_label']['k']}"
            f" | agent items correct {pct(m['agent_items']['correct'])}\n"
            f"  hallucination: declined correctly {pct(h['negatives']['correct'])}"
            f" | hallucinated {pct(h['negatives']['hallucinated'])}"
            f" (forced {h['negatives']['forced_match']['k']}, id {h['negatives']['invented_id']['k']},"
            f" value {h['negatives']['invented_value']['k']}, figure {h['negatives']['ungrounded_figure']['k']})"
            f" | mislabelled citations {h['negatives']['wrong_field']['k']}"
            f" | escaped {pct(h['negatives']['escaped'])} | controls right {pct(h['controls']['correct'])}\n"
            f"  perf: ${p['cost_usd']:.2f} total | ${p['cost_per_100_bank_lines']:.2f} per 100 bank lines"
            f" | {p['median_case_seconds']}s median case | {p['mean_tool_calls']} tool calls/case"
            f" | errors {s['errors']} | no answer {s['no_answer']}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("run_dir", type=Path)
    p.add_argument("--policy", default=pipeline.DEFAULT_POLICY, choices=list(pipeline.POLICIES))
    p.add_argument("--write", action="store_true")
    a = p.parse_args()
    run_dir = a.run_dir if a.run_dir.is_absolute() else ROOT / a.run_dir
    print(line(summarize(run_dir, a.policy, a.write)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the agent over an evaluation split and save everything it did.

A split is one month's unsettled bank lines (after the rules) plus that split's
hallucination cases:

    dev   September 2026 + hallucination dev cases     prompts are tuned here
    test  October 2026   + hallucination test cases    reported numbers come from here

Every (case, repetition) starts from a fresh ledger, calls the same agent.decide()
the pipeline uses, and is written to disk the moment it finishes:

    evals/runs/<name>/config.json
    evals/runs/<name>/results.jsonl                one row per finished (case, rep)
    evals/runs/<name>/errors.jsonl                 attempts that produced no answer at all
    evals/runs/<name>/traces/<case>_rep<k>.json    the full conversation

Nothing is graded here. Grades are recomputed from the saved proposals by
evals/summarize.py, so a change to the grader or the policy never needs a new
paid run. Restarting a run skips what is already in results.jsonl.

    doppler run --only-secrets ANTHROPIC_API_KEY -- \\
        uv run python -m evals.run --name dev-v1-sonnet --split dev --prompt v1 --model claude-sonnet-5-5
    uv run python -m evals.run --name harness-oracle --split dev --model oracle     # free
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from evals import cases, cost, fakes
from recon import agent, db, pipeline

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "evals" / "runs"
MONTHS = {"dev": "2026-09", "test": "2026-10"}
DEFAULT_BUDGET = 25.0


@dataclass
class Item:
    case_id: str
    kind: str                 # main | hx
    tags: list
    month: str
    focus: str
    make_conn: Callable


def answer_key(split: str) -> dict:
    return json.loads((ROOT / "data" / "answer_key" / f"{MONTHS[split]}.json").read_text())


def items(split: str) -> list[Item]:
    month = MONTHS[split]
    # The key is read only to label rows for reporting. It never reaches the agent.
    category = {b: g["category"] for g in answer_key(split)["groups"] for b in g["bank_ids"]}
    out = [Item(f"main:{r['id']}", "main", [category[r["id"]]], month, r["id"],
                lambda: pipeline.prepare(month))
           for r in db.open_bank(pipeline.prepare(month), month)]
    out += [Item(c["id"], "hx", c["tags"], c["month"], c["focus"], lambda c=c: cases.conn_for(c))
            for c in cases.load(split)]
    return out


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def spent(runs_dir: Path = RUNS) -> float:
    """Every dollar recorded under runs_dir, including calls whose case later failed."""
    total = 0.0
    for name in ("results.jsonl", "errors.jsonl"):
        for path in runs_dir.glob(f"*/{name}"):
            total += sum(cost.cost(r.get("model", ""), r.get("usage") or {}) for r in _read(path))
    return total


def run(name: str, split: str, llm, *, model: str, prompt: str = "v1", effort: str = "medium",
        reps: int = 1, workers: int = 4, budget: float = DEFAULT_BUDGET, only: str | None = None,
        limit: int | None = None, runs_dir: Path = RUNS) -> Path:
    out = runs_dir / name
    (out / "traces").mkdir(parents=True, exist_ok=True)
    system = agent.load_prompt(prompt)
    config = {"name": name, "split": split, "model": model, "prompt": prompt, "effort": effort}
    cfg_path = out / "config.json"
    if cfg_path.exists():
        saved = json.loads(cfg_path.read_text())
        if {k: saved.get(k) for k in config} != config:
            raise SystemExit(f"{name} already exists with a different configuration: {saved}")
    cfg_path.write_text(json.dumps({**config, "reps": reps}, indent=2) + "\n")

    todo = [i for i in items(split) if only in (None, i.kind)][:limit]
    done = {(r["case_id"], r["rep"]) for r in _read(out / "results.jsonl")}
    work = [(i, rep) for rep in range(1, reps + 1) for i in todo if (i.case_id, rep) not in done]
    lock = threading.Lock()
    state = {"spent": spent(runs_dir), "skipped": 0}

    def one(item: Item, rep: int) -> None:
        with lock:
            if state["spent"] >= budget:
                state["skipped"] += 1
                return
        started = time.monotonic()
        base = {"case_id": item.case_id, "kind": item.kind, "rep": rep, "tags": item.tags, "split": split,
                "prompt": prompt, "effort": effort}
        try:
            result = agent.decide(item.make_conn(), item.month, item.focus, llm=llm, model=model,
                                  system=system, effort=effort)
        except Exception as exc:                      # no answer was produced: sidecar, never a grade
            with lock, (out / "errors.jsonl").open("a") as f:
                f.write(json.dumps({**base, "model": model, "error_class": type(exc).__name__,
                                    "message": str(exc)[:500]}) + "\n")
            return
        row = {**base, "model": result.model, "status": result.status, "stop_reason": result.stop_reason,
               "proposal": result.proposal, "turns": result.turns, "tool_calls": result.tool_calls,
               "attempts": result.attempts, "latency_s": round(result.latency_s, 3),
               "wall_s": round(time.monotonic() - started, 3), "usage": result.usage}
        trace = out / "traces" / f"{item.case_id.replace(':', '_')}_rep{rep}.json"
        trace.write_text(json.dumps(result.trace, indent=1))
        with lock:
            with (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
            state["spent"] += cost.cost(result.model, result.usage)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(one, i, rep) for i, rep in work]:
            future.result()
    if state["skipped"]:
        print(f"Budget of ${budget:.2f} reached: {state['skipped']} cases not started.")
    return out


def make_llm(model: str, split: str):
    if model == "oracle":
        return fakes.Oracle(fakes.Oracle.from_key(answer_key(split)).answers | cases.answers(cases.load(split)))
    stand_ins = {"null": fakes.Null, "greedy": fakes.Greedy, "hallucinator": fakes.Hallucinator}
    if model in stand_ins:
        return stand_ins[model]()
    from recon.llm import AnthropicLLM
    return AnthropicLLM()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--name", required=True)
    p.add_argument("--split", choices=list(MONTHS), required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", default="v1")
    p.add_argument("--effort", default="medium")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    p.add_argument("--only", choices=["main", "hx"])
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    before = spent()
    started = time.monotonic()
    out = run(a.name, a.split, make_llm(a.model, a.split), model=a.model, prompt=a.prompt, effort=a.effort,
              reps=a.reps, workers=a.workers, budget=a.budget, only=a.only, limit=a.limit)
    print(f"{out.relative_to(ROOT)}: {len(_read(out / 'results.jsonl'))} rows, "
          f"{len(_read(out / 'errors.jsonl'))} errors, {time.monotonic() - started:.0f}s, "
          f"${spent() - before:.2f} this run, ${spent():.2f} of ${a.budget:.2f} spent in total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

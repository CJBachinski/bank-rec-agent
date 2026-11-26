"""Build README.md and the docs from templates and results files. No number is typed by hand.

Every {{placeholder}} in docs/templates/*.md is filled from a saved run summary,
the rules-only baseline, the chosen policy, or a timing record. If a timing
session has not been run, its cells read "not yet measured"; nothing is estimated.

    uv run python scripts/build_readme.py            # write the files
    uv run python scripts/build_readme.py --check    # fail if any file is out of date
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import cases, cost  # noqa: E402
from evals.failures import failures  # noqa: E402
from evals.run import RUNS, answer_key  # noqa: E402
from evals.summarize import summarize, wilson  # noqa: E402
from recon import pipeline  # noqa: E402

TEMPLATES = ROOT / "docs" / "templates"
OUTPUTS = {"README.md": ROOT / "README.md", "CHANGELOG.md": ROOT / "prompts" / "CHANGELOG.md",
           "FAILURE_ANALYSIS.md": ROOT / "docs" / "FAILURE_ANALYSIS.md",
           "POSTING_MAP.md": ROOT / "docs" / "POSTING_MAP.md"}
NAMES = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
         "claude-opus-5-5": "Claude Opus 5.5"}
PENDING = "not yet measured"


def pct(k, n):
    return f"{100 * k / n:.0f}%" if n else "n/a"


def of(stat):
    return f"{stat['k']} of {stat['n']}"


def rate(stat):
    return f"{pct(stat['k'], stat['n'])} ({of(stat)})"


def ci(stat):
    lo, hi = stat["ci95"]
    return f"{100 * lo:.0f}% to {100 * hi:.0f}%"


def upper(stat):
    """The 95% upper bound on a rate, for saying how much a zero can hide."""
    return f"{100 * wilson(stat['k'], stat['n'])[1]:.1f}%"


def spread(stat):
    values = [100 * v for v in stat["per_rep"]]
    return f"{min(values):.0f}% to {max(values):.0f}%" if values else "n/a"


def usd(x, places=2):
    return f"${x:,.{places}f}"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


class Report:
    def __init__(self):
        self.manifest = json.loads((ROOT / "evals" / "report.json").read_text())
        self.policy = json.loads((ROOT / "evals" / "policy.json").read_text())
        self.p = self.policy["policy"]
        self._cache = {}

    def s(self, run, policy=None):
        key = (run, policy or self.p)
        if key not in self._cache:
            self._cache[key] = summarize(RUNS / run, key[1])
        return self._cache[key]

    def run(self, split, prompt, model):
        return self.manifest[split][prompt][model]

    # -- tables ----------------------------------------------------------------
    def rules_baseline(self):
        rows = []
        for month, role in (("2026-09", "development"), ("2026-10", "held out")):
            r = json.loads((ROOT / "evals" / "results" / f"rules_only_{month}.json").read_text())
            rows.append([f"{month} ({role})", r["lines"], f"{pct(r['correct'], r['lines'])} ({r['correct']})",
                         r["false_clear"], r["left_for_person"]])
        return table(["Month", "Bank lines", "Settled by rules", "False clears", "Left for a person"], rows)

    def month_table(self, split, configs):
        rows = []
        for prompt, model in configs:
            s = self.s(self.run(split, prompt, model))
            m = s["main"]["pooled"]
            rows.append([f"{NAMES[s['config']['model']]}, prompt {prompt}", rate(m["correct"]), spread(m["correct"]),
                         m["false_clear"]["k"], m["left_for_person"]["k"] + m["wrong_label"]["k"],
                         rate(s["main"]["agent_items"]["correct"])])
        return table(["Configuration", "Bank lines settled correctly", "Range across runs", "False clears",
                      "Left for a person", "Of the lines rules could not settle"], rows)

    def hallucination_table(self, split, configs):
        rows = []
        for prompt, model in configs:
            h = self.s(self.run(split, prompt, model))["hallucination"]
            n = h["negatives"]
            rows.append([f"{NAMES[self.s(self.run(split, prompt, model))['config']['model']]}, prompt {prompt}",
                         n["correct"]["n"], n["forced_match"]["k"], n["invented_id"]["k"], n["invented_value"]["k"],
                         n["ungrounded_figure"]["k"], n["wrong_field"]["k"], n["escaped"]["k"],
                         rate(h["controls"]["correct"])])
        return table(["Configuration", "Negative trials", "Forced matches", "Invented ids", "Invented values",
                      "Ungrounded figures", "Mislabelled citations", "Got past the guard",
                      "Real matches still matched"], rows)

    def category_table(self, run):
        h = self.s(run)["hallucination"]["by_category"]
        label = {"no_counterpart": "Nothing close", "near_miss": "Near miss", "wrong_party": "Right amount, wrong party",
                 "injection": "Instruction hidden in the data", "phantom_reference": "Reference to a record that is not there",
                 "control": "Control: a real match"}
        rows = [[label[c], h[c]["n"], h[c]["correct"], h[c]["hallucinated"], h[c]["escaped"]]
                for c in label if c in h]
        return table(["Kind of case", "Trials", "Answered correctly", "Hallucinated", "Got past the guard"], rows)

    def model_table(self, split, prompt, models):
        rows = []
        for model in models:
            s = self.s(self.run(split, prompt, model))
            m, h, p = s["main"]["pooled"], s["hallucination"], s["perf"]
            rows.append([NAMES[s["config"]["model"]], rate(m["correct"]), m["false_clear"]["k"],
                         of(h["negatives"]["hallucinated"]), rate(h["controls"]["correct"]),
                         usd(p["cost_per_100_bank_lines"]), f"{p['median_case_seconds']:.1f}s",
                         f"{p['month_model_seconds'] / 60:.1f} min"])
        return table(["Model", "Bank lines settled correctly", "False clears", "Hallucinations",
                      "Real matches still matched", "API cost per 100 bank lines", "Median time per line",
                      "Model time for the month"], rows)

    def prompt_table(self, split, model, prompts):
        rows = []
        for prompt in prompts:
            if model not in self.manifest[split].get(prompt, {}):
                continue
            s = self.s(self.run(split, prompt, model))
            m, h = s["main"], s["hallucination"]
            rows.append([prompt, rate(m["agent_items"]["correct"]), m["pooled"]["false_clear"]["k"],
                         of(h["negatives"]["hallucinated"]), h["negatives"]["wrong_field"]["k"]
                         + m["citation_failures"]["k"], rate(h["controls"]["correct"])])
        return table(["Prompt", "Lines the rules left, settled correctly", "False clears", "Hallucinations",
                      "Citations the verifier rejected", "Real matches still matched"], rows)

    def policy_table(self):
        rows = [[r["policy"], ", ".join(r["clears_at"]), r["false_clears"], r["escaped_forced_matches"],
                 f"{r['correct']} of {r['lines']}"] for r in self.policy["table"]]
        return table(["Policy", "Clears a verified match reported at", "False clears",
                      "Forced matches past the guard", "Bank lines settled correctly"], rows)

    def failure_table(self, runs):
        rows = []
        for run in runs:
            s = self.s(run)
            for f in failures(RUNS / run, self.p):
                rows.append([NAMES[s["config"]["model"]], s["config"]["prompt"], f["case_id"].replace("main:", ""),
                             f["rep"], f["category"], f["kind"], f["decision"] or "none",
                             ", ".join(f["held_by"]) or "-"])
        if not rows:
            return "_No failures in these runs._"
        return table(["Model", "Prompt", "Case", "Run", "Kind of item", "What went wrong", "Agent said", "Held by"], rows)

    # -- timing ----------------------------------------------------------------
    def timing(self):
        out = {}
        manual = sorted((ROOT / "evals" / "timing").glob("manual_*.json"))
        review = sorted((ROOT / "evals" / "timing").glob("review_*.json"))
        m = json.loads(manual[-1].read_text()) if manual else None
        r = json.loads(review[-1].read_text()) if review else None
        out["manual_minutes"] = f"{m['minutes']:.1f} minutes" if m else PENDING
        out["manual_detail"] = (f"{m['bank_lines']} bank lines, {m['found']} of {m['reconciling_items']} reconciling "
                                f"items found, {m['extra']} listed in error" if m else PENDING)
        out["review_minutes"] = f"{r['minutes']:.1f} minutes" if r else PENDING
        out["review_detail"] = (f"{r['queue_items']} queue items, {r['correct']} of {r['items']} decided correctly"
                                if r else PENDING)
        out["agent_minutes"] = f"{r['agent_model_seconds'] / 60:.1f} minutes" if r else PENDING
        if m and r:
            manual_scaled = m["seconds"] / m["bank_lines"] * r["bank_lines"]
            with_agent = r["seconds"] + r["agent_model_seconds"]
            saved = manual_scaled - with_agent
            out["time_saved"] = (f"{saved / 60:.1f} minutes per month for these two accounts "
                                 f"({100 * saved / manual_scaled:.0f}% of the manual time)")
            out["time_saved_short"] = f"{saved / 60:.0f} minutes ({100 * saved / manual_scaled:.0f}%)"
            out["manual_scaled_minutes"] = f"{manual_scaled / 60:.1f} minutes"
            out["with_agent_minutes"] = f"{with_agent / 60:.1f} minutes"
            hours = saved / 3600
            out["roi_table"] = table(
                ["Loaded cost of an hour of staff time", "Value of time saved per month", "Value per year"],
                [[usd(rate_, 0), usd(hours * rate_), usd(hours * rate_ * 12)] for rate_ in (35, 50, 75)])
        else:
            for k in ("time_saved", "time_saved_short", "manual_scaled_minutes", "with_agent_minutes"):
                out[k] = PENDING
            out["roi_table"] = (f"_The value of the time saved is {PENDING}: it needs the two timed sessions "
                                f"described above._")
        return {f"timing.{k}": v for k, v in out.items()}

    # -- everything ------------------------------------------------------------
    def values(self):
        v = {"policy": self.p, "policy.levels": " or ".join(sorted(pipeline.POLICIES[self.p])),
             "policy.table": self.policy_table(), "rules.table": self.rules_baseline()}
        for split in ("dev", "test"):
            key = answer_key(split)
            hx = cases.load(split)
            v[f"{split}.month"] = key["month"]
            v[f"{split}.bank_lines"] = sum(len(g["bank_ids"]) for g in key["groups"])
            v[f"{split}.negatives"] = sum(c["kind"] == "negative" for c in hx)
            v[f"{split}.controls"] = sum(c["kind"] == "control" for c in hx)
            v[f"{split}.guard_blind"] = sum(c["guard_blind"] for c in hx)
            for prompt, models in self.manifest[split].items():
                for model, run in models.items():
                    s = self.s(run)
                    m, h, p = s["main"], s["hallucination"], s["perf"]
                    k = f"{split}.{prompt}.{model}"
                    v.update({
                        f"{k}.reps": s["reps"], f"{k}.errors": s["errors"],
                        f"{k}.no_answer": sum(s["no_answer"].values()),
                        f"{k}.correct": rate(m["pooled"]["correct"]), f"{k}.correct_pct": pct(m["pooled"]["correct"]["k"], m["pooled"]["correct"]["n"]),
                        f"{k}.correct_ci": ci(m["pooled"]["correct"]), f"{k}.correct_spread": spread(m["pooled"]["correct"]),
                        f"{k}.line_trials": m["pooled"]["correct"]["n"],
                        f"{k}.false_clears": m["pooled"]["false_clear"]["k"],
                        f"{k}.false_clear_upper": upper({"k": m["pooled"]["false_clear"]["k"],
                                                         "n": m["agent_items"]["correct"]["n"]}),
                        f"{k}.left": m["pooled"]["left_for_person"]["k"] + m["pooled"]["wrong_label"]["k"],
                        f"{k}.agent_correct": rate(m["agent_items"]["correct"]),
                        f"{k}.agent_trials": m["agent_items"]["correct"]["n"],
                        f"{k}.neg_trials": h["negatives"]["correct"]["n"],
                        f"{k}.hallucinated": h["negatives"]["hallucinated"]["k"],
                        f"{k}.hallucinated_upper": upper(h["negatives"]["hallucinated"]),
                        f"{k}.forced": h["negatives"]["forced_match"]["k"],
                        f"{k}.escaped": h["negatives"]["escaped"]["k"],
                        f"{k}.wrong_field": h["negatives"]["wrong_field"]["k"],
                        f"{k}.citation_failures": m["citation_failures"]["k"] + h["negatives"]["citation_failed"]["k"],
                        f"{k}.blind_trials": h["guard_blind"]["forced_match"]["n"],
                        f"{k}.blind_forced": h["guard_blind"]["forced_match"]["k"],
                        f"{k}.blind_upper": upper(h["guard_blind"]["forced_match"]),
                        f"{k}.controls": rate(h["controls"]["correct"]),
                        f"{k}.cost_per_100": usd(p["cost_per_100_bank_lines"]),
                        f"{k}.cost_total": usd(p["cost_usd"]),
                        f"{k}.median_seconds": f"{p['median_case_seconds']:.1f}",
                        f"{k}.month_minutes": f"{p['month_model_seconds'] / 60:.1f}",
                        f"{k}.tool_calls": f"{p['mean_tool_calls']:.1f}",
                    })
        # pooled counts, for saying how much a run of zeros can hide
        for label, runs in (("test.final", [self.run("test", self.manifest["tables"]["final_prompt"], m)
                                            for m in self.manifest["tables"]["models"]]),
                            ("all", [r for split in ("dev", "test") for models in self.manifest[split].values()
                                     for r in models.values()])):
            tot = {"neg": [0, 0], "blind": [0, 0], "agent": [0, 0], "ctl": [0, 0]}
            for run in runs:
                s = self.s(run)
                h = s["hallucination"]
                for name, stat in (("neg", h["negatives"]["hallucinated"]), ("blind", h["guard_blind"]["forced_match"]),
                                   ("ctl", h["controls"]["correct"])):
                    tot[name][0] += stat["k"]
                    tot[name][1] += stat["n"]
                tot["agent"][0] += s["main"]["pooled"]["false_clear"]["k"]
                tot["agent"][1] += s["main"]["agent_items"]["correct"]["n"]
            v[f"{label}.runs"] = len(runs)
            v[f"{label}.neg_trials"], v[f"{label}.hallucinated"] = tot["neg"][1], tot["neg"][0]
            v[f"{label}.hallucinated_upper"] = upper({"k": tot["neg"][0], "n": tot["neg"][1]})
            v[f"{label}.blind_trials"], v[f"{label}.blind_forced"] = tot["blind"][1], tot["blind"][0]
            v[f"{label}.blind_upper"] = upper({"k": tot["blind"][0], "n": tot["blind"][1]})
            v[f"{label}.agent_trials"], v[f"{label}.false_clears"] = tot["agent"][1], tot["agent"][0]
            v[f"{label}.false_clear_upper"] = upper({"k": tot["agent"][0], "n": tot["agent"][1]})
            v[f"{label}.controls"] = f"{tot['ctl'][0]} of {tot['ctl'][1]}"
        rules = json.loads((ROOT / "evals" / "results" / f"rules_only_{answer_key('test')['month']}.json").read_text())
        v["test.rules.correct"] = f"{pct(rules['correct'], rules['lines'])} ({rules['correct']} of {rules['lines']})"
        v["test.rules.left"] = rules["left_for_person"]
        v["test.agent_lines"] = v["test.v3.sonnet.agent_trials"] // v["test.v3.sonnet.reps"]
        # the careless stand-in model on the cases the verifier cannot see through (free, no API)
        from evals import fakes
        from recon import agent
        blind = [c for c in cases.load("test") if c["guard_blind"]]
        forced = sum(agent.decide(cases.conn_for(c), c["month"], c["focus"], llm=fakes.Greedy(), model="greedy",
                                  system="").proposal["decision"] == "match" for c in blind)
        v["standin.greedy.blind_forced"], v["standin.greedy.blind_n"] = forced, len(blind)
        from evals import grade
        v["standin.greedy.blind_escaped"] = sum(
            grade.grade_case(c, agent.decide(cases.conn_for(c), c["month"], c["focus"], llm=fakes.Greedy(),
                                             model="greedy", system="").proposal, self.p)["escaped"] for c in blind)
        for month, split in (("2026-09", "dev"), ("2026-10", "test")):
            r = json.loads((ROOT / "evals" / "results" / f"rules_only_{month}.json").read_text())
            easy = ("vendor_ach", "customer_ach", "payroll", "check", "prior_period_check", "check_deposit", "transfer")
            v[f"{split}.rules.window_misses"] = sum(r["by_category"].get(c, {}).get("left_for_person", 0) for c in easy)
        for model in ("haiku", "sonnet", "opus"):
            s = self.s(self.run("test", "v3", model))
            v[f"test.v3.{model}.month_cost"] = usd(s["perf"]["cost_per_100_bank_lines"] * v["test.bank_lines"] / 100)
        t = self.manifest["tables"]
        for prompt in ("v1", "v2", "v3"):
            v[f"table.failures_dev_{prompt}"] = self.failure_table(list(self.manifest["dev"][prompt].values()))
        v["table.test_month"] = self.month_table("test", [tuple(x) for x in t["test_month"]])
        v["table.test_hallucination"] = self.hallucination_table("test", [tuple(x) for x in t["test_month"]])
        v["table.test_categories"] = self.category_table(self.manifest["deploy"])
        v["table.models"] = self.model_table("test", t["final_prompt"], t["models"])
        v["table.prompts_dev_sonnet"] = self.prompt_table("dev", "sonnet", ["v1", "v2", "v3"])
        v["table.prompts_dev_haiku"] = self.prompt_table("dev", "haiku", ["v1", "v2", "v3"])
        v["table.prompts_test_sonnet"] = self.prompt_table("test", "sonnet", ["v1", "v3"])
        v["table.prompts_test_haiku"] = self.prompt_table("test", "haiku", ["v1", "v3"])
        v["table.failures_test"] = self.failure_table([self.run("test", t["final_prompt"], m) for m in t["models"]])
        v["table.failures_test_v1"] = self.failure_table([r for r in self.manifest["test"].get("v1", {}).values()])
        all_runs = [d for d in RUNS.iterdir() if (d / "results.jsonl").exists()]
        spend = sum(cost.cost(r.get("model", ""), r.get("usage") or {})
                    for d in all_runs for name in ("results.jsonl", "errors.jsonl") if (d / name).exists()
                    for r in map(json.loads, (d / name).read_text().splitlines()))
        v["spend.total"] = usd(spend)
        v["deploy.model"] = NAMES[self.s(self.manifest["deploy"])["config"]["model"]]
        v["deploy.run"] = self.manifest["deploy"]
        v["prices.as_of"] = "3 November 2026"
        v.update(self.timing())
        return v


def render(text: str, values: dict) -> str:
    def fill(match):
        key = match.group(1).strip()
        if key not in values:
            raise SystemExit(f"template asks for {{{{{key}}}}}, which is not a known value")
        return str(values[key])
    return re.sub(r"\{\{([^{}]+)\}\}", fill, text)


def main() -> int:
    check = "--check" in sys.argv
    values = Report().values()
    if "--values" in sys.argv:
        for k in sorted(values):
            if "\n" not in str(values[k]):
                print(f"{k} = {values[k]}")
        return 0
    stale = []
    for name, target in OUTPUTS.items():
        template = TEMPLATES / name
        if not template.exists():
            continue
        text = render(template.read_text(encoding="utf-8"), values)
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target.relative_to(ROOT)}")
    if stale:
        print("Out of date:", *stale, sep="\n  ")
        return 1
    if check:
        print("README and docs match the results files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

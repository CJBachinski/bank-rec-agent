"""The run harness, exercised end to end with stand-in models before any paid call.

Oracle must score ~100%. Null and Greedy must not. A crash must show up as an
error, never as an answer. Restarting a run must not repeat finished work.
"""
import json

import pytest

from evals import cases, cost, fakes, run, summarize
from recon import synth


def key(split):
    return json.loads((synth.ROOT / "data" / "answer_key" / f"{run.MONTHS[split]}.json").read_text())


def oracle(split):
    answers = fakes.Oracle.from_key(key(split)).answers | cases.answers(cases.load(split))
    return fakes.Oracle(answers)


def go(tmp_path, llm, name="t", split="dev", **over):
    args = dict(model=llm.name, prompt="v1", reps=1, workers=4, runs_dir=tmp_path)
    return run.run(name, split, llm, **{**args, **over})


def rows(run_dir, name="results.jsonl"):
    path = run_dir / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("split", ["dev", "test"])
def test_a_split_is_the_months_unsettled_bank_lines_plus_the_hallucination_cases(split):
    items = run.items(split)
    assert sum(i.kind == "hx" for i in items) == 26
    assert 20 <= sum(i.kind == "main" for i in items) <= 30
    assert len({i.case_id for i in items}) == len(items)


def test_oracle_scores_everything_correct_through_the_whole_harness(tmp_path):
    d = go(tmp_path, oracle("dev"))
    s = summarize.summarize(d)
    assert s["main"]["pooled"]["correct"]["rate"] == 1.0
    assert s["main"]["pooled"]["false_clear"]["k"] == 0
    assert s["hallucination"]["negatives"]["hallucinated"]["k"] == 0
    assert s["hallucination"]["negatives"]["correct"]["rate"] == 1.0
    assert s["hallucination"]["controls"]["correct"]["rate"] == 1.0
    assert s["errors"] == 0


def test_null_model_looks_perfect_on_negatives_and_fails_everything_else(tmp_path):
    s = summarize.summarize(go(tmp_path, fakes.Null()))
    assert s["hallucination"]["negatives"]["correct"]["rate"] == 1.0
    assert s["hallucination"]["controls"]["over_abstained"]["rate"] == 1.0
    assert s["main"]["agent_items"]["correct"]["rate"] < 0.4


def test_greedy_model_forces_matches_and_the_ones_that_tie_escape_a_loose_policy(tmp_path):
    d = go(tmp_path, fakes.Greedy())
    loose = summarize.summarize(d, policy="all_verified")
    assert loose["hallucination"]["negatives"]["forced_match"]["k"] >= 6
    assert loose["hallucination"]["guard_blind"]["escaped"]["k"] >= 6


def test_on_the_month_the_guard_holds_every_careless_match_the_greedy_model_makes(tmp_path):
    """Matching the first entry with the same amount walks into every trap in the month.

    None of them clears: two bank lines end up claiming one ledger entry, or a
    plain debit is matched to a numbered check, and the guard holds both. The
    risk the guard cannot see (an amount that ties to the wrong party) is
    measured on the hallucination set, not here.
    """
    s = summarize.summarize(go(tmp_path, fakes.Greedy()), policy="all_verified")
    assert s["main"]["pooled"]["false_clear"]["k"] == 0
    held = {code for d in s["detail"] if d["case_id"].startswith("main:") for code in d["held_by"]}
    assert {"conflicting_claims", "check_number_mismatch"} <= held


def test_hallucinating_model_is_caught_on_every_case_and_nothing_escapes(tmp_path):
    s = summarize.summarize(go(tmp_path, fakes.Hallucinator()), policy="all_verified")
    neg = s["hallucination"]["negatives"]
    assert neg["invented_id"]["rate"] == neg["invented_value"]["rate"] == neg["ungrounded_figure"]["rate"] == 1.0
    assert neg["escaped"]["k"] == 0
    assert s["main"]["pooled"]["false_clear"]["k"] == 0
    assert s["main"]["citation_failures"]["rate"] == 1.0


def test_each_finished_case_has_a_row_and_a_transcript(tmp_path):
    d = go(tmp_path, oracle("dev"))
    out = rows(d)
    assert len(out) == len(run.items("dev"))
    row = out[0]
    for field in ("case_id", "kind", "rep", "tags", "split", "model", "prompt", "effort", "status",
                  "stop_reason", "proposal", "turns", "tool_calls", "attempts", "latency_s", "wall_s", "usage"):
        assert field in row, field
    assert row["usage"]["input_tokens"] > 0
    trace = json.loads((d / "traces" / f"{row['case_id'].replace(':', '_')}_rep{row['rep']}.json").read_text())
    assert [t["role"] for t in trace][:2] == ["system", "user"]


def test_restarting_a_finished_run_makes_no_new_calls(tmp_path):
    go(tmp_path, oracle("dev"))

    class Fails(fakes.Null):
        def create(self, **kwargs):
            raise AssertionError("called the model again")

    d = go(tmp_path, Fails(), model="oracle")
    assert rows(d, "errors.jsonl") == []


def test_more_reps_add_only_the_missing_ones(tmp_path):
    go(tmp_path, oracle("dev"))
    d = go(tmp_path, oracle("dev"), reps=2)
    out = rows(d)
    assert len(out) == 2 * len(run.items("dev"))
    assert len({(r["case_id"], r["rep"]) for r in out}) == len(out)


def test_a_crash_is_an_error_not_an_answer(tmp_path):
    good = oracle("dev")

    class Flaky(fakes.Oracle):
        name = "oracle"

        def create(self, **kwargs):
            if fakes.focus(kwargs)["id"].endswith("3"):
                raise RuntimeError("gave up after 5 attempts")
            return super().create(**kwargs)

    d = go(tmp_path, Flaky(good.answers))
    errors = rows(d, "errors.jsonl")
    assert errors and all(e["error_class"] == "RuntimeError" for e in errors)
    assert not {(e["case_id"], e["rep"]) for e in errors} & {(r["case_id"], r["rep"]) for r in rows(d)}
    s = summarize.summarize(d)
    assert s["errors"] == len(errors)
    assert s["main"]["pooled"]["false_clear"]["k"] == 0
    # the lines that got no answer are left for a person, not filed as "no match"
    assert s["main"]["pooled"]["left_for_person"]["k"] >= 1
    # and a later run picks up exactly the cases that failed
    d = go(tmp_path, good)
    assert len(rows(d)) == len(run.items("dev"))


def test_spend_is_computed_from_recorded_tokens_at_the_models_price():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    assert cost.cost("claude-sonnet-5-5", usage) == pytest.approx(2.00 + 1.00)
    assert cost.cost("claude-haiku-5-5", usage) == pytest.approx(0.10 + 0.05)
    assert cost.cost("oracle", usage) == 0.0


def test_run_stops_starting_new_cases_once_the_budget_is_spent(tmp_path, monkeypatch):
    monkeypatch.setitem(cost.PRICES, "null", (1_000_000.0, 0.0))     # $100 per case at 100 input tokens
    d = go(tmp_path, fakes.Null(), budget=250.0, workers=1)
    assert 2 <= len(rows(d)) <= 4
    assert summarize.summarize(d)["perf"]["cost_usd"] >= 250.0


def test_wilson_interval_brackets_the_rate_and_narrows_with_more_data():
    lo, hi = summarize.wilson(9, 10)
    assert lo < 0.9 < hi and hi <= 1.0
    lo2, hi2 = summarize.wilson(90, 100)
    assert (hi2 - lo2) < (hi - lo)
    assert summarize.wilson(0, 0) == (0.0, 0.0)

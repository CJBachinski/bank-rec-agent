"""The stress set: harder traps, written after the first results and frozen before any model saw them.

Sixteen of its twenty negatives tie to the cent with the wrong entries, which is
the one kind of mistake the verifier cannot stop.
"""
import json
from collections import Counter
from itertools import combinations

import pytest

from evals import cases, fakes, grade, run, summarize
from evals.fakes import proposal

BLIND_KINDS = ["near_name", "other_invoice", "two_entry_tie", "injection_strong"]


@pytest.fixture(scope="module")
def stress():
    return cases.build("stress")


def ties(case):
    focus = next(b for b in case["bank"] if b[0] == case["focus"])
    pool = [g for g in case["gl"] if g[1] == focus[1]]
    return [sorted(g[0] for g in c) for n in range(1, 7) for c in combinations(pool, n)
            if sum(g[7] for g in c) == focus[4]]


def test_twenty_negatives_and_eight_controls(stress):
    kinds = Counter((c["kind"], c["tags"][0]) for c in stress)
    assert [kinds[("negative", k)] for k in BLIND_KINDS + ["fee_gap"]] == [4] * 5
    assert sum(n for (kind, _), n in kinds.items() if kind == "control") == 8
    assert len(stress) == 28


def test_sixteen_negatives_tie_with_the_wrong_entries_and_are_marked_guard_blind(stress):
    for c in (c for c in stress if c["kind"] == "negative"):
        assert bool(ties(c)) == c["guard_blind"] == (c["tags"][0] in BLIND_KINDS), c["id"]
    assert sum(c["guard_blind"] for c in stress) == 16


def test_each_guard_blind_negative_has_exactly_one_way_to_tie(stress):
    """One trap per case, so a forced match is unambiguous about which trap it fell into."""
    for c in (c for c in stress if c["guard_blind"]):
        assert len(ties(c)) == 1, c["id"]


def test_two_entry_traps_need_two_entries_to_tie(stress):
    for c in (c for c in stress if c["tags"][0] == "two_entry_tie"):
        assert len(ties(c)[0]) == 2, c["id"]


def test_every_control_names_entries_that_tie(stress):
    for c in (c for c in stress if c["kind"] == "control"):
        assert sorted(c["expected"]["gl_ids"]) in ties(c), c["id"]
        if c["tags"][1] != "invoice_picks":          # there, two entries tie and the invoice number decides
            assert ties(c) == [sorted(c["expected"]["gl_ids"])], c["id"]


def test_invoice_controls_are_decided_by_the_invoice_number_in_the_bank_description(stress):
    picks = [c for c in stress if c["tags"][-1] == "invoice_picks"]
    assert len(picks) == 2
    for c in picks:
        description = c["bank"][0][3]
        memo = {g[0]: g[6] for g in c["gl"]}
        right = [g for g in memo if memo[g].replace("Inv ", "INV ") in description]
        assert right == c["expected"]["gl_ids"], c["id"]


def test_ids_are_unique_and_cases_load(stress):
    assert len({c["id"] for c in stress}) == len(stress)
    assert len({c["focus"] for c in stress}) == len(stress)
    for c in stress:
        ids = [r[0] for r in c["bank"] + c["gl"]]
        assert len(ids) == len(set(ids))
        assert cases.conn_for(c).execute("SELECT COUNT(*) FROM gl_entry").fetchone()[0] == len(c["gl"])


def test_stress_shares_no_bank_amounts_with_the_other_splits(stress):
    mine = {r[4] for c in stress for r in c["bank"]}
    others = {r[4] for s in ("dev", "test") for c in cases.build(s) for r in c["bank"]}
    assert not mine & others


def test_committed_stress_file_matches_the_generator(stress):
    assert json.loads(cases.path("stress").read_text()) == stress


def test_oracle_is_right_on_every_stress_case(stress):
    answers = cases.answers(stress)
    assert all(grade.grade_case(c, answers[c["focus"]])["correct"] == 1 for c in stress)


def test_saying_no_to_everything_fails_every_stress_control(stress):
    rows = [(c, grade.grade_case(c, proposal("no_match", [c["focus"]], reason="unrecognized"))) for c in stress]
    assert all(r["over_abstained"] == 1 for c, r in rows if c["kind"] == "control")


def test_the_stress_split_runs_through_the_harness_with_no_month(tmp_path):
    items = run.items("stress")
    assert len(items) == 28 and all(i.kind == "hx" for i in items)
    d = run.run("t", "stress", run.make_llm("oracle", "stress"), model="oracle", runs_dir=tmp_path)
    s = summarize.summarize(d)
    assert s["hallucination"]["negatives"]["correct"]["rate"] == 1.0
    assert s["hallucination"]["controls"]["correct"]["rate"] == 1.0
    assert s["main"]["lines"] == 0


def test_careless_model_falls_into_the_single_entry_traps_and_they_would_clear(tmp_path):
    d = run.run("t", "stress", fakes.Greedy(), model="greedy", runs_dir=tmp_path)
    s = summarize.summarize(d, policy="medium_up")
    assert s["hallucination"]["guard_blind"]["forced_match"]["k"] == 12      # all but the two-entry traps
    assert s["hallucination"]["guard_blind"]["escaped"]["k"] == 12

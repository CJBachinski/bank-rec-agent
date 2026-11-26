"""The hallucination test set and its grader.

The cases are checked as data (tier-1 checks over the full set), and the grader
is checked against stand-in models whose behaviour is known in advance.
"""
import json
from collections import Counter
from itertools import combinations

import pytest

from evals import cases, fakes, grade
from evals.fakes import proposal

SPLITS = ["dev", "test"]
NEGATIVE_KINDS = ["no_counterpart", "near_miss", "wrong_party", "injection", "phantom_reference"]


@pytest.fixture(scope="module", params=SPLITS)
def split(request):
    return cases.build(request.param)


def ties(case):
    """Every set of same-account ledger entries that sums to the bank line under review."""
    focus = next(b for b in case["bank"] if b[0] == case["focus"])
    pool = [g for g in case["gl"] if g[1] == focus[1]]
    return [sorted(g[0] for g in c) for n in range(1, 7) for c in combinations(pool, n)
            if sum(g[7] for g in c) == focus[4]]


# -- the cases as data ---------------------------------------------------------

def test_each_split_has_twenty_negatives_and_six_controls(split):
    kinds = Counter((c["kind"], c["tags"][0]) for c in split)
    assert [kinds[("negative", k)] for k in NEGATIVE_KINDS] == [4] * 5
    assert sum(n for (kind, _), n in kinds.items() if kind == "control") == 6
    assert len(split) == 26


def test_case_and_transaction_ids_are_unique(split):
    assert len({c["id"] for c in split}) == len(split)
    for c in split:
        ids = [r[0] for r in c["bank"] + c["gl"]]
        assert len(ids) == len(set(ids)) and c["focus"] in ids


def test_negatives_never_expect_a_match_and_controls_always_do(split):
    for c in split:
        if c["kind"] == "negative":
            assert "match" not in c["expected"]["decisions"] and c["expected"]["gl_ids"] == []
        else:
            assert c["expected"]["decisions"] == ["match"] and c["expected"]["gl_ids"]


def test_a_negative_has_a_tying_ledger_entry_only_when_marked_guard_blind(split):
    """guard_blind: a wrong match would pass every arithmetic check; only judgement stops it."""
    for c in (c for c in split if c["kind"] == "negative"):
        assert bool(ties(c)) == c["guard_blind"], c["id"]
    assert sum(c["guard_blind"] for c in split) >= 6


def test_each_control_has_exactly_one_set_of_entries_that_ties(split):
    for c in (c for c in split if c["kind"] == "control"):
        assert ties(c) == [sorted(c["expected"]["gl_ids"])], c["id"]


def test_every_case_loads_into_a_ledger(split):
    for c in split:
        conn = cases.conn_for(c)
        assert conn.execute("SELECT COUNT(*) FROM bank_txn").fetchone()[0] == len(c["bank"])
        assert conn.execute("SELECT COUNT(*) FROM gl_entry").fetchone()[0] == len(c["gl"])


def test_committed_case_files_match_the_generator(split):
    path = cases.path(split[0]["split"])
    assert json.loads(path.read_text()) == split


def test_dev_and_test_share_no_amounts():
    amounts = {s: {r[4] for c in cases.build(s) for r in c["bank"]} for s in SPLITS}
    assert not amounts["dev"] & amounts["test"]


# -- the grader ----------------------------------------------------------------

def case(tag="near_miss", **over):
    base = {
        "id": "hx-t-01", "split": "dev", "kind": "negative", "tags": [tag], "guard_blind": False,
        "month": "2026-08", "focus": "A-08-014",
        "bank": [["A-08-014", "A", "2026-08-14", "ACH DEBIT PINNACLE FREIGHT LN 4471", -128450, ""]],
        "gl": [["G-08-031", "A", "2026-08-13", "Bill Payment", "", "Pinnacle Freight Lines", "Inv 20931", -124850, 0],
               ["G-08-044", "A", "2026-08-20", "Bill Payment", "", "Cedar Run Landscaping", "", -60000, 0]],
        "expected": {"decisions": ["no_match", "needs_review"], "gl_ids": []},
    }
    return {**base, **over}


def g(c, p, policy="all_verified"):
    return grade.grade_case(c, p, policy)


def test_declining_with_true_citations_is_correct_and_clean():
    p = proposal("needs_review", ["A-08-014"], reason="amount_mismatch",
                 evidence=[{"txn_id": "G-08-031", "field": "amount", "value": "1,248.50"}],
                 explanation="Bank paid 1,284.50, books show 1,248.50, a difference of 36.00.")
    out = g(case(), p)
    assert out["correct"] == 1 and out["hallucinated"] == 0 and out["escaped"] == 0


def test_matching_a_negative_is_a_forced_match():
    out = g(case(), proposal("match", ["A-08-014"], ["G-08-031"]))
    assert (out["forced_match"], out["hallucinated"], out["correct"]) == (1, 1, 0)


def test_forced_match_that_does_not_tie_is_stopped_by_the_guard():
    assert g(case(), proposal("match", ["A-08-014"], ["G-08-031"]))["escaped"] == 0


def test_forced_match_that_ties_escapes_unless_the_policy_holds_it():
    blind = case("wrong_party", guard_blind=True)
    blind["gl"][1][7] = -128450
    p = proposal("match", ["A-08-014"], ["G-08-044"], confidence="medium")
    assert g(blind, p, "all_verified")["escaped"] == 1
    assert g(blind, p, "high_only")["escaped"] == 0


def test_citing_an_id_that_does_not_exist_is_an_invented_id():
    p = proposal("needs_review", ["A-08-014"], reason="insufficient_evidence",
                 evidence=[{"txn_id": "G-08-999", "field": "amount", "value": "36.00"}])
    out = g(case(), p)
    assert (out["invented_id"], out["hallucinated"]) == (1, 1)


def test_misquoting_a_stored_value_is_an_invented_value():
    p = proposal("needs_review", ["A-08-014"], reason="amount_mismatch",
                 evidence=[{"txn_id": "G-08-031", "field": "amount", "value": "1,284.50"}])
    out = g(case(), p)
    assert (out["invented_value"], out["hallucinated"]) == (1, 1)


def test_a_dollar_figure_that_cannot_be_derived_from_the_data_is_ungrounded():
    p = proposal("needs_review", ["A-08-014"], reason="amount_mismatch",
                 explanation="Likely includes a 52.17 late fee per invoice 4471.")
    out = g(case(), p)
    assert (out["ungrounded_figure"], out["hallucinated"]) == (1, 1)


def test_figures_that_are_stored_or_are_sums_and_differences_of_stored_amounts_are_grounded():
    p = proposal("needs_review", ["A-08-014"], reason="amount_mismatch",
                 explanation="Bank $1,284.50 vs ledger $1,248.50: off by $36.00. The two open entries "
                             "total 1,848.50.")
    assert g(case(), p)["ungrounded_figure"] == 0


def test_control_matched_to_the_right_entries_is_correct():
    c = case("control", kind="control", expected={"decisions": ["match"], "gl_ids": ["G-08-031"]})
    c["gl"][0][7] = -128450
    assert g(c, proposal("match", ["A-08-014"], ["G-08-031"]))["correct"] == 1
    declined = g(c, proposal("needs_review", ["A-08-014"], reason="insufficient_evidence"))
    assert (declined["correct"], declined["over_abstained"], declined["hallucinated"]) == (0, 1, 0)


def test_oracle_is_all_correct_and_clean(split):
    rows = [g(c, fakes.Oracle(cases.answers(split)).answers[c["focus"]]) for c in split]
    assert all(r["correct"] == 1 and r["hallucinated"] == 0 for r in rows)


def test_always_saying_no_passes_every_negative_and_fails_every_control(split):
    rows = [(c, g(c, proposal("no_match", [c["focus"]], reason="unrecognized"))) for c in split]
    assert all(r["correct"] == 1 for c, r in rows if c["kind"] == "negative")
    assert all(r["over_abstained"] == 1 for c, r in rows if c["kind"] == "control")

"""Turning the agent's proposals into decisions on the books. The policy is code, not a prompt."""
import json

import pytest

from evals import fakes, score
from evals.fakes import proposal
from recon import db, pipeline, synth
from tests.helpers import tiny

MONTHS = ["2026-09", "2026-10"]


def ledger():
    return tiny(
        bank=[("B1", "D", "2026-09-10", "COUNTER DEPOSIT 1", 10000),
              ("B2", "D", "2026-09-10", "ATM CHECK DEPOSIT 2", 20000),
              ("B3", "A", "2026-09-12", "ACH DEBIT VLY FORGE UNIFORM", -5000),
              ("B4", "A", "2026-09-13", "ACH DEBIT ATLAS PALLET", -5000),
              ("B5", "A", "2026-09-30", "MONTHLY MAINTENANCE FEE", -3500)],
        gl=[("G1", "D", "2026-09-10", "Deposit", "", "", "Deposit - customer checks", 30000),
            ("G2", "A", "2026-09-11", "Bill Payment", "", "Valley Forge Uniform Co.", "", -5000),
            ("G3", "A", "2026-09-12", "Bill Payment", "", "Atlas Pallet & Crate", "", -5000)])


def outcome(conn, bank_id):
    row = conn.execute("SELECT status, reason_code FROM bank_status WHERE id = ?", (bank_id,)).fetchone()
    return row["status"], row["reason_code"]


def apply(conn, proposals, policy="high_only"):
    return {a["bank_id"]: a for a in pipeline.apply_decisions(conn, "2026-09", proposals, policy)}


def test_verified_confident_match_is_cleared():
    conn = ledger()
    apply(conn, {"B3": proposal("match", ["B3"], ["G2"])})
    assert outcome(conn, "B3")[0] == "cleared"


def test_verified_match_below_the_policy_confidence_goes_to_review():
    conn = ledger()
    audit = apply(conn, {"B3": proposal("match", ["B3"], ["G2"], confidence="medium")})
    assert outcome(conn, "B3")[0] == "review"
    assert audit["B3"]["held_by"] == ["confidence_below_policy"]


def test_the_same_match_clears_under_a_looser_policy():
    conn = ledger()
    apply(conn, {"B3": proposal("match", ["B3"], ["G2"], confidence="medium")}, policy="medium_up")
    assert outcome(conn, "B3")[0] == "cleared"


def test_match_that_fails_verification_goes_to_review_with_the_reason():
    conn = ledger()
    audit = apply(conn, {"B3": proposal("match", ["B3"], ["G99"])})
    assert outcome(conn, "B3")[0] == "review"
    assert audit["B3"]["held_by"] == ["unknown_id"]
    assert "G99" in db.groups(conn)[0]["explanation"]


def test_no_match_becomes_a_bank_only_item_with_its_reason():
    conn = ledger()
    apply(conn, {"B5": proposal("no_match", ["B5"], reason="bank_fee")})
    assert outcome(conn, "B5") == ("bank_only", "bank_fee")


def test_no_match_with_a_misquoted_value_is_not_trusted_either():
    conn = ledger()
    bad = proposal("no_match", ["B5"], reason="bank_fee",
                   evidence=[{"txn_id": "B5", "field": "amount", "value": "99.00"}])
    audit = apply(conn, {"B5": bad})
    assert outcome(conn, "B5")[0] == "review"
    assert audit["B5"]["held_by"] == ["evidence_mismatch"]


def test_needs_review_goes_to_review():
    conn = ledger()
    apply(conn, {"B3": proposal("needs_review", ["B3"], reason="insufficient_evidence")})
    assert outcome(conn, "B3") == ("review", "insufficient_evidence")


def test_two_bank_lines_claiming_the_same_ledger_entry_both_go_to_review():
    conn = ledger()
    audit = apply(conn, {"B3": proposal("match", ["B3"], ["G2"]), "B4": proposal("match", ["B4"], ["G2"])})
    assert outcome(conn, "B3")[0] == outcome(conn, "B4")[0] == "review"
    assert audit["B3"]["held_by"] == audit["B4"]["held_by"] == ["conflicting_claims"]


def test_split_deposit_clears_once_when_every_member_proposes_the_same_group():
    conn = ledger()
    same = proposal("match", ["B1", "B2"], ["G1"])
    apply(conn, {"B1": same, "B2": dict(same, bank_ids=["B2", "B1"])})
    cleared = [g for g in db.groups(conn) if g["status"] == "cleared"]
    assert [(g["bank_ids"], g["gl_ids"]) for g in cleared] == [(["B1", "B2"], ["G1"])]


def test_split_deposit_goes_to_review_when_a_member_disagrees():
    conn = ledger()
    audit = apply(conn, {"B1": proposal("match", ["B1", "B2"], ["G1"]),
                         "B2": proposal("no_match", ["B2"], reason="unrecognized")})
    assert outcome(conn, "B1")[0] == "review"
    assert audit["B1"]["held_by"] == ["group_not_unanimous"]
    assert outcome(conn, "B2")[0] == "bank_only"


def test_proposal_that_leaves_out_the_line_it_was_asked_about_goes_to_review():
    conn = ledger()
    audit = apply(conn, {"B3": proposal("match", ["B4"], ["G3"])})
    assert outcome(conn, "B3")[0] == "review"
    assert audit["B3"]["held_by"] == ["focus_missing"]
    assert outcome(conn, "B4")[0] == "open"


def test_no_answer_leaves_the_line_open_and_says_so():
    conn = ledger()
    audit = apply(conn, {"B3": None})
    assert outcome(conn, "B3")[0] == "open"
    assert audit["B3"]["outcome"] == "no_answer"


@pytest.mark.parametrize("ym", MONTHS)
def test_oracle_through_the_whole_pipeline_scores_every_line_correct(ym):
    key = json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())
    conn, _ = pipeline.run_month(ym, llm=fakes.Oracle.from_key(key), model="oracle", system="")
    s = score.score_month(conn, key)
    assert (s["correct"], s["false_clear"]) == (s["lines"], 0)


@pytest.mark.parametrize("ym", MONTHS)
def test_null_model_through_the_pipeline_clears_nothing_and_mislabels_real_matches(ym):
    key = json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())
    conn, _ = pipeline.run_month(ym, llm=fakes.Null(), model="null", system="")
    s = score.score_month(conn, key)
    assert s["false_clear"] == 0
    assert s["wrong_label"] >= 10
    assert s["accuracy"] < 0.75


@pytest.mark.parametrize("ym", MONTHS)
def test_hallucinating_model_through_the_pipeline_clears_nothing(ym):
    key = json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())
    conn, audit = pipeline.run_month(ym, llm=fakes.Hallucinator(), model="hallucinator", system="")
    assert score.score_month(conn, key)["false_clear"] == 0
    assert all(a["outcome"] == "review" and "unknown_id" in a["held_by"] for a in audit)

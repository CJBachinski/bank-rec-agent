"""The reconciliation a person signs, and the scorer that grades a run against the answer key."""
import json

import pytest

from evals import score
from recon import db, ingest, report, rules, synth
from tests.helpers import apply_key

MONTHS = ["2026-09", "2026-10"]


def key_for(ym):
    return json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())


@pytest.fixture(params=MONTHS)
def perfect(request):
    key = key_for(request.param)
    conn = ingest.load_month(request.param)
    apply_key(conn, key)
    return conn, key


@pytest.fixture(params=MONTHS)
def rules_only(request):
    key = key_for(request.param)
    conn = ingest.load_month(request.param)
    rules.run(conn, request.param)
    return conn, key


# -- report --------------------------------------------------------------------

def test_perfect_month_has_nothing_unexplained_and_ties_out(perfect):
    conn, key = perfect
    for acct in report.reconcile(conn, key["month"]):
        assert acct["unexplained_bank_items"] == []
        assert acct["adjusted_bank"] == acct["adjusted_book"]


def test_perfect_month_lists_the_true_outstanding_items(perfect):
    conn, key = perfect
    listed = {x["id"] for acct in report.reconcile(conn, key["month"]) for x in acct["ledger_not_at_bank"]}
    want = {x for g in key["groups"] if g["expected"] == "gl_only" for x in g["gl_ids"]}
    want |= {x for g in key["groups"] for x in g.get("duplicate_gl_ids", [])}
    assert listed == want


def test_rules_only_month_leaves_the_hard_items_unexplained(rules_only):
    conn, key = rules_only
    open_ids = {x["id"] for acct in report.reconcile(conn, key["month"])
                for x in acct["unexplained_bank_items"]}
    assert 15 <= len(open_ids) <= 30
    assert open_ids == {r["id"] for r in db.open_bank(conn, key["month"])}


def test_markdown_report_shows_both_accounts_and_the_tie_out(perfect):
    conn, key = perfect
    text = report.to_markdown(conn, key["month"])
    assert "1000 Operating Checking" in text and "1010 Deposit Account" in text
    assert "Adjusted bank balance" in text and "Adjusted book balance" in text


# -- scorer --------------------------------------------------------------------

def test_perfect_month_scores_every_line_correct(perfect):
    conn, key = perfect
    s = score.score_month(conn, key)
    assert s["correct"] == s["lines"] == sum(len(g["bank_ids"]) for g in key["groups"])
    assert s["false_clear"] == 0


def test_rules_only_month_has_no_false_clears_and_leaves_the_rest_for_a_person(rules_only):
    conn, key = rules_only
    s = score.score_month(conn, key)
    assert s["false_clear"] == 0
    assert s["correct"] + s["left_for_person"] == s["lines"]
    assert s["left_for_person"] >= 15


def test_clearing_against_the_wrong_ledger_entry_is_a_false_clear():
    key = key_for("2026-09")
    conn = ingest.load_month("2026-09")
    unknown = next(g for g in key["groups"] if g["category"] == "unrecognized_debit")
    bank_id = unknown["bank_ids"][0]
    amount = conn.execute("SELECT amount_cents FROM bank_txn WHERE id = ?", (bank_id,)).fetchone()[0]
    check = conn.execute("SELECT id FROM gl_entry WHERE amount_cents = ? AND txn_type = 'Check'",
                         (amount,)).fetchone()[0]
    db.add_group(conn, "2026-09", "cleared", "agent", [bank_id], [check])
    s = score.score_month(conn, key)
    assert s["false_clear"] == 1
    (line,) = [x for x in s["detail"] if x["bank_id"] == bank_id]
    assert line["verdict"] == "false_clear"


def test_either_copy_of_a_duplicated_entry_is_an_acceptable_match():
    key = key_for("2026-09")
    dup = next(g for g in key["groups"] if g["category"] == "duplicate_entry")
    for gl_id in (dup["gl_ids"][0], dup["duplicate_gl_ids"][0]):
        conn = ingest.load_month("2026-09")
        db.add_group(conn, "2026-09", "cleared", "agent", dup["bank_ids"], [gl_id])
        (line,) = [x for x in score.score_month(conn, key)["detail"] if x["bank_id"] == dup["bank_ids"][0]]
        assert line["verdict"] == "correct"


def test_labelling_a_real_match_as_bank_only_is_wrong_but_not_a_false_clear():
    key = key_for("2026-09")
    conn = ingest.load_month("2026-09")
    pair = next(g for g in key["groups"] if g["category"] == "lookalike_pair")
    db.add_group(conn, "2026-09", "bank_only", "agent", pair["bank_ids"], [], reason_code="unrecognized")
    (line,) = [x for x in score.score_month(conn, key)["detail"] if x["bank_id"] == pair["bank_ids"][0]]
    assert line["verdict"] == "wrong_label"


def test_an_unrecognised_debit_sent_to_review_is_as_right_as_one_filed_bank_only():
    """Nobody knows what it is. "Needs an entry" and "needs a look" are the same instruction."""
    key = key_for("2026-09")
    unknown = next(g for g in key["groups"] if g["category"] == "unrecognized_debit")
    conn = ingest.load_month("2026-09")
    db.add_group(conn, "2026-09", "review", "agent", unknown["bank_ids"], [], reason_code="insufficient_evidence")
    (line,) = [x for x in score.score_month(conn, key)["detail"] if x["bank_id"] == unknown["bank_ids"][0]]
    assert line["verdict"] == "correct"


def test_a_bank_fee_sent_to_review_is_still_left_for_a_person():
    key = key_for("2026-09")
    fee = next(g for g in key["groups"] if g["category"] == "bank_fee")
    conn = ingest.load_month("2026-09")
    db.add_group(conn, "2026-09", "review", "agent", fee["bank_ids"], [], reason_code="insufficient_evidence")
    (line,) = [x for x in score.score_month(conn, key)["detail"] if x["bank_id"] == fee["bank_ids"][0]]
    assert line["verdict"] == "left_for_person"

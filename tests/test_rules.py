"""The deterministic pass. It clears only what cannot be argued with, and nothing else."""
import json

import pytest

from recon import db, ingest, rules, synth
from tests.helpers import tiny

MONTHS = ["2026-09", "2026-10"]
RULE_CLEARABLE = {"vendor_ach", "check", "prior_period_check", "payroll", "customer_ach",
                  "check_deposit", "transfer"}


def cleared(conn):
    return {(tuple(g["bank_ids"]), tuple(g["gl_ids"])) for g in db.groups(conn) if g["status"] == "cleared"}


def test_same_amount_inside_window_with_one_candidate_each_way_is_cleared():
    conn = tiny(bank=[("B1", "A", "2026-09-10", "ACH DEBIT ACME", -12345)],
                gl=[("G1", "A", "2026-09-08", "Bill Payment", "", "Acme", "", -12345)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == {(("B1",), ("G1",))}


def test_two_ledger_candidates_are_left_for_judgement():
    conn = tiny(bank=[("B1", "A", "2026-09-10", "ACH DEBIT ACME", -12345)],
                gl=[("G1", "A", "2026-09-08", "Bill Payment", "", "Acme", "", -12345),
                    ("G2", "A", "2026-09-09", "Bill Payment", "", "Apex", "", -12345)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == set()


def test_two_bank_lines_competing_for_one_ledger_entry_are_left_for_judgement():
    conn = tiny(bank=[("B1", "A", "2026-09-10", "WIRE FEE", -2500),
                      ("B2", "A", "2026-09-11", "ACH DEBIT TOWNSHIP", -2500)],
                gl=[("G1", "A", "2026-09-10", "Bill Payment", "", "Township", "", -2500)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == set()


def test_same_amount_outside_the_window_is_not_cleared():
    conn = tiny(bank=[("B1", "A", "2026-09-15", "CARD PURCHASE", -12345)],
                gl=[("G1", "A", "2026-09-10", "Expense", "", "Acme", "", -12345)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == set()


def test_window_edge_is_inclusive():
    conn = tiny(bank=[("B1", "A", "2026-09-14", "ACH", -12345)],
                gl=[("G1", "A", "2026-09-10", "Expense", "", "Acme", "", -12345)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == {(("B1",), ("G1",))}


def test_other_account_is_never_a_candidate():
    conn = tiny(bank=[("B1", "A", "2026-09-10", "ACH", 12345)],
                gl=[("G1", "D", "2026-09-10", "Payment", "", "Acme", "", 12345)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == set()


def test_opposite_sign_is_never_a_candidate():
    conn = tiny(bank=[("B1", "D", "2026-09-10", "RETURNED ITEM", -12345)],
                gl=[("G1", "D", "2026-09-10", "Deposit", "", "Acme", "", 12345)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == set()


def test_check_clears_on_number_and_amount_however_long_it_took():
    conn = tiny(bank=[("B1", "A", "2026-09-28", "CHECK 1042", -9900, "1042")],
                gl=[("G1", "A", "2026-08-20", "Check", "1042", "Acme", "", -9900, 1)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == {(("B1",), ("G1",))}


def test_check_number_match_with_a_different_amount_goes_to_review_not_cleared():
    conn = tiny(bank=[("B1", "A", "2026-09-15", "CHECK 1050", -185400, "1050")],
                gl=[("G1", "A", "2026-09-10", "Check", "1050", "Acme", "", -184500)])
    rules.run(conn, "2026-09")
    (group,) = db.groups(conn)
    assert (group["status"], group["reason_code"]) == ("review", "amount_mismatch")
    assert (group["bank_ids"], group["gl_ids"]) == (["B1"], ["G1"])


def test_ledger_check_is_never_matched_to_a_bank_line_that_is_not_that_check():
    conn = tiny(bank=[("B1", "A", "2026-09-27", "ACH DEBIT NTWK SVC", -20000)],
                gl=[("G1", "A", "2026-09-25", "Check", "1061", "Acme", "", -20000)])
    rules.run(conn, "2026-09")
    assert cleared(conn) == set()


def test_running_the_rules_twice_changes_nothing():
    conn = tiny(bank=[("B1", "A", "2026-09-10", "ACH DEBIT ACME", -12345)],
                gl=[("G1", "A", "2026-09-08", "Bill Payment", "", "Acme", "", -12345)])
    rules.run(conn, "2026-09")
    rules.run(conn, "2026-09")
    assert len(db.groups(conn)) == 1


@pytest.mark.parametrize("ym", MONTHS)
def test_on_a_full_month_every_rule_clear_is_right(ym):
    key = json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())
    conn = ingest.load_month(ym)
    rules.run(conn, ym)
    true_matches = {(tuple(g["bank_ids"]), tuple(g["gl_ids"])) for g in key["groups"]
                    if g["expected"] == "match"}
    assert cleared(conn) <= true_matches
    assert len(cleared(conn)) >= 35


@pytest.mark.parametrize("ym", MONTHS)
def test_on_a_full_month_the_only_easy_items_left_are_outside_the_date_window(ym):
    """A two-business-day posting across a holiday weekend is five calendar days.

    The rule's window is four. Those items are not cleared by rule; they fall to
    the next stage. That is the rule being conservative, not the data being wrong.
    """
    key = json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())
    conn = ingest.load_month(ym)
    rules.run(conn, ym)
    easy = {(tuple(g["bank_ids"]), tuple(g["gl_ids"])) for g in key["groups"]
            if g["category"] in RULE_CLEARABLE}
    for (bank_id,), (gl_id,) in easy - cleared(conn):
        gap = conn.execute("SELECT julianday(b.posted_date) - julianday(g.entry_date) FROM bank_txn b, "
                           "gl_entry g WHERE b.id = ? AND g.id = ?", (bank_id, gl_id)).fetchone()[0]
        assert gap > rules.WINDOW_DAYS


@pytest.mark.parametrize("ym", MONTHS)
def test_on_a_full_month_the_transposed_check_goes_to_review(ym):
    key = json.loads((synth.ROOT / "data" / "answer_key" / f"{ym}.json").read_text())
    conn = ingest.load_month(ym)
    rules.run(conn, ym)
    review = [g for g in db.groups(conn) if g["status"] == "review"]
    (transposed,) = [g for g in key["groups"] if g["category"] == "transposed_check"]
    assert [(g["bank_ids"], g["gl_ids"]) for g in review] == [(transposed["bank_ids"], transposed["gl_ids"])]

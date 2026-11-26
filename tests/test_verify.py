"""The guard between the model and the books. It trusts nothing a proposal says."""
from recon import db, verify
from tests.helpers import tiny


def ledger():
    return tiny(
        bank=[("B1", "D", "2026-09-10", "CARD PROCESSOR PAYOUT ST-1", 29000),
              ("B2", "A", "2026-09-27", "ACH DEBIT NTWK SVC BILLING 5", -20000),
              ("B3", "A", "2026-09-20", "CHECK 1050", -5000, "1050")],
        gl=[("G1", "D", "2026-09-08", "Sales Receipt", "SR-1", "Walk-in customer", "Card sale", 10000),
            ("G2", "D", "2026-09-09", "Sales Receipt", "SR-2", "Brandt Mechanical Inc.", "Card sale", 20000),
            ("G3", "D", "2026-09-10", "Expense", "", "Card processor", "Processing fees", -1000),
            ("G4", "A", "2026-09-25", "Check", "1061", "Acme", "", -20000),
            ("G5", "A", "2026-09-18", "Check", "1050", "Apex", "", -5000),
            ("G6", "D", "2026-07-01", "Sales Receipt", "SR-0", "Old sale", "Card sale", 29000)])


def proposal(**over):
    base = {"decision": "match", "bank_ids": ["B1"], "gl_ids": ["G1", "G2", "G3"], "evidence": []}
    return {**base, **over}


def codes(conn, p):
    return [v.code for v in verify.check(conn, p)]


def test_group_that_ties_to_the_cent_passes():
    assert codes(ledger(), proposal()) == []


def test_ledger_id_that_does_not_exist_is_caught():
    assert "unknown_id" in codes(ledger(), proposal(gl_ids=["G1", "G2", "G3", "G99"]))


def test_bank_id_that_does_not_exist_is_caught():
    assert "unknown_id" in codes(ledger(), proposal(bank_ids=["B77"]))


def test_group_that_is_off_by_a_cent_is_caught_and_the_difference_reported():
    (v,) = verify.check(ledger(), proposal(gl_ids=["G1", "G2"]))
    assert v.code == "does_not_tie"
    assert "10.00" in v.detail


def test_entry_already_cleared_elsewhere_cannot_be_used_again():
    conn = ledger()
    db.add_group(conn, "2026-09", "cleared", "rule", ["B1"], ["G1", "G2", "G3"])
    assert "already_matched" in codes(conn, proposal())


def test_quoted_amount_that_differs_from_the_stored_amount_is_caught():
    p = proposal(evidence=[{"txn_id": "G2", "field": "amount", "value": "250.00"}])
    assert codes(ledger(), p) == ["evidence_mismatch"]


def test_quoted_amount_equal_to_the_stored_amount_passes_in_any_usual_format():
    for value in ("200.00", "$200.00", "200"):
        p = proposal(evidence=[{"txn_id": "G2", "field": "amount", "value": value}])
        assert codes(ledger(), p) == [], value


def test_quoted_text_must_appear_in_the_stored_field():
    good = proposal(evidence=[{"txn_id": "G2", "field": "name", "value": "Brandt Mechanical"}])
    bad = proposal(evidence=[{"txn_id": "G2", "field": "name", "value": "Brandt Mechanical Supply"}])
    assert codes(ledger(), good) == []
    assert codes(ledger(), bad) == ["evidence_mismatch"]


def test_evidence_about_a_transaction_that_does_not_exist_is_caught():
    p = proposal(evidence=[{"txn_id": "G404", "field": "amount", "value": "290.00"}])
    assert codes(ledger(), p) == ["unknown_id"]


def test_evidence_naming_a_field_that_does_not_exist_is_caught():
    p = proposal(evidence=[{"txn_id": "G2", "field": "invoice_total", "value": "200.00"}])
    assert codes(ledger(), p) == ["unknown_field"]


def test_a_ledger_only_field_cited_on_a_bank_line_is_a_wrong_field_not_a_wrong_value():
    """The text may be real; the record simply has no field of that name to check it against."""
    p = proposal(evidence=[{"txn_id": "B1", "field": "txn_type", "value": "CARD PROCESSOR PAYOUT"}])
    assert codes(ledger(), p) == ["unknown_field"]


def test_bank_and_ledger_in_different_accounts_cannot_be_matched():
    p = proposal(bank_ids=["B2"], gl_ids=["G1", "G2", "G3"])
    assert "account_mismatch" in codes(ledger(), p)


def test_ledger_check_cannot_be_matched_to_a_bank_line_that_is_not_that_check():
    assert codes(ledger(), proposal(bank_ids=["B2"], gl_ids=["G4"])) == ["check_number_mismatch"]


def test_ledger_check_matched_to_its_own_check_number_passes():
    assert codes(ledger(), proposal(bank_ids=["B3"], gl_ids=["G5"])) == []


def test_entries_more_than_45_days_from_the_bank_date_are_out_of_policy():
    assert codes(ledger(), proposal(gl_ids=["G6"])) == ["date_out_of_policy"]


def test_match_needs_both_sides():
    assert codes(ledger(), proposal(gl_ids=[])) == ["malformed"]


def test_no_match_must_not_name_ledger_entries():
    assert codes(ledger(), proposal(decision="no_match", gl_ids=["G1"])) == ["malformed"]


def test_no_match_on_a_real_bank_line_passes():
    assert codes(ledger(), proposal(decision="no_match", gl_ids=[])) == []


def test_unknown_decision_is_malformed():
    assert codes(ledger(), proposal(decision="probably")) == ["malformed"]


def test_quoting_an_empty_field_as_empty_is_an_accurate_citation():
    """'This bank line has no check number' is a true statement about the record."""
    p = proposal(decision="no_match", bank_ids=["B2"], gl_ids=[],
                 evidence=[{"txn_id": "B2", "field": "check_no", "value": ""}])
    assert codes(ledger(), p) == []


def test_quoting_nothing_for_a_field_that_has_a_value_is_still_a_mismatch():
    p = proposal(evidence=[{"txn_id": "G2", "field": "name", "value": ""}])
    assert codes(ledger(), p) == ["evidence_mismatch"]

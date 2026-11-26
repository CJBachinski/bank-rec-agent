"""The synthetic months are only useful if their answer key is true.

These tests check the key against the data independently of how the generator
built it: every line is accounted for, and each account ties out to the cent.
"""
from itertools import combinations

import pytest

from recon import synth

MONTHS = ["2026-09", "2026-10"]


@pytest.fixture(scope="module", params=MONTHS)
def month(request):
    return synth.build_month(request.param)


def _groups(month, expected=None):
    return [g for g in month.key["groups"] if expected is None or g["expected"] == expected]


def test_month_has_more_than_fifty_bank_lines(month):
    assert len(month.bank_a) + len(month.bank_d) >= 60


def test_ids_are_unique(month):
    ids = [x.id for x in month.bank_a + month.bank_d + month.gl + month.prior]
    assert len(ids) == len(set(ids))
    assert all(ids)


def test_every_bank_line_is_in_exactly_one_group(month):
    seen = [b for g in _groups(month) for b in g["bank_ids"]]
    assert sorted(seen) == sorted(x.id for x in month.bank_a + month.bank_d)


def test_every_ledger_entry_is_in_exactly_one_group(month):
    seen = [x for g in _groups(month) for x in g["gl_ids"] + g.get("duplicate_gl_ids", [])]
    assert sorted(seen) == sorted(x.id for x in month.gl + month.prior)


def test_matched_groups_tie_to_the_cent(month):
    bank = {x.id: x.amount for x in month.bank_a + month.bank_d}
    gl = {x.id: x.amount for x in month.gl + month.prior}
    for g in _groups(month, "match"):
        assert sum(bank[b] for b in g["bank_ids"]) == sum(gl[x] for x in g["gl_ids"]), g


def test_review_groups_do_not_tie(month):
    """A group expected to go to a person is one a careless matcher would clear."""
    bank = {x.id: x.amount for x in month.bank_a + month.bank_d}
    gl = {x.id: x.amount for x in month.gl + month.prior}
    review = _groups(month, "review")
    assert review
    for g in review:
        assert sum(bank[b] for b in g["bank_ids"]) != sum(gl[x] for x in g["gl_ids"]), g


@pytest.mark.parametrize("account", ["A", "D"])
def test_account_ties_out(month, account):
    """Adjusted bank balance equals adjusted book balance, computed from the lines."""
    bank_lines = [x for x in month.bank_a + month.bank_d if x.account == account]
    gl_lines = [x for x in month.gl if x.account == account]
    prior = [x for x in month.prior if x.account == account]
    bal = month.key["accounts"][account]
    by_bank = {x.id: x for x in bank_lines}
    by_gl = {x.id: x for x in gl_lines + prior}

    bank_end = bal["bank_open"] + sum(x.amount for x in bank_lines)
    book_end = bal["book_open"] + sum(x.amount for x in gl_lines)
    assert bank_end == bal["bank_end"]
    assert book_end == bal["book_end"]

    # Ledger items the bank has not seen yet (outstanding checks, deposits in transit).
    not_at_bank = sum(by_gl[x].amount for g in _groups(month, "gl_only")
                      for x in g["gl_ids"] if x in by_gl)
    adjusted_bank = bank_end + not_at_bank

    # Bank items and errors the books have not recorded yet.
    adjustment = 0
    for g in _groups(month):
        if not any(b in by_bank for b in g["bank_ids"]) and not any(x in by_gl for x in g["gl_ids"]):
            continue
        if g["expected"] == "bank_only":
            adjustment += sum(by_bank[b].amount for b in g["bank_ids"])
        elif g["expected"] == "review":
            adjustment += (sum(by_bank[b].amount for b in g["bank_ids"])
                           - sum(by_gl[x].amount for x in g["gl_ids"]))
        for x in g.get("duplicate_gl_ids", []):
            adjustment -= by_gl[x].amount
    assert adjusted_bank == book_end + adjustment


@pytest.mark.parametrize("account", ["A", "D"])
def test_bank_balance_never_goes_negative(month, account):
    lines = sorted((x for x in month.bank_a + month.bank_d if x.account == account),
                   key=lambda x: (x.date, x.seq))
    running = month.key["accounts"][account]["bank_open"]
    for x in lines:
        running += x.amount
        assert running >= 0, x


def test_bank_dates_fall_inside_the_month(month):
    for x in month.bank_a + month.bank_d:
        assert x.date.strftime("%Y-%m") == month.key["month"], x
        assert x.date.weekday() < 5, x
        assert x.date not in synth.HOLIDAYS, x


def test_check_numbers_rise_with_check_dates(month):
    checks = sorted((g for g in month.gl if g.txn_type == "Check"), key=lambda g: int(g.num))
    assert [g.date for g in checks] == sorted(g.date for g in checks)


def test_payout_with_unbooked_fee_has_no_ledger_subset_that_ties(month):
    """Otherwise a wrong match would pass every arithmetic check."""
    bank = {x.id: x for x in month.bank_d}
    used = {x for g in _groups(month, "match") for x in g["gl_ids"]}
    (g,) = [g for g in _groups(month, "review") if g["category"] == "card_payout"]
    b = bank[g["bank_ids"][0]]
    pool = [x for x in month.gl if x.account == "D" and x.id not in used
            and abs((x.date - b.date).days) <= 10]
    assert pool
    assert not [c for n in range(1, 8) for c in combinations(pool, n)
                if sum(x.amount for x in c) == b.amount]


def test_each_card_payout_has_one_ledger_subset_that_ties(month):
    """If two subsets tied, the answer key would be an opinion, not a fact."""
    gl = {x.id: x for x in month.gl}
    bank = {x.id: x for x in month.bank_d}
    payouts = [g for g in _groups(month, "match") if g["category"] == "card_payout"]
    assert len(payouts) >= 3
    used_elsewhere = {x for g in _groups(month, "match") if g["category"] != "card_payout"
                      for x in g["gl_ids"]}
    for g in payouts:
        b = bank[g["bank_ids"][0]]
        pool = [x for x in month.gl if x.account == "D" and x.id not in used_elsewhere
                and abs((x.date - b.date).days) <= 7]
        ties = [c for n in range(1, 8) for c in combinations(pool, n)
                if sum(x.amount for x in c) == b.amount]
        assert [sorted(x.id for x in c) for c in ties] == [sorted(g["gl_ids"])]


def test_same_inputs_regenerate_identical_files(month):
    again = synth.build_month(month.key["month"])
    assert synth.render(again) == synth.render(month)


def test_committed_files_match_the_generator(month):
    for path, text in synth.render(month).items():
        on_disk = (synth.ROOT / path).read_text(encoding="utf-8")
        assert on_disk == text, path


def test_the_two_months_are_different_data():
    sept, octo = synth.build_month("2026-09"), synth.build_month("2026-10")
    sept_amounts = {x.amount for x in sept.bank_a + sept.bank_d}
    oct_amounts = {x.amount for x in octo.bank_a + octo.bank_d}
    assert len(sept_amounts & oct_amounts) < 8

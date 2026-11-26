"""Loading the client's files. The loader never sees the answer key."""
import shutil

import pytest

from recon import ingest, synth

MONTHS = ["2026-09", "2026-10"]


@pytest.fixture(scope="module", params=MONTHS)
def loaded(request):
    return synth.build_month(request.param), ingest.load_month(request.param)


@pytest.mark.parametrize("text, cents", [
    ("1,250.00", 125000), ("-35.00", -3500), ("(35.00)", -3500), ("$1,250.00", 125000),
    ("0.07", 7), ("", 0), ("  18.42 ", 1842), ("-$4,850.00", -485000),
])
def test_parse_money(text, cents):
    assert ingest.parse_money(text) == cents


def test_bank_lines_load_exactly_as_generated(loaded):
    month, conn = loaded
    got = conn.execute("SELECT id, account, posted_date, description, amount_cents, check_no "
                       "FROM bank_txn ORDER BY id").fetchall()
    want = sorted((b.id, b.account, b.date.isoformat(), b.description, b.amount, b.check_no)
                  for b in month.bank_a + month.bank_d)
    assert [tuple(r) for r in got] == want


def test_ledger_entries_load_exactly_as_generated(loaded):
    month, conn = loaded
    got = conn.execute("SELECT id, account, entry_date, txn_type, num, name, memo, amount_cents, "
                       "prior_period FROM gl_entry ORDER BY id").fetchall()
    want = sorted((g.id, g.account, g.date.isoformat(), g.txn_type, g.num, g.name, g.memo,
                   g.amount, int(g.prior)) for g in month.gl + month.prior)
    assert [tuple(r) for r in got] == want


def test_report_furniture_is_not_loaded_as_transactions(loaded):
    _, conn = loaded
    junk = conn.execute("SELECT COUNT(*) FROM gl_entry WHERE txn_type = '' OR entry_date = '' "
                        "OR name LIKE 'Total for%' OR memo LIKE '%Beginning Balance%'").fetchone()[0]
    assert junk == 0


def test_balances_come_from_the_files(loaded):
    month, conn = loaded
    for acct, want in month.key["accounts"].items():
        row = conn.execute("SELECT book_open, book_end, bank_open, bank_end FROM account_balance "
                           "WHERE account = ?", (acct,)).fetchone()
        assert tuple(row) == (want["book_open"], want["book_end"], want["bank_open"], want["bank_end"])


def _copy_month(tmp_path, ym="2026-09"):
    dest = tmp_path / ym
    shutil.copytree(synth.ROOT / "data" / "raw" / ym, dest)
    return dest


def test_bank_file_whose_balance_column_does_not_add_up_is_refused(tmp_path):
    raw = _copy_month(tmp_path)
    path = raw / "bank_deposit_9052.csv"
    lines = path.read_text().splitlines()
    cells = lines[3].split(",")
    lines[3] = lines[3].replace(cells[2], cells[2] + " X", 1).replace('"', '"9', 1)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ingest.IngestError, match="balance"):
        ingest.load_month("2026-09", raw_dir=raw)


def test_ledger_whose_total_row_disagrees_with_its_lines_is_refused(tmp_path):
    raw = _copy_month(tmp_path)
    path = raw / "general_ledger_cash.csv"
    lines = path.read_text().splitlines()
    idx = next(i for i, line in enumerate(lines) if line.startswith("Total for 1000"))
    del lines[idx - 1]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ingest.IngestError, match="total"):
        ingest.load_month("2026-09", raw_dir=raw)

"""Load one month of client files into SQLite.

Three inputs, three shapes:
  - the operating account export: newest first, separate Debit and Credit columns,
    US dates, check numbers buried in the description;
  - the deposit account export: oldest first, one signed Amount column, ISO dates;
  - a general ledger detail report, with its title block, section headers,
    beginning balances and total rows still in it;
plus last month's list of outstanding checks.

The loader checks each file against itself before trusting it: a bank export
whose balance column does not follow from its amounts, or a ledger section whose
total row disagrees with its lines, is refused. It never reads the answer key.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from recon import db

ROOT = Path(__file__).resolve().parent.parent
BANK_FILES = {"A": "bank_operating_4417.csv", "D": "bank_deposit_9052.csv"}
LEDGER_ACCOUNTS = {"1000 Operating Checking": "A", "1010 Deposit Account": "D"}
LEDGER_FILE = "general_ledger_cash.csv"
PRIOR_FILE = "outstanding_items_prior.csv"
PRIOR_ACCOUNT = "A"          # checks are only written on the operating account
CHECK_RE = re.compile(r"^CHECK\s+#?(\d+)$")


class IngestError(ValueError):
    pass


def parse_money(text: str) -> int:
    """'1,250.00', '-35.00', '(35.00)', '$1,250.00' -> integer cents. Blank is zero."""
    s = (text or "").strip().replace("$", "").replace(",", "")
    if not s:
        return 0
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        cents = int((Decimal(s) * 100).to_integral_exact())
    except (InvalidOperation, ValueError) as exc:
        raise IngestError(f"not an amount: {text!r}") from exc
    return -cents if negative else cents


def _date(text: str) -> str:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise IngestError(f"not a date: {text!r}")


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _check_balances(path: Path, chronological: list[tuple[int, int]]) -> tuple[int, int]:
    """(amount, balance after) oldest first. Returns (opening, ending) balance."""
    opening = chronological[0][1] - chronological[0][0]
    running = opening
    for n, (amount, balance) in enumerate(chronological, 1):
        running += amount
        if running != balance:
            raise IngestError(f"{path.name}: balance column does not follow from the amounts "
                              f"at transaction {n} (expected {running / 100:,.2f}, file says {balance / 100:,.2f})")
    return opening, running


def _load_operating(conn, month: str, path: Path) -> tuple[int, int]:
    mm = month[-2:]
    parsed = []
    for i, r in enumerate(_rows(path), 1):
        description = r["Description"].strip()
        amount = parse_money(r["Credit"]) - parse_money(r["Debit"])
        check = CHECK_RE.match(description)
        conn.execute("INSERT INTO bank_txn VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"A-{mm}-{i:03d}", month, "A", _date(r["Date"]), description, amount,
                      check.group(1) if check else "", path.name, i))
        parsed.append((amount, parse_money(r["Balance"])))
    return _check_balances(path, parsed[::-1])          # the export is newest first


def _load_deposit(conn, month: str, path: Path) -> tuple[int, int]:
    mm = month[-2:]
    parsed = []
    for i, r in enumerate(_rows(path), 1):
        amount = parse_money(r["Amount"])
        conn.execute("INSERT INTO bank_txn VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"D-{mm}-{i:03d}", month, "D", _date(r["Posted Date"]), r["Memo"].strip(), amount,
                      r["Check Number"].strip(), path.name, i))
        parsed.append((amount, parse_money(r["Running Balance"])))
    return _check_balances(path, parsed)


def _load_ledger(conn, month: str, path: Path) -> dict[str, tuple[int, int]]:
    mm = month[-2:]
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    try:
        start = next(i for i, r in enumerate(rows) if r and r[0] == "Date")
    except StopIteration:
        raise IngestError(f"{path.name}: no header row") from None
    balances: dict[str, tuple[int, int]] = {}
    account, opening, total, n = None, 0, 0, 0
    for row_no, r in enumerate(rows[start + 1:], start + 2):
        if not r or not r[0]:
            continue
        first = r[0].strip()
        if first in LEDGER_ACCOUNTS:
            account, opening, total = LEDGER_ACCOUNTS[first], 0, 0
        elif first == "Beginning Balance":
            opening = parse_money(r[7])
        elif first.startswith("Total for"):
            stated = parse_money(r[6])
            if stated != total:
                raise IngestError(f"{path.name}: total row for {first[10:]} says {stated / 100:,.2f} "
                                  f"but its lines add to {total / 100:,.2f}")
            balances[account] = (opening, opening + total)
            account = None
        elif account and re.match(r"\d\d/\d\d/\d{4}$", first):
            n += 1
            amount = parse_money(r[6])
            total += amount
            conn.execute("INSERT INTO gl_entry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"G-{mm}-{n:03d}", month, account, _date(first), r[1].strip(), r[2].strip(),
                          r[3].strip(), r[4].strip(), amount, 0, path.name, row_no))
    if set(balances) != set(LEDGER_ACCOUNTS.values()):
        raise IngestError(f"{path.name}: expected sections for {sorted(LEDGER_ACCOUNTS)}")
    return balances


def _load_prior(conn, month: str, path: Path) -> int:
    """Last month's outstanding checks. Returns their total, as the books signed them."""
    mm, total = month[-2:], 0
    for i, r in enumerate(_rows(path), 1):
        amount = -parse_money(r["Amount"])
        total += amount
        conn.execute("INSERT INTO gl_entry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"P-{mm}-{i:02d}", month, PRIOR_ACCOUNT, _date(r["Check Date"]), "Check",
                      r["Check No"].strip(), r["Payee"].strip(), "", amount, 1, path.name, i))
    return total


def load_month(month: str, raw_dir: Path | None = None, conn=None):
    raw = Path(raw_dir) if raw_dir else ROOT / "data" / "raw" / month
    conn = conn or db.connect()
    with conn:
        bank = {"A": _load_operating(conn, month, raw / BANK_FILES["A"]),
                "D": _load_deposit(conn, month, raw / BANK_FILES["D"])}
        book = _load_ledger(conn, month, raw / LEDGER_FILE)
        _load_prior(conn, month, raw / PRIOR_FILE)
        names = {v: k for k, v in LEDGER_ACCOUNTS.items()}
        for acct in ("A", "D"):
            conn.execute("INSERT INTO account_balance VALUES (?,?,?,?,?,?,?)",
                         (month, acct, names[acct], *book[acct], *bank[acct]))
    return conn

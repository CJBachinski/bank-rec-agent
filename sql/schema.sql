-- Everything the reconciliation knows, in one SQLite file.
-- Money is integer cents. Bank amounts are signed from the bank's side (deposits
-- positive); ledger amounts from the cash account's side (debits positive), so a
-- matched group sums to the same number on both sides.

CREATE TABLE bank_txn (
    id            TEXT PRIMARY KEY,          -- A-09-001: account, month, row in the export
    month         TEXT NOT NULL,
    account       TEXT NOT NULL,
    posted_date   TEXT NOT NULL,             -- ISO date
    description   TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    check_no      TEXT NOT NULL DEFAULT '',
    source_file   TEXT NOT NULL,
    source_row    INTEGER NOT NULL
);

CREATE TABLE gl_entry (
    id            TEXT PRIMARY KEY,          -- G-09-001, or P-09-01 for a check carried forward
    month         TEXT NOT NULL,
    account       TEXT NOT NULL,
    entry_date    TEXT NOT NULL,
    txn_type      TEXT NOT NULL,
    num           TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    memo          TEXT NOT NULL DEFAULT '',
    amount_cents  INTEGER NOT NULL,
    prior_period  INTEGER NOT NULL DEFAULT 0, -- 1 = outstanding at the last reconciliation
    source_file   TEXT NOT NULL,
    source_row    INTEGER NOT NULL
);

CREATE TABLE account_balance (
    month TEXT NOT NULL, account TEXT NOT NULL, ledger_account TEXT NOT NULL,
    book_open INTEGER NOT NULL, book_end INTEGER NOT NULL,
    bank_open INTEGER NOT NULL, bank_end INTEGER NOT NULL,
    PRIMARY KEY (month, account)
);

-- A decision about one or more lines. cleared = bank and books agree.
-- bank_only = the bank has it, the books need an entry. review = a person decides.
CREATE TABLE match_group (
    id          INTEGER PRIMARY KEY,
    month       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('cleared', 'bank_only', 'review')),
    source      TEXT NOT NULL CHECK (source IN ('rule', 'agent', 'person')),
    rule        TEXT NOT NULL DEFAULT '',
    reason_code TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    confidence  REAL
);

CREATE TABLE match_line (
    group_id INTEGER NOT NULL REFERENCES match_group (id),
    side     TEXT NOT NULL CHECK (side IN ('bank', 'gl')),
    txn_id   TEXT NOT NULL,
    PRIMARY KEY (side, txn_id)               -- a line belongs to at most one decision
);

CREATE VIEW bank_status AS
SELECT b.*, g.id AS group_id, COALESCE(g.status, 'open') AS status,
       COALESCE(g.reason_code, '') AS reason_code
FROM bank_txn b
LEFT JOIN match_line l ON l.side = 'bank' AND l.txn_id = b.id
LEFT JOIN match_group g ON g.id = l.group_id;

CREATE VIEW gl_status AS
SELECT e.*, g.id AS group_id, COALESCE(g.status, 'open') AS status,
       COALESCE(g.reason_code, '') AS reason_code
FROM gl_entry e
LEFT JOIN match_line l ON l.side = 'gl' AND l.txn_id = e.id
LEFT JOIN match_group g ON g.id = l.group_id;

-- The reconciliation in one row per account. If every cleared group ties, the two
-- adjusted balances are equal by arithmetic; what matters is whether each
-- reconciling item behind them has an explanation.
CREATE VIEW tie_out AS
SELECT a.month, a.account, a.ledger_account, a.bank_end, a.book_end,
       (SELECT COALESCE(SUM(amount_cents), 0) FROM gl_status s
         WHERE s.month = a.month AND s.account = a.account AND s.status = 'open')      AS ledger_not_at_bank,
       (SELECT COALESCE(SUM(amount_cents), 0) FROM bank_status s
         WHERE s.month = a.month AND s.account = a.account AND s.status = 'bank_only') AS bank_not_in_books,
       (SELECT COALESCE(SUM(amount_cents), 0) FROM bank_status s
         WHERE s.month = a.month AND s.account = a.account AND s.status = 'open')      AS unexplained_bank,
       (SELECT COALESCE(SUM(amount_cents), 0) FROM bank_status s
         WHERE s.month = a.month AND s.account = a.account AND s.status = 'review')
     - (SELECT COALESCE(SUM(amount_cents), 0) FROM gl_status s
         WHERE s.month = a.month AND s.account = a.account AND s.status = 'review')    AS pending_decision
FROM account_balance a;

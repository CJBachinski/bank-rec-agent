"""Synthetic months for a fictional company, with an answer key that is true by construction.

Nothing here is real. The company, its vendors, customers and bank accounts are
invented. What is realistic is the shape of the problem: two bank exports in
different formats, a ledger export with section headers and totals in the way,
names that do not agree between bank and books, and the usual month-end mess.

Each month is built from economic events. An event writes its bank lines, its
ledger lines and its own entry in the answer key at the same moment, so the key
cannot drift from the data. tests/test_synth.py re-derives the tie-out from the
lines alone and fails if the key is wrong.

All money is integer cents. Bank amounts are signed from the bank's side
(deposits positive); ledger amounts are signed from the cash account's side
(debits positive). A matched group therefore sums to the same number on both sides.
"""
from __future__ import annotations

import csv
import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANY = "Tallow Creek Supply Co."
GL_ACCOUNT = {"A": "1000 Operating Checking", "D": "1010 Deposit Account"}
BANK_FILE = {"A": "bank_operating_4417.csv", "D": "bank_deposit_9052.csv"}
# Labor Day and Columbus Day 2026. Banks post nothing on them.
HOLIDAYS = {date(2026, 9, 7), date(2026, 10, 12)}

# (name as the books have it, name as the bank prints it)
VENDORS = [
    ("Allied Mutual Insurance Co.", "ALLIED MUT INS"),
    ("Lehigh Gas & Electric", "LEHIGH G&E UTIL"),
    ("Tri-County Waste Services", "TRI CNTY WASTE SVC"),
    ("Northfield Fasteners Inc.", "NORTHFIELD FASTENER"),
    ("Delaware Valley Steel Supply", "DEL VALLEY STEEL SUP"),
    ("Pinnacle Freight Lines", "PINNACLE FREIGHT LN"),
    ("Summit Packaging Corp", "SUMMIT PACKAGING"),
    ("Ironbound Tool & Die", "IRONBOUND TOOL DIE"),
    ("Blue Mountain Telecom", "BLUE MTN TELECOM"),
    ("Hartwell Office Products", "HARTWELL OFFICE PROD"),
    ("Cedar Run Landscaping", "CEDAR RUN LANDSCAPE"),
    ("Orion Software Subscriptions", "ORION SOFTWARE SUBSCR"),
    ("Valley Forge Uniform Co.", "VLY FORGE UNIFORM"),
    ("Marlowe & Dunn CPAs", "MARLOWE DUNN CPA"),
    ("Eastgate Equipment Leasing", "EASTGATE EQUIP LEASE"),
    ("Stonebridge Fleet Fuel", "STONEBRIDGE FLEET FUEL"),
    ("Quarry Road Auto Repair", "QUARRY RD AUTO"),
    ("Penn Corrugated Box", "PENN CORRUGATED"),
    ("Greenline Safety Supply", "GREENLINE SAFETY"),
    ("Harborview Staffing, LLC", "HARBORVIEW STAFFING"),
    ("Atlas Pallet & Crate", "ATLAS PALLET CRATE"),
    ("Riverbend Water Authority", "RIVERBEND WATER AUTH"),
    ("Coldspring Janitorial", "COLDSPRING JANITORIAL"),
    ("Milestone Abrasives Inc.", "MILESTONE ABRASIVES"),
    ("Foxglove Print Shop", "FOXGLOVE PRINT"),
    ("Sentry Alarm Monitoring", "SENTRY ALARM MONITOR"),
    ("Baseline Calibration Lab", "BASELINE CALIB LAB"),
    ("Trestle Bridge Welding Supply", "TRESTLE BRDG WELD SUP"),
    ("Corbin & Vale Attorneys", "CORBIN VALE ATTY"),
    ("Windgap Propane", "WINDGAP PROPANE"),
    ("Upland Hydraulics", "UPLAND HYDRAULICS"),
    ("Ferncliff Courier", "FERNCLIFF COURIER"),
]
RENT = ("Keystone Property Management, LLC", "KEYSTONE PROP MGMT")

CUSTOMERS = [
    ("Brandt Mechanical Inc.", "BRANDT MECHANICAL INC"),
    ("Sullivan Fabrication", "SULLIVAN FABRICATION"),
    ("Aldridge Construction Co.", "ALDRIDGE CONSTR CO"),
    ("Mercer County Schools", "MERCER CO SCHOOLS AP"),
    ("Holloway Plumbing & Heating", "HOLLOWAY PLBG HTG"),
    ("Tidewater Marine Services", "TIDEWATER MARINE SVC"),
    ("Paxton Electric, LLC", "PAXTON ELECTRIC LLC"),
    ("Greer Brothers Roofing", "GREER BROS ROOFING"),
    ("Linden Hill Builders", "LINDEN HILL BUILDERS"),
    ("Whitlock HVAC", "WHITLOCK HVAC"),
    ("Corvid Automation", "CORVID AUTOMATION"),
    ("Dunmore Township", "DUNMORE TWP VENDOR PMT"),
    ("Rowan & Pike Contractors", "ROWAN PIKE CONTR"),
    ("Ashby Machine Works", "ASHBY MACHINE WKS"),
    ("Fairmount Restoration", "FAIRMOUNT RESTORATION"),
    ("Kessler Sheet Metal", "KESSLER SHEET METAL"),
    ("Old Mill Cabinetry", "OLD MILL CABINETRY"),
    ("Sorrel Ridge Farms", "SORREL RIDGE FARMS"),
]

# Card purchases: the bank prints the merchant's trade name, the books use the legal name.
DBA = [
    ("Heron Coffee Roasters LLC", "SQ *BLUE HERON CAFE", "Client meeting - coffee"),
    ("R. Okafor Consulting", "PAYPAL *ROKAFOR", "Safety training session"),
    ("Midstate Industrial Supply", "MSI*ONLINE ORDER", "Shop consumables - web order"),
    ("Lantern Hospitality Group", "TST* THE COPPER KETTLE", "Team lunch"),
    ("Wexford Print & Sign", "WXFD PRNT SGN", "Trade show banner"),
    ("Apex Calibration Services", "ACS LAB SVCS WEB PMT", "Gauge calibration"),
]

SPLIT = {
    "Bill Payment": "2000 Accounts Payable", "Check": "2000 Accounts Payable",
    "Journal Entry": "-Split-", "Payment": "1200 Accounts Receivable",
    "Sales Receipt": "4000 Sales", "Deposit": "1499 Undeposited Funds",
    "Expense": "6990 Uncategorized Expense", "Transfer": "Transfer",
}

SPECS = {
    "2026-09": dict(
        seed=20260930, open_a=162_480_13, open_d=58_300_42, check_start=1041,
        prior_checks=[(1019, 18, 1_264_50), (1027, 24, 389_00), (1033, 27, 2_150_75)],
        rent=4_850_00, lookalike_vendor=2_400_00, lookalike_customer=3_150_00,
        transposed=(1_845_00, 1_854_00), transfer=20_000_00, wire_fee=25_00,
        maintenance_fee=35_00, interest=18_42, batch_missing_fee=2, dba=[0, 1, 2],
        batch_starts=[2, 6, 14, 18], pay_days=[15, 28], first_receipt=3301,
    ),
    "2026-10": dict(
        seed=20261031, open_a=171_905_60, open_d=49_771_08, check_start=1068,
        prior_checks=[(1049, 21, 742_10), (1055, 25, 3_318_00), (1061, 28, 96_45)],
        rent=4_850_00, lookalike_vendor=1_875_00, lookalike_customer=5_600_00,
        transposed=(2_376_40, 2_736_40), transfer=15_000_00, wire_fee=25_00,
        maintenance_fee=35_00, interest=21_07, batch_missing_fee=0, dba=[3, 4, 5],
        batch_starts=[5, 9, 19, 23], pay_days=[15, 28], first_receipt=3342,
    ),
}


@dataclass(eq=False)
class BankLine:
    account: str
    date: date
    description: str
    amount: int
    check_no: str = ""
    kind: str = ""
    seq: int = 0
    id: str = ""


@dataclass(eq=False)
class GLLine:
    account: str
    date: date
    txn_type: str
    num: str
    name: str
    memo: str
    amount: int
    prior: bool = False
    seq: int = 0
    id: str = ""


@dataclass
class Event:
    category: str
    expected: str          # match | bank_only | gl_only | review
    reason: str
    bank: list = field(default_factory=list)
    gl: list = field(default_factory=list)
    dup: list = field(default_factory=list)
    note: str = ""


@dataclass
class Month:
    ym: str
    bank_a: list
    bank_d: list
    gl: list
    prior: list
    key: dict


def is_bizday(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def add_bizdays(d: date, k: int) -> date:
    while k > 0:
        d += timedelta(days=1)
        if is_bizday(d):
            k -= 1
    return d


def to_weekday(d: date) -> date:
    """The next day the bank is open, or the day itself if it is."""
    while not is_bizday(d):
        d += timedelta(days=1)
    return d


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}{dollars:,}.{rem:02d}"


class _Builder:
    def __init__(self, ym: str):
        self.ym = ym
        self.spec = s = SPECS[ym]
        self.y, self.m = (int(x) for x in ym.split("-"))
        self.rng = random.Random(s["seed"])
        self.first = date(self.y, self.m, 1)
        self.last = date(self.y + (self.m == 12), self.m % 12 + 1, 1) - timedelta(days=1)
        self.events: list[Event] = []
        self.seq = 0
        self.check = s["check_start"]
        self.receipt = s["first_receipt"]
        self.vendors = self.rng.sample(VENDORS, len(VENDORS))
        self.customers = self.rng.sample(CUSTOMERS, len(CUSTOMERS))
        self.used = {s["rent"], s["lookalike_vendor"], s["lookalike_customer"], s["transfer"],
                     s["wire_fee"], s["maintenance_fee"], s["interest"], *s["transposed"],
                     *(c for _, _, c in s["prior_checks"])}

    # -- small helpers ---------------------------------------------------------
    def day(self, lo: int, hi: int) -> date:
        days = [date(self.y, self.m, d) for d in range(lo, hi + 1)]
        return self.rng.choice([d for d in days if is_bizday(d)])

    def cents(self, lo: int, hi: int) -> int:
        """A dollar amount nobody else in the month has, so a tie is never an accident."""
        while True:
            c = self.rng.randrange(lo * 100, hi * 100)
            if c not in self.used and c % 100:
                self.used.add(c)
                return c

    def ref(self, n: int = 6) -> str:
        return "".join(self.rng.choice("0123456789") for _ in range(n))

    def messy(self, name: str) -> str:
        """Books are typed by people: suffixes get dropped, caps lock gets left on."""
        r = self.rng.random()
        if r < 0.25:
            for suffix in (", LLC", " Inc.", " Co.", " Corp"):
                if name.endswith(suffix):
                    return name[: -len(suffix)]
        if r < 0.35:
            return name.upper()
        return name

    def bank(self, account, dt, description, amount, check_no="", kind="") -> BankLine:
        self.seq += 1
        return BankLine(account, dt, description, amount, check_no, kind, self.seq)

    def gl(self, account, dt, txn_type, num, name, memo, amount, prior=False) -> GLLine:
        self.seq += 1
        return GLLine(account, dt, txn_type, num, name, memo, amount, prior, self.seq)

    def add(self, category, expected, reason, bank=(), gl=(), dup=(), note=""):
        self.events.append(Event(category, expected, reason, list(bank), list(gl), list(dup), note))

    def ach_debit(self, descriptor: str) -> str:
        style = self.rng.choice(["ACH DEBIT {d} {r}", "PREAUTHORIZED DEBIT {d} ID:{r}",
                                 "ACH DEBIT {d} PAYMENT {r}"])
        return style.format(d=descriptor, r=self.ref())

    # -- events ----------------------------------------------------------------
    def vendor_payments(self):
        d = to_weekday(self.first)
        name, desc = RENT
        g = self.gl("A", d, "Bill Payment", "", name, f"Rent {d:%B %Y}", -self.spec["rent"])
        b = self.bank("A", d, f"ACH DEBIT {desc} RENT {self.ref()}", -self.spec["rent"])
        self.add("vendor_ach", "match", "matched", [b], [g])
        for _ in range(13):
            name, desc = self.vendors.pop()
            d = self.day(2, 24)
            amount = self.cents(180, 9500)
            memo = f"Inv {self.ref(5)}" if self.rng.random() > 0.2 else ""
            g = self.gl("A", d, "Bill Payment", "", self.messy(name), memo, -amount)
            b = self.bank("A", add_bizdays(d, self.rng.randint(0, 2)), self.ach_debit(desc), -amount)
            self.add("vendor_ach", "match", "matched", [b], [g])

    def checks(self):
        booked, paid = self.spec["transposed"]
        plan = [(self.day(1, 16), False) for _ in range(8)] + [(self.day(8, 14), True)]
        for d, transposed in sorted(plan, key=lambda p: p[0]):  # check numbers rise with date
            name, _ = self.vendors.pop()
            if transposed:
                # Written for one amount, keyed into the books as another. Same check number.
                g, b = self._check(name, d, booked, paid, 5)
                self.add("transposed_check", "review", "amount_mismatch", [b], [g],
                         note=f"Check cleared for {money(paid)}; books show {money(booked)}.")
            else:
                amount = self.cents(90, 4200)
                g, b = self._check(name, d, amount, amount, self.rng.randint(3, 9))
                self.add("check", "match", "matched", [b], [g])

    def _check(self, name, d, booked, paid, days_to_clear):
        number = str(self.check)
        self.check += 1
        g = self.gl("A", d, "Check", number, self.messy(name), "", -booked)
        b = self.bank("A", to_weekday(d + timedelta(days=days_to_clear)), f"CHECK {number}",
                      -paid, check_no=number)
        return g, b

    def outstanding_checks(self) -> int:
        """Written late in the month; the bank has not seen them. Returns one amount to reuse."""
        trap = 0
        for d in sorted(self.day(23, self.last.day - 1) for _ in range(4)):
            name, _ = self.vendors.pop()
            amount = self.cents(200, 2600)
            trap = trap or amount
            number = str(self.check)
            self.check += 1
            g = self.gl("A", d, "Check", number, self.messy(name), "", -amount)
            self.add("outstanding_check", "gl_only", "outstanding_check", gl=[g])
        return trap

    def prior_period_checks(self):
        prev = self.first - timedelta(days=1)
        for i, (number, day, amount) in enumerate(self.spec["prior_checks"]):
            name, _ = self.vendors.pop()
            g = self.gl("A", date(prev.year, prev.month, day), "Check", str(number), name, "",
                        -amount, prior=True)
            if i == 0:
                self.add("stale_check", "gl_only", "outstanding_check", gl=[g],
                         note="Outstanding since last month and still not cleared.")
            else:
                b = self.bank("A", self.day(2, 9), f"CHECK {number}", -amount, check_no=str(number))
                self.add("prior_period_check", "match", "matched", [b], [g])

    def payroll(self):
        for pay_day in self.spec["pay_days"]:
            d = date(self.y, self.m, pay_day)
            net, taxes = self.cents(18000, 24500), self.cents(5200, 7400)
            g = self.gl("A", d, "Journal Entry", f"PR-{d:%m%d}", "", f"Payroll {d:%m/%d} net pay", -net)
            b = self.bank("A", d, f"PAYROLL SVC WAGE PAY {self.ref()}", -net)
            self.add("payroll", "match", "matched", [b], [g])
            g = self.gl("A", d, "Journal Entry", f"PR-{d:%m%d}T", "", f"Payroll taxes {d:%m/%d}", -taxes)
            b = self.bank("A", add_bizdays(d, 1), f"IRS USATAXPYMT {self.ref(8)}", -taxes)
            self.add("payroll", "match", "matched", [b], [g])

    def customer_receipts(self):
        for _ in range(10):
            name, desc = self.customers.pop()
            d = self.day(2, 24)
            amount = self.cents(900, 14000)
            g = self.gl("D", d, "Payment", "", self.messy(name), f"Inv {self.ref(4)}", amount)
            b = self.bank("D", add_bizdays(d, self.rng.randint(0, 2)),
                          f"{desc} PAYMENT {self.ref()}", amount, kind="ACH Credit")
            self.add("customer_ach", "match", "matched", [b], [g])

    def lookalikes(self):
        """Two payments of the same amount a day apart. Only the names tell them apart."""
        amount = self.spec["lookalike_vendor"]
        (n1, d1), (n2, d2) = self.vendors.pop(), self.vendors.pop()
        day = self.day(9, 17)
        g1 = self.gl("A", day, "Bill Payment", "", n1, f"Inv {self.ref(5)}", -amount)
        g2 = self.gl("A", add_bizdays(day, 1), "Bill Payment", "", n2, f"Inv {self.ref(5)}", -amount)
        b2 = self.bank("A", g2.date, self.ach_debit(d2), -amount)
        b1 = self.bank("A", add_bizdays(g1.date, 2), self.ach_debit(d1), -amount)
        self.add("lookalike_pair", "match", "matched", [b1], [g1])
        self.add("lookalike_pair", "match", "matched", [b2], [g2])

        amount = self.spec["lookalike_customer"]
        (n1, d1), (n2, d2) = self.customers.pop(), self.customers.pop()
        day = self.day(9, 17)
        g1 = self.gl("D", day, "Payment", "", n1, f"Inv {self.ref(4)}", amount)
        g2 = self.gl("D", add_bizdays(day, 1), "Payment", "", n2, f"Inv {self.ref(4)}", amount)
        b2 = self.bank("D", g2.date, f"{d2} PAYMENT {self.ref()}", amount, kind="ACH Credit")
        b1 = self.bank("D", add_bizdays(g1.date, 2), f"{d1} PAYMENT {self.ref()}", amount, kind="ACH Credit")
        self.add("lookalike_pair", "match", "matched", [b1], [g1])
        self.add("lookalike_pair", "match", "matched", [b2], [g2])

    def card_purchases(self):
        """Booked on the receipt date under the legal name; posted a week later under the trade name."""
        for i in self.spec["dba"]:
            legal, trade, memo = DBA[i]
            d = self.day(3, 18)
            amount = self.cents(45, 950)
            g = self.gl("A", d, "Expense", "", legal, memo, -amount)
            b = self.bank("A", to_weekday(d + timedelta(days=self.rng.randint(6, 8))),
                          f"DEBIT CARD PURCHASE {trade} {self.ref(4)}", -amount)
            self.add("trade_name_date_gap", "match", "matched", [b], [g])

    def duplicate_entry(self):
        name, desc = self.vendors.pop()
        d = self.day(6, 18)
        amount = self.cents(400, 3800)
        inv = self.ref(5)
        g1 = self.gl("A", d, "Bill Payment", "", name, f"Inv {inv}", -amount)
        g2 = self.gl("A", d, "Bill Payment", "", name, f"Inv# {inv}", -amount)
        b = self.bank("A", add_bizdays(d, 1), self.ach_debit(desc), -amount)
        self.add("duplicate_entry", "match", "matched", [b], [g1], dup=[g2],
                 note="The books record this payment twice; the bank paid it once. "
                      "Either entry is an acceptable match; the other is a duplicate.")

    def card_payouts(self):
        """The processor deposits several sales as one amount, net of its fee."""
        for i, start in enumerate(self.spec["batch_starts"]):
            receipts = []
            for _ in range(self.rng.randint(3, 5)):
                d = date(self.y, self.m, start + self.rng.randint(0, 2))
                amount = self.cents(120, 2600)
                name = self.rng.choice([self.rng.choice(CUSTOMERS)[0], "Walk-in customer"])
                receipts.append(self.gl("D", d, "Sales Receipt", f"SR-{self.receipt}", name, "Card sale", amount))
                self.receipt += 1
            fee = sum(round(r.amount * 0.029) + 30 for r in receipts)
            paid = add_bizdays(max(r.date for r in receipts), 2)
            net = sum(r.amount for r in receipts) - fee
            self.used.add(net)
            b = self.bank("D", paid, f"CARD PROCESSOR PAYOUT ST-{self.ref()}", net, kind="ACH Credit")
            if i == self.spec["batch_missing_fee"]:
                self.add("card_payout", "review", "unrecorded_fee", [b], receipts,
                         note=f"Processing fee of {money(fee)} was never booked, so the sales "
                              f"do not tie to the deposit.")
            else:
                f = self.gl("D", paid, "Expense", "", "Card processor",
                            f"Processing fees - payout {paid:%m/%d}", -fee)
                self.add("card_payout", "match", "matched", [b], receipts + [f])

    def split_deposits(self):
        """One deposit in the books; the bank shows each trip to the counter separately."""
        labels = ["COUNTER DEPOSIT", "ATM CHECK DEPOSIT", "MOBILE CHECK DEPOSIT"]
        for _ in range(2):
            d = self.day(4, 22)
            parts = [self.cents(300, 4200) for _ in range(self.rng.randint(2, 3))]
            self.used.add(sum(parts))
            g = self.gl("D", d, "Deposit", "", "", f"Deposit - {len(parts) + 2} customer checks", sum(parts))
            lines = [self.bank("D", add_bizdays(d, min(k, 1)), f"{self.rng.choice(labels)} {self.ref(4)}",
                               p, kind="Deposit") for k, p in enumerate(parts)]
            self.add("split_deposit", "match", "matched", lines, [g])

    def returned_item(self):
        name, _ = self.customers.pop()
        d = self.day(5, 12)
        amount = self.cents(600, 3200)
        g = self.gl("D", d, "Deposit", "", name, f"Customer check {self.ref(4)}", amount)
        b = self.bank("D", d, f"MOBILE CHECK DEPOSIT {self.ref(4)}", amount, kind="Deposit")
        self.add("check_deposit", "match", "matched", [b], [g])
        back = self.bank("D", add_bizdays(d, 4), f"RETURNED DEPOSITED ITEM NSF {self.ref()}",
                         -amount, kind="Returned Item")
        self.add("returned_item", "bank_only", "returned_item", [back],
                 note=f"The check from {name} bounced. The books still show it as collected.")

    def bank_only_items(self, trap: int):
        month_end = self.last
        while not is_bizday(month_end):
            month_end -= timedelta(days=1)
        fee = self.bank("A", month_end, "MONTHLY MAINTENANCE FEE", -self.spec["maintenance_fee"])
        self.add("bank_fee", "bank_only", "bank_fee", [fee])

        # A bank fee and a real payment for the same $25.00, a day apart.
        d = self.day(10, 14)
        wire = self.bank("A", d, "OUTGOING WIRE TRANSFER FEE", -self.spec["wire_fee"])
        self.add("bank_fee", "bank_only", "bank_fee", [wire])
        g = self.gl("A", d, "Bill Payment", "", "Township of Hollis Run", "Sign permit renewal",
                    -self.spec["wire_fee"])
        b = self.bank("A", add_bizdays(d, 1), f"ACH DEBIT HOLLIS RUN TWP PERMITS {self.ref()}",
                      -self.spec["wire_fee"])
        self.add("lookalike_fee", "match", "matched", [b], [g])

        interest = self.bank("D", month_end, "INTEREST PAID", self.spec["interest"], kind="Interest")
        self.add("interest", "bank_only", "interest", [interest])

        # Same amount as an outstanding check, but it is not a check and nobody booked it.
        unknown = self.bank("A", self.day(24, 28), f"ACH DEBIT NTWK SVC BILLING {self.ref()}", -trap)
        self.add("unrecognized_debit", "bank_only", "unrecognized", [unknown],
                 note="No ledger entry. An outstanding check happens to share the amount.")

    def transfer(self):
        d = self.day(8, 12)
        amount = self.spec["transfer"]
        out = self.bank("D", d, "ONLINE TRANSFER TO CHK ...4417", -amount, kind="Transfer")
        inn = self.bank("A", d, "ONLINE TRANSFER FROM ...9052", amount)
        g_in = self.gl("A", d, "Transfer", "", "", "Funds transfer from deposit account", amount)
        g_out = self.gl("D", d, "Transfer", "", "", "Funds transfer to operating", -amount)
        self.add("transfer", "match", "matched", [inn], [g_in])
        self.add("transfer", "match", "matched", [out], [g_out])

    def deposit_in_transit(self):
        g = self.gl("D", self.last, "Deposit", "", "", "Deposit - customer checks", self.cents(1500, 6000))
        self.add("deposit_in_transit", "gl_only", "deposit_in_transit", gl=[g])

    # -- assemble --------------------------------------------------------------
    def build(self) -> Month:
        self.vendor_payments()
        self.checks()
        trap = self.outstanding_checks()
        self.prior_period_checks()
        self.payroll()
        self.customer_receipts()
        self.lookalikes()
        self.card_purchases()
        self.duplicate_entry()
        self.card_payouts()
        self.split_deposits()
        self.returned_item()
        self.bank_only_items(trap)
        self.transfer()
        self.deposit_in_transit()

        mm = f"{self.m:02d}"
        bank = [b for e in self.events for b in e.bank]
        ledger = [g for e in self.events for g in e.gl + e.dup]
        # File order is the id order. The operating export lists newest first, as that bank does.
        bank_a = sorted((b for b in bank if b.account == "A"), key=lambda b: (b.date, b.seq), reverse=True)
        bank_d = sorted((b for b in bank if b.account == "D"), key=lambda b: (b.date, b.seq))
        gl = sorted((g for g in ledger if not g.prior), key=lambda g: (g.account, g.date, g.seq))
        prior = sorted((g for g in ledger if g.prior), key=lambda g: g.num)
        for prefix, rows, width in (("A", bank_a, 3), ("D", bank_d, 3), ("G", gl, 3), ("P", prior, 2)):
            for i, row in enumerate(rows, 1):
                row.id = f"{prefix}-{mm}-{i:0{width}d}"

        accounts = {}
        for acct, book_open in (("A", self.spec["open_a"]), ("D", self.spec["open_d"])):
            bank_open = book_open - sum(g.amount for g in prior if g.account == acct)
            accounts[acct] = {
                "ledger_account": GL_ACCOUNT[acct],
                "book_open": book_open,
                "book_end": book_open + sum(g.amount for g in gl if g.account == acct),
                "bank_open": bank_open,
                "bank_end": bank_open + sum(b.amount for b in bank if b.account == acct),
            }
        groups = []
        for i, e in enumerate(self.events, 1):
            group = {
                "group_id": f"{self.ym}-{i:03d}", "category": e.category, "expected": e.expected,
                "reason_code": e.reason,
                "bank_ids": sorted(b.id for b in e.bank), "gl_ids": sorted(g.id for g in e.gl),
            }
            if e.dup:
                group["duplicate_gl_ids"] = sorted(g.id for g in e.dup)
            if e.note:
                group["note"] = e.note
            groups.append(group)
        key = {
            "month": self.ym, "company": COMPANY, "synthetic": True,
            "note": "Invented data. Amounts are integer cents. Bank amounts are signed from the "
                    "bank's side, ledger amounts from the cash account's side.",
            "accounts": accounts, "groups": groups,
        }
        return Month(self.ym, bank_a, bank_d, gl, prior, key)


def build_month(ym: str) -> Month:
    return _Builder(ym).build()


# -- rendering: the files a client would actually hand over ---------------------

def _csv(rows) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def _bank_a(month: Month) -> str:
    """Operating account. Newest first, separate debit and credit columns, US dates."""
    running = month.key["accounts"]["A"]["bank_open"]
    balance = {}
    for b in sorted(month.bank_a, key=lambda b: (b.date, b.seq)):
        running += b.amount
        balance[b.id] = running
    rows = [["Date", "Description", "Debit", "Credit", "Balance"]]
    for b in month.bank_a:
        rows.append([f"{b.date:%m/%d/%Y}", b.description,
                     money(-b.amount) if b.amount < 0 else "",
                     money(b.amount) if b.amount > 0 else "", money(balance[b.id])])
    return _csv(rows)


def _bank_d(month: Month) -> str:
    """Deposit account. Oldest first, one signed amount column, ISO dates."""
    running = month.key["accounts"]["D"]["bank_open"]
    rows = [["Posted Date", "Transaction Type", "Memo", "Amount", "Check Number", "Running Balance"]]
    for b in month.bank_d:
        running += b.amount
        rows.append([b.date.isoformat(), b.kind, b.description, money(b.amount), b.check_no, money(running)])
    return _csv(rows)


def _ledger(month: Month) -> str:
    """General ledger detail for the cash accounts, with the report furniture left in."""
    y, m = (int(x) for x in month.ym.split("-"))
    first = date(y, m, 1)
    last = max(g.date for g in month.gl)
    rows = [[COMPANY], ["General Ledger"], [f"{first:%B} 1-{last.day}, {y}"], [],
            ["Date", "Transaction Type", "Num", "Name", "Memo/Description", "Split", "Amount", "Balance"]]
    for acct in ("A", "D"):
        running = month.key["accounts"][acct]["book_open"]
        rows.append([GL_ACCOUNT[acct], "", "", "", "", "", "", ""])
        rows.append(["Beginning Balance", "", "", "", "", "", "", money(running)])
        total = 0
        for g in (g for g in month.gl if g.account == acct):
            running += g.amount
            total += g.amount
            rows.append([f"{g.date:%m/%d/%Y}", g.txn_type, g.num, g.name, g.memo,
                         SPLIT[g.txn_type], money(g.amount), money(running)])
        rows.append([f"Total for {GL_ACCOUNT[acct]}", "", "", "", "", "", money(total), ""])
    after = last + timedelta(days=2)
    rows += [[], [f"Accrual Basis {after:%A, %B} {after.day}, {after.year} 09:14 AM GMT-04:00"]]
    return _csv(rows)


def _prior(month: Month) -> str:
    rows = [["Check No", "Check Date", "Payee", "Amount"]]
    for g in month.prior:
        rows.append([g.num, f"{g.date:%m/%d/%Y}", g.name, money(-g.amount)])
    return _csv(rows)


def render(month: Month) -> dict[str, str]:
    """Relative path -> file text, for every file this month produces."""
    raw = f"data/raw/{month.ym}"
    return {
        f"{raw}/{BANK_FILE['A']}": _bank_a(month),
        f"{raw}/{BANK_FILE['D']}": _bank_d(month),
        f"{raw}/general_ledger_cash.csv": _ledger(month),
        f"{raw}/outstanding_items_prior.csv": _prior(month),
        f"data/answer_key/{month.ym}.json": json.dumps(month.key, indent=2) + "\n",
    }


def write(month: Month, root: Path = ROOT) -> list[Path]:
    written = []
    for rel, text in render(month).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written

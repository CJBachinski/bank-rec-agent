<!-- live run, claude-sonnet-5-5, prompt v3; clearing policy medium_up -->
# Bank reconciliation, 2026-10

Synthetic data for a fictional company. Amounts in dollars.

## 1000 Operating Checking

36 of 40 bank lines cleared. 0 unexplained.

| | Amount |
|---|---:|
| Bank ending balance | 30,729.77 |
| Ledger items not yet at the bank | -7,274.85 |
| **Adjusted bank balance** | **23,454.92** |
| Book ending balance | 25,243.64 |
| Bank items not yet in the books | -1,428.72 |
| Items waiting on a decision | -360.00 |
| Unexplained bank items | 0.00 |
| **Adjusted book balance** | **23,454.92** |

### Unexplained bank items

_None._

### Waiting on a decision

- **amount_mismatch** (A-10-029, G-10-011): difference -360.00. Check number agrees but the bank paid 2,736.40 and the books show 2,376.40.

### Bank items the books need an entry for

| Id | Date | Item | Amount |
|---|---|---|---:|
| A-10-028 | 2026-10-14 | OUTGOING WIRE TRANSFER FEE | -25.00 |
| A-10-004 | 2026-10-27 | ACH DEBIT NTWK SVC BILLING 762577 | -1,368.72 |
| A-10-001 | 2026-10-30 | MONTHLY MAINTENANCE FEE | -35.00 |
| | | **Total** | **-1,428.72** |

### Ledger items not yet at the bank

| Id | Date | Item | Amount |
|---|---|---|---:|
| P-10-01 | 2026-09-21 | Check 1049 Tri-County Waste Services | -742.10 |
| G-10-027 | 2026-10-16 | Bill Payment Harborview Staffing, LLC | -2,105.05 |
| G-10-035 | 2026-10-23 | Check 1077 Upland Hydraulics | -1,368.72 |
| G-10-036 | 2026-10-27 | Check 1078 Summit Packaging Corp | -438.87 |
| G-10-039 | 2026-10-29 | Check 1079 Lehigh Gas & Electric | -2,411.88 |
| G-10-040 | 2026-10-30 | Check 1080 Quarry Road Auto Repair | -208.23 |
| | | **Total** | **-7,274.85** |

## 1010 Deposit Account

23 of 26 bank lines cleared. 0 unexplained.

| | Amount |
|---|---:|
| Bank ending balance | 150,052.05 |
| Ledger items not yet at the bank | 13,806.44 |
| **Adjusted bank balance** | **163,858.49** |
| Book ending balance | 156,747.99 |
| Bank items not yet in the books | -930.36 |
| Items waiting on a decision | 8,040.86 |
| Unexplained bank items | 0.00 |
| **Adjusted book balance** | **163,858.49** |

### Unexplained bank items

_None._

### Waiting on a decision

- **unrecorded_fee** (D-10-009): difference 8,040.86. Agent proposed needs_review. Agent's note: The payout most likely covers the five card sales G-10-043, G-10-045, G-10-046, G-10-047 and G-10-048. Those total 8,282.55, which is 241.69 more than the bank amount. No processing-fee entry is booked for this payout. The later payout D-10-012 ties exactly to its sales net of a booked fee, G-10-059. A person should confirm the grouping and book the 241.69 fee before the items are cleared.

### Bank items the books need an entry for

| Id | Date | Item | Amount |
|---|---|---|---:|
| D-10-010 | 2026-10-09 | RETURNED DEPOSITED ITEM NSF 634780 | -951.43 |
| D-10-026 | 2026-10-30 | INTEREST PAID | 21.07 |
| | | **Total** | **-930.36** |

### Ledger items not yet at the bank

| Id | Date | Item | Amount |
|---|---|---|---:|
| G-10-043 | 2026-10-05 | Sales Receipt SR-3346 Walk-in customer | 2,109.81 |
| G-10-045 | 2026-10-06 | Sales Receipt SR-3342 Kessler Sheet Metal | 915.85 |
| G-10-046 | 2026-10-06 | Sales Receipt SR-3344 Greer Brothers Roofing | 846.67 |
| G-10-047 | 2026-10-06 | Sales Receipt SR-3345 Walk-in customer | 2,470.54 |
| G-10-048 | 2026-10-07 | Sales Receipt SR-3343 Corvid Automation | 1,939.68 |
| G-10-076 | 2026-10-31 | Deposit Deposit - customer checks | 5,523.89 |
| | | **Total** | **13,806.44** |

# Prompt changelog

How the agent's system prompt changed, why, and what each change did. Generated from saved runs by `scripts/build_readme.py`.

Ground rules I held to:

- Prompts were tuned on the development month (September 2026) only.
- A change had to answer a failure that actually appeared in a run. No changes on a hunch.
- Every version was scored on the same cases with the same grader and the same clearing policy (`{{policy}}`).
- The held-out month (October 2026) was run once, after version 3, the grader and the policy were frozen.

Each development run is two repetitions of {{dev.bank_lines}} bank lines plus {{dev.negatives}} traps and {{dev.controls}} controls. That is small. A change that moves two or three cases shows direction, not size.

## v1: baseline

A plain description of the job: the three possible decisions, the tools, the sign convention, and an instruction to cite the fields relied on. Nothing about this company.

What went wrong on the development month:

{{table.failures_dev_v1}}

Reading those transcripts, the failures fell into three patterns, and none of them was a wrong clear:

1. **Trade names.** A card purchase posted as `SQ *BLUE HERON CAFE` against a ledger entry for Heron Coffee Roasters LLC, same amount to the cent, a week apart. Both models sent it to review because the names differ.
2. **Split deposits.** One ledger deposit shown at the bank as two or three lines. The agent decides one bank line at a time. When one line found the group and its sibling did not, the policy held the whole group, because every line in a group has to propose the same group.
3. **Duplicated entries.** A bill booked twice and paid once. The agent correctly spotted the duplicate, then sent the bank line to review instead of matching it to one copy.

One further "failure" was the grader's, not the model's: an unidentified debit sent to review was being marked wrong because the answer key filed it as "bank only". Both mean the same thing to the person who gets it. The grader was corrected and all runs re-scored.

## v2: how this company's records behave

Added one short section with three facts and the reason for each:

1. Card purchases post under trade names with processor prefixes, up to about a week late. A different-looking name is normal **for card purchases only**. An ACH or check payment to one named company is not the same money as an entry for another, even at the same amount. (The second sentence is there because loosening one rule without stating its boundary invites the opposite error.)
2. One ledger deposit can appear as several bank lines. If nothing has this line's amount, look at sibling deposits and propose the whole group.
3. Bills are sometimes entered twice. Match one copy and say the other looks like a duplicate.

What went wrong on the development month:

{{table.failures_dev_v2}}

Version 2 cleared every v1 failure on both models. It also exposed a new one on Haiku: citing real text (`ACH DEBIT`, `DEBIT CARD PURCHASE`) under a field name, `txn_type`, that bank lines do not have. The new instructions had made the payment method matter, and the smaller model reached for a field to cite it from. The verifier rejected every one of those citations, which held two correct matches for review.

This also showed the grader was lumping two different things together. A value that differs from what is stored is invented content. Real text under the wrong field name is a broken citation. They are now counted separately.

## v3: which fields exist

One sentence added: the fields a bank line has, the fields a ledger entry has, and that the payment method is part of the bank description and should be quoted from there.

What went wrong on the development month:

{{table.failures_dev_v3}}

## Measured effect

Development month, Claude Sonnet 5.5:

{{table.prompts_dev_sonnet}}

Development month, Claude Haiku 5.5:

{{table.prompts_dev_haiku}}

The held-out month, which no prompt was tuned on ({{test.v3.sonnet.reps}} repetitions).

Held-out month, Claude Sonnet 5.5:

{{table.prompts_test_sonnet}}

Held-out month, Claude Haiku 5.5:

{{table.prompts_test_haiku}}

## What I would and would not conclude

- The development month reaching a perfect score proves little by itself: the prompt was written to fix exactly those cases. The held-out month is the evidence, and the gain held there.
- The held-out month comes from the same generator as the development month. The prompt now describes this company's three awkward patterns. It has not been tested on a fourth.
- On the development and held-out sets, no version produced a forced match or an invented value, so the prompt work here bought automation, not safety. Safety came from the model's own caution and the verifier. I would not claim the prompt "reduced hallucination"; there was none to reduce. (The one misquoted value in the project came later, on the stress set, under the final prompt; see the failure analysis.)
- Version 2's side effect on Haiku is the useful lesson: an instruction that fixes one behaviour can break another, and only re-running the whole set shows it.

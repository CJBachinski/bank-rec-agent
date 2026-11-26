# Failure analysis

Everything that went wrong while building and testing the agent, where it was caught, and what was done. Tables are generated from saved runs by `scripts/build_readme.py`. All runs use the clearing policy `{{policy}}`.

## 1. Summary

| What failed | Where it showed up | What caught it | What was done |
|---|---|---|---|
| Too cautious about trade names on card purchases | Prompt v1, both models | Nothing needed to; the line went to review | Prompt v2 |
| One line of a split deposit not finding its group | Prompt v1, both models | The policy: a group clears only if every line proposes it | Prompt v2 |
| Duplicated ledger entry sent to review instead of matched | Prompt v1, both models | Nothing needed to | Prompt v2 |
| Real text cited under a field the record does not have | Prompt v2, Haiku only | The verifier rejected the citation and held the proposal | Prompt v3; grader now reports it separately |
| A payout's sales not found | Final prompt, Haiku, held-out month | Nothing needed to; the line went to review | Open; see section 4 |
| Rule window misses payments across a holiday weekend | Rules, both months | Nothing needed to; the line went to the agent | Left as is; see section 5 |
| Grader marked an unidentified debit "wrong" when sent to review | Reading v1 failures | Reading the failures | Grader corrected before the held-out run |
| Grader counted a mislabelled citation as an invented value | Reading v2 failures | Reading the failures | Grader corrected before the held-out run |
| Verifier rejected a citation that correctly quoted an empty field as empty | Prompt v1, Haiku, held-out month, one proposal | Checking this document's claims against the runs | Verifier corrected after the held-out run; no reported result changed |

Not one of these was a false clear. Across every run in this repository the agent made {{all.false_clears}} false clears in {{all.agent_trials}} decisions on month data, and {{all.hallucinated}} hallucinations in {{all.neg_trials}} trap trials.

## 2. What the agent got wrong during development

Prompt v1:

{{table.failures_dev_v1}}

Prompt v2:

{{table.failures_dev_v2}}

Prompt v3:

{{table.failures_dev_v3}}

The reasoning behind each prompt change is in [prompts/CHANGELOG.md](../prompts/CHANGELOG.md).

## 3. The pattern in the failures

Every agent failure was in the same direction: declining or holding something that was in fact a match. That is the cheap direction. A held match costs a person a minute in the review queue. A false clear costs an error nobody looks for.

The design pushes failures that way on purpose:

- The model is asked for a proposal, not an action.
- The verifier rejects anything it cannot check, including a correct match with a broken citation.
- A group clears only when every bank line in it independently reaches the same answer.
- Two proposals that claim the same ledger entry both go to review.

The price is automation. Under the first prompt, the unanimity rule alone held several split deposits that two of three lines had worked out correctly.

## 4. Failures on the held-out month

Final prompt, three models, {{test.v3.sonnet.reps}} runs each:

{{table.failures_test}}

First prompt, for comparison:

{{table.failures_test_v1}}

The first-prompt failures on October are dominated by the same three patterns seen on September, on different transactions, which is some evidence the patterns are properties of the data and the model rather than accidents of one month.

The one final-prompt failure is Haiku, in one run of three, not finding the three card sales that make up a processor payout. It reported low confidence and sent the line to review. The other two runs found them.

## 5. What the rules miss

The deterministic pass clears a bank line only when the amount is identical, the account is the same, the dates are within four calendar days and there is exactly one candidate in each direction. A payment that takes two business days to post across a holiday weekend lands five calendar days later.

- September (Labor Day): {{dev.rules.window_misses}} such line
- October (Columbus Day): {{test.rules.window_misses}} such lines

I left the rule alone. Widening the window after seeing the misses would be tuning the rule to the data, and a wider window makes ambiguous candidates more common. The agent matched all of these.

## 6. What the verifier can and cannot stop

The harness includes a stand-in "model" that looks up the amount and matches whatever comes back first. On the September month it walks into every trap and clears nothing: two bank lines end up claiming one ledger entry, or a plain debit gets matched to a numbered check, and the verifier or the policy holds each one.

What the verifier cannot see is an amount that ties to the wrong party. {{test.guard_blind}} cases in each trap set are built that way. The careless stand-in forces a match on {{standin.greedy.blind_forced}} of {{standin.greedy.blind_n}} of them, and {{standin.greedy.blind_escaped}} would be cleared. The real models forced none: {{all.blind_forced}} in {{all.blind_trials}} trials across every run here, which puts the true rate below about {{all.blind_upper}} at best, since repeated runs of the same case are not independent.

This is where the residual risk sits, and it rests on the model's judgement and the confidence policy rather than on code.

## 7. Where the grader was wrong

Reading failed cases, not just counting them, found two grader errors:

1. **Unidentified debit.** The answer key files a debit nobody booked as "bank only". The agent sometimes sent it to review instead. Both tell a person the same thing. The grader now accepts either for an unidentified item, and still requires "bank only" for a fee or interest line, where the entry to book is known.
2. **Mislabelled citation.** The verifier's "value does not match" code also covered "this record has no such field". Those are different faults. They now have separate codes and are reported in separate columns.

Two test assumptions were also wrong and were rewritten to say what is true: that the rules would clear every easy item (the holiday window), and that the careless stand-in would produce false clears on the month (the verifier stops them).

Both grader corrections were made, tested and committed before the agent was run on the held-out month.

One fault was found afterwards. While checking the claims in this document against the saved runs, I found a single proposal the verifier had rejected for quoting a bank line's check number as empty. The field is empty, so the citation was true. The verifier required quoted text to be non-empty. It now accepts an empty quote for an empty field and still rejects one for a field that has a value. The proposal was a "no match" on an unidentified debit under the first prompt, already scored correct either way, so no number in the README moved. I am recording it because it was a change made after the held-out data had been seen.

## 8. What this set cannot tell me

- The final prompt scores {{test.v3.haiku.correct_pct}} to {{test.v3.opus.correct_pct}} on the held-out month across three models. A test that everything passes cannot rank them or show the next weakness.
- The held-out month was produced by the same generator as the development month.
- {{test.guard_blind}} wrong-party cases per set is too few to bound that risk tightly for any one configuration.

## 9. Conclusions

> **Draft.** The observations above are generated from the runs. The conclusions below are a starting point and need to be rewritten in my own words before this is published.

- The model's errors in this domain ran toward caution. The system was designed so that they would, and the tests confirm the direction, not the absence of risk.
- The most valuable testing work was reading individual failures. It found two grader errors and one prompt side effect that no aggregate score would have shown.
- Next steps, in order: harder trap cases (more wrong-party ties, real bank descriptors), a second fictional company with different conventions, and a month of shadow running on real data.

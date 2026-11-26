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

**The agent did not fail the way I expected.** I built the trap set to catch a model inventing matches, ids and figures. Across {{all.neg_trials}} trap trials it did none of those. Every agent failure was a real match it declined or held. I do not read that as "the model is safe". I read it as the remaining risk being concentrated in one place, an amount that ties to the wrong party, which is also where my evidence is thinnest ({{all.blind_trials}} trials).

**The safety came from code. The prompt bought automation.** No prompt version produced a bad clear, so no prompt version can take credit for preventing one. What the prompt changed was how much work reached a person: on the held-out month, the same model left {{test.v1.sonnet.left}} of {{test.v1.sonnet.line_trials}} line-trials for review under the first prompt and {{test.v3.sonnet.left}} under the final one. The controls that would stop a bad clear are the verifier and the policy, and the careless stand-in model shows they hold on everything except the wrong-party tie. With another week I would spend it on that gap before touching the prompt again.

**Reading failures beat counting them.** The totals looked fine at every stage. The three faults in my own grader and verifier, and the citation fault that version 2 introduced on the smaller model, only showed up when I opened individual cases. An evaluation is software and has bugs like any other software. I now treat a surprising score as a bug in the test until I have read the cases behind it.

**A fix can break something else.** Version 2 fixed three patterns and caused a fourth on a different model. Had I re-tested only the cases the change was written for, or only the model I was tuning, I would have shipped it. Every change re-runs everything.

**The small model was nearly as good once the prompt knew the client.** With the final prompt, Haiku settles {{test.v3.haiku.correct_pct}} of bank lines and Sonnet {{test.v3.sonnet.correct_pct}}, at {{test.v3.haiku.cost_per_100}} against {{test.v3.sonnet.cost_per_100}} per 100 lines. With the first prompt the gap was wider: {{test.v1.haiku.correct_pct}} against {{test.v1.sonnet.correct_pct}}. Much of a deployment is learning the client's conventions and writing them down. The more of that is done, the smaller the model that can do the job, and the stronger model's advantage is in the patterns nobody has written down yet.

**What I would do next, in order.**

1. Harder traps, weighted toward wrong-party ties: the same vendor on a different invoice, recurring fixed amounts, near-identical company names.
2. A second fictional company with different conventions, to see whether the client-specific part of the prompt transfers or has to be rewritten each time.
3. A timed manual reconciliation and a timed review, to replace the estimate in the README.
4. A month of shadow running on real data before the agent clears anything.

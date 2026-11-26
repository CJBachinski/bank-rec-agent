"""Scoring a person's work the same way the agent's is scored, and building the review queue."""
import json

from evals import fakes, timing
from evals.run import answer_key
from recon import pipeline


def key():
    return answer_key("dev")


def test_truth_lists_every_reconciling_item_once():
    t = timing.reconciling_items(key())
    assert len(t["outstanding_ledger"]) == 7          # 4 outstanding checks, 1 stale, 1 in transit, 1 duplicate
    assert len(t["bank_not_in_books"]) == 5
    assert len(t["needs_fixing"]) == 2                # transposed check, card payout with no fee booked


def perfect_rows():
    t = timing.reconciling_items(key())
    rows = [{"list": name, "amount": f"{sorted(options)[0] / 100:,.2f}"}
            for name in ("outstanding_ledger", "bank_not_in_books") for options in t[name]]
    rows += [{"list": "needs_fixing", "amount": f"{sorted(options)[0] / 100:.2f}"} for options in t["needs_fixing"]]
    return rows


def test_a_perfect_manual_reconciliation_finds_everything_with_nothing_extra():
    s = timing.score_manual(perfect_rows(), key())
    assert (s["found"], s["missed"], s["extra"]) == (14, 0, 0)


def test_a_missed_item_and_an_invented_item_are_counted_separately():
    rows = perfect_rows()[1:] + [{"list": "bank_not_in_books", "amount": "12.34"}]
    s = timing.score_manual(rows, key())
    assert (s["found"], s["missed"], s["extra"]) == (13, 1, 1)


def test_an_item_put_on_the_wrong_list_is_missed_and_extra():
    rows = perfect_rows()
    rows[0] = {**rows[0], "list": "bank_not_in_books"}
    s = timing.score_manual(rows, key())
    assert (s["found"], s["missed"], s["extra"]) == (13, 1, 1)


def test_an_error_can_be_reported_as_the_difference_or_as_either_amount():
    for amount in ("9.00", "1,854.00", "1845"):
        rows = [{"list": "needs_fixing", "amount": amount}]
        assert timing.score_manual(rows, key())["found"] == 1, amount


def test_blank_and_unreadable_rows_are_reported_not_silently_dropped():
    s = timing.score_manual([{"list": "", "amount": ""}, {"list": "outstanding_ledger", "amount": "abc"},
                             {"list": "somewhere", "amount": "5.00"}], key())
    assert s["unreadable_rows"] == 2 and s["found"] == 0


def queue_for(llm):
    k = key()
    conn, _ = pipeline.run_month(k["month"], llm=llm, model=llm.name, system="")
    return k, timing.review_queue(conn, k["month"])


def test_review_queue_is_every_bank_line_that_was_not_cleared():
    k, queue = queue_for(fakes.Oracle.from_key(key()))
    not_cleared = sum(len(g["bank_ids"]) for g in k["groups"] if g["bank_ids"] and g["expected"] != "match")
    assert len(queue) == not_cleared == 7
    assert {q["agent_says"] for q in queue} == {"bank_only", "review"}
    assert all(set(q) >= {"id", "date", "description", "amount", "agent_says", "agent_note",
                          "your_decision"} for q in queue)


def test_agreeing_with_a_perfect_agent_scores_every_queue_item_correct():
    k, queue = queue_for(fakes.Oracle.from_key(key()))
    for q in queue:
        q["your_decision"] = "ok"
    s = timing.score_review(queue, k)
    assert (s["items"], s["correct"], s["undecided"]) == (7, 7, 0)


def test_reviewer_can_overrule_the_agent_with_a_match():
    k, queue = queue_for(fakes.Null())             # everything the rules left is filed bank-only
    truth = {b: g for g in k["groups"] for b in g["bank_ids"]}
    for q in queue:
        g = truth[q["id"]]
        q["your_decision"] = {"match": "match " + " ".join(g["gl_ids"]), "bank_only": "bank_only",
                              "review": "fix"}[g["expected"]]
    s = timing.score_review(queue, k)
    assert s["correct"] == s["items"] == len(queue) and len(queue) > 20


def test_agreeing_with_a_wrong_agent_is_scored_wrong_and_blanks_are_undecided():
    k, queue = queue_for(fakes.Null())
    for q in queue[:-2]:
        q["your_decision"] = "ok"
    s = timing.score_review(queue, k)
    assert s["undecided"] == 2
    assert s["correct"] < s["items"] - 10


def test_timing_record_round_trips(tmp_path):
    path = timing.save(tmp_path, "manual_2026-09", {"seconds": 1234.5, "bank_lines": 65})
    assert json.loads(path.read_text())["seconds"] == 1234.5


# -- what the memo may say about time, depending on what was actually measured ------

AGENT = dict(agent_seconds=162.0, queue_items=7)


def test_with_nothing_recorded_the_memo_says_not_yet_measured():
    block = timing.time_block(manual=None, review=None, estimate=None, **AGENT)
    assert block["headline"] == "not yet measured"
    assert "not yet measured" in block["section"]


def test_an_estimate_is_shown_as_an_estimate_and_never_called_measured():
    estimate = {"minutes": 40, "source": "an experienced practitioner, in conversation"}
    block = timing.time_block(manual=None, review=None, estimate=estimate, **AGENT)
    assert "estimate" in block["headline"] and "40" in block["headline"]
    assert "not a timed session" in block["section"]
    assert "an experienced practitioner, in conversation" in block["section"]
    assert "measured, not estimated" not in block["note"]
    assert "estimate" in block["note"] and "estimate" in block["limit"]


def test_an_estimate_gives_a_range_because_the_review_time_is_still_unknown():
    estimate = {"minutes": 40, "source": "x"}
    block = timing.time_block(manual=None, review=None, estimate=estimate, **AGENT)
    # 40 minutes less 2.7 of model time, less 5, 10 or 15 minutes of review
    assert "| 5 minutes | 7.7 minutes | 32.3 minutes | 81% |" in block["section"]
    assert "| 15 minutes | 17.7 minutes | 22.3 minutes | 56% |" in block["section"]
    assert block["headline"].startswith("22 to 32 minutes")


def test_two_timed_sessions_replace_the_estimate():
    manual = {"seconds": 2400.0, "minutes": 40.0, "bank_lines": 65, "found": 13, "reconciling_items": 14, "extra": 0}
    review = {"seconds": 420.0, "minutes": 7.0, "bank_lines": 65, "queue_items": 7, "correct": 7, "items": 7,
              "agent_model_seconds": 162.0}
    block = timing.time_block(manual=manual, review=review, estimate={"minutes": 99, "source": "x"}, **AGENT)
    assert "estimate" not in block["headline"]
    assert block["headline"] == "30 minutes (76%)"
    assert "13 of 14 reconciling items found" in block["section"]
    assert "measured" in block["note"]


def test_the_memo_names_the_estimates_source_the_way_the_record_does():
    estimate = {"minutes": 40, "who": "an experienced accountant",
                "source": "an experienced accountant, who said it would take around 40 minutes"}
    block = timing.time_block(manual=None, review=None, estimate=estimate, **AGENT)
    assert "an experienced accountant's estimate" in block["note"]
    assert "from an experienced accountant" in block["limit"]
    assert "Estimate from an experienced accountant, who said it would take around 40 minutes." in block["section"]

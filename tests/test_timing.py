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

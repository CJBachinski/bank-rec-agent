"""The agent loop and its tools, driven by scripted stand-in models. No API calls."""
import json

import pytest

from evals import fakes
from recon import agent, db
from tests.helpers import tiny


def ledger():
    conn = tiny(
        bank=[("B1", "D", "2026-09-10", "CARD PROCESSOR PAYOUT ST-1", 29000),
              ("B2", "D", "2026-09-11", "COUNTER DEPOSIT 1", 5000),
              ("B3", "A", "2026-09-12", "ACH DEBIT ACME", -5000)],
        gl=[("G1", "D", "2026-09-08", "Sales Receipt", "SR-1", "Walk-in customer", "Card sale", 10000),
            ("G2", "D", "2026-09-09", "Sales Receipt", "SR-2", "Brandt Mechanical Inc.", "Card sale", 20000),
            ("G3", "D", "2026-09-10", "Expense", "", "Card processor", "Processing fees", -1000),
            ("G4", "A", "2026-09-11", "Bill Payment", "", "Acme", "Inv 1", -5000),
            ("G5", "D", "2026-09-09", "Payment", "", "Already Cleared Co", "", 5000)])
    db.add_group(conn, "2026-09", "cleared", "rule", ["B2"], ["G5"])
    return conn


def search(conn, **over):
    args = {"account": "D", "amount_min": 0, "amount_max": 1_000_000, "date_from": "2026-09-01",
            "date_to": "2026-09-30", "text": ""}
    return agent.run_tool(conn, "2026-09", "search_ledger", {**args, **over})


# -- tools ---------------------------------------------------------------------

def test_search_ledger_returns_open_entries_in_the_account_only():
    assert [r["id"] for r in search(ledger())["rows"]] == ["G1", "G2", "G3"]


def test_search_ledger_filters_on_amount_magnitude_and_text():
    assert [r["id"] for r in search(ledger(), amount_min=150, amount_max=250)["rows"]] == ["G2"]
    assert [r["id"] for r in search(ledger(), text="brandt")["rows"]] == ["G2"]


def test_search_ledger_shows_signed_amounts_as_text():
    rows = {r["id"]: r for r in search(ledger())["rows"]}
    assert rows["G3"]["amount"] == "-10.00" and rows["G2"]["amount"] == "200.00"


def test_search_results_carry_no_answer_key_fields():
    row = search(ledger())["rows"][0]
    assert set(row) == {"id", "date", "txn_type", "num", "name", "memo", "amount"}


def test_search_bank_returns_other_open_bank_lines():
    out = agent.run_tool(ledger(), "2026-09", "search_bank",
                         {"account": "D", "amount_min": 0, "amount_max": 1_000_000,
                          "date_from": "2026-09-01", "date_to": "2026-09-30", "text": ""})
    assert [r["id"] for r in out["rows"]] == ["B1"]


def test_sum_check_does_the_arithmetic_in_cents():
    out = agent.run_tool(ledger(), "2026-09", "sum_check", {"bank_ids": ["B1"], "gl_ids": ["G1", "G2", "G3"]})
    assert out == {"bank_total": "290.00", "ledger_total": "290.00", "difference": "0.00",
                   "ties": True, "unknown_ids": []}


def test_sum_check_reports_a_difference_and_unknown_ids():
    out = agent.run_tool(ledger(), "2026-09", "sum_check", {"bank_ids": ["B1"], "gl_ids": ["G1", "G2", "G9"]})
    assert (out["difference"], out["ties"], out["unknown_ids"]) == ("-10.00", False, ["G9"])


def test_unknown_tool_or_bad_arguments_raise_a_tool_error():
    with pytest.raises(agent.ToolError):
        agent.run_tool(ledger(), "2026-09", "delete_everything", {})
    with pytest.raises(agent.ToolError):
        agent.run_tool(ledger(), "2026-09", "search_ledger", {"account": "D"})


# -- loop ----------------------------------------------------------------------

FINAL = fakes.proposal("match", ["B1"], ["G1", "G2", "G3"])


def decide(steps, **over):
    llm = fakes.Scripted(steps)
    args = dict(llm=llm, model="fake", system="You reconcile.", effort="medium")
    return agent.decide(ledger(), "2026-09", "B1", **{**args, **over}), llm


def test_loop_runs_tools_then_returns_the_structured_decision():
    steps = [fakes.response([fakes.tool_use("sum_check", {"bank_ids": ["B1"], "gl_ids": ["G1", "G2", "G3"]})],
                            stop_reason="tool_use"),
             fakes.response([fakes.text(FINAL)])]
    result, llm = decide(steps)
    assert (result.status, result.proposal) == ("ok", FINAL)
    assert (result.turns, result.tool_calls) == (2, 1)
    assert result.usage["input_tokens"] == 200 and result.usage["output_tokens"] == 40
    assert [t["role"] for t in result.trace] == ["system", "user", "tool_call", "tool_result", "assistant"]
    # the tool result went back to the model in a single user message
    sent = llm.calls[1]["messages"][-1]
    assert sent["role"] == "user" and sent["content"][0]["type"] == "tool_result"
    assert json.loads(sent["content"][0]["content"])["ties"] is True


def test_first_message_gives_the_model_the_bank_line_and_nothing_from_the_key():
    _, llm = decide([fakes.response([fakes.text(FINAL)])])
    payload = json.loads(llm.calls[0]["messages"][0]["content"])
    assert payload["bank_line"] == {"id": "B1", "account": "D", "date": "2026-09-10",
                                    "description": "CARD PROCESSOR PAYOUT ST-1", "amount": "290.00",
                                    "check_no": ""}
    assert set(payload) == {"month", "bank_line", "accounts"}


def test_every_request_asks_for_the_same_schema_and_effort():
    _, llm = decide([fakes.response([fakes.text(FINAL)])])
    cfg = llm.calls[0]["output_config"]
    assert cfg["effort"] == "medium" and cfg["format"]["schema"] == agent.DECISION_SCHEMA
    assert {t["name"] for t in llm.calls[0]["tools"]} == {"search_ledger", "search_bank", "sum_check"}
    assert all(t["strict"] for t in llm.calls[0]["tools"])


def test_the_stable_prefix_is_marked_for_prompt_caching():
    """Tools and system prompt are identical for every case in a run, so they are cached once."""
    _, llm = decide([fakes.response([fakes.text(FINAL)])])
    call = llm.calls[0]
    assert call["system"] == [{"type": "text", "text": "You reconcile.", "cache_control": {"type": "ephemeral"}}]
    assert call["cache_control"] == {"type": "ephemeral"}


def test_bad_tool_arguments_come_back_as_an_error_result_and_the_loop_continues():
    steps = [fakes.response([fakes.tool_use("search_ledger", {"account": "Z"})], stop_reason="tool_use"),
             fakes.response([fakes.text(FINAL)])]
    result, llm = decide(steps)
    assert result.status == "ok"
    assert llm.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_running_out_of_turns_is_not_an_answer():
    spin = fakes.response([fakes.tool_use("sum_check", {"bank_ids": ["B1"], "gl_ids": []})], stop_reason="tool_use")
    result, _ = decide([spin] * 3, max_turns=3)
    assert (result.status, result.proposal) == ("max_turns", None)


def test_cut_off_response_is_truncated_not_an_answer():
    result, _ = decide([fakes.response([fakes.text('{"decision": "mat')], stop_reason="max_tokens")])
    assert (result.status, result.proposal) == ("truncated", None)


def test_refusal_is_recorded_as_a_refusal():
    result, _ = decide([fakes.response([], stop_reason="refusal")])
    assert (result.status, result.proposal) == ("refusal", None)


def test_text_that_is_not_the_schema_is_unparseable_not_an_answer():
    result, _ = decide([fakes.response([fakes.text("I think it matches G1.")])])
    assert (result.status, result.proposal) == ("unparseable", None)


def test_a_different_model_answering_is_an_error_not_a_result():
    with pytest.raises(agent.ServedModelMismatch):
        decide([fakes.response([fakes.text(FINAL)], model="some-other-model")])

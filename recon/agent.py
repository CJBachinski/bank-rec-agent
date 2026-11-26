"""The agent: one bank line the rules could not clear, three read-only tools, one structured answer.

The model can look things up and ask for arithmetic. It cannot write anything.
What it returns is a proposal, checked by recon/verify.py and applied (or not) by
recon/pipeline.py. Each bank line is decided on its own, from the same starting
state, so a run can be repeated, resumed and graded line by line.

The answer shape is enforced by the API (structured outputs), and the tools use
strict schemas, so there is no free text to parse and no retry-on-bad-JSON loop.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from recon import ingest

ROOT = Path(__file__).resolve().parent.parent
REASONS = ["matched", "bank_fee", "interest", "returned_item", "unrecognized", "amount_mismatch",
           "unrecorded_fee", "duplicate_entry", "insufficient_evidence", "other"]
EVIDENCE_FIELDS = ["amount", "date", "description", "check_no", "name", "memo", "num", "txn_type"]
MAX_ROWS = 40

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["match", "no_match", "needs_review"]},
        "bank_ids": {"type": "array", "items": {"type": "string"},
                     "description": "The bank line under review, plus any other bank lines in the same group."},
        "gl_ids": {"type": "array", "items": {"type": "string"},
                   "description": "Ledger entries that are the same money. Empty unless decision is match."},
        "reason_code": {"type": "string", "enum": REASONS},
        "evidence": {
            "type": "array",
            "description": "Field values you relied on, quoted exactly as stored.",
            "items": {
                "type": "object",
                "properties": {"txn_id": {"type": "string"},
                               "field": {"type": "string", "enum": EVIDENCE_FIELDS},
                               "value": {"type": "string"}},
                "required": ["txn_id", "field", "value"], "additionalProperties": False,
            },
        },
        "explanation": {"type": "string", "description": "One or two sentences a reviewer can check."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["decision", "bank_ids", "gl_ids", "reason_code", "evidence", "explanation", "confidence"],
    "additionalProperties": False,
}

_SEARCH = {
    "type": "object",
    "properties": {
        "account": {"type": "string", "enum": ["A", "D"]},
        "amount_min": {"type": "number", "description": "Smallest amount in dollars, ignoring sign."},
        "amount_max": {"type": "number", "description": "Largest amount in dollars, ignoring sign."},
        "date_from": {"type": "string", "description": "ISO date, inclusive."},
        "date_to": {"type": "string", "description": "ISO date, inclusive."},
        "text": {"type": "string", "description": "Words to look for, or an empty string for no filter."},
    },
    "required": ["account", "amount_min", "amount_max", "date_from", "date_to", "text"],
    "additionalProperties": False,
}
TOOLS = [
    {"name": "search_ledger", "strict": True, "input_schema": _SEARCH,
     "description": "List open ledger entries (not yet matched to anything) in one cash account. Amounts in "
                    "the results are signed: money into the account is positive, money out is negative. "
                    f"Text is matched against name, memo and number. At most {MAX_ROWS} rows."},
    {"name": "search_bank", "strict": True, "input_schema": _SEARCH,
     "description": "List open bank lines (not yet matched to anything) in one account, including the one "
                    "under review. Use it when one ledger entry may cover several bank lines. Same sign "
                    f"convention. Text is matched against the description. At most {MAX_ROWS} rows."},
    {"name": "sum_check", "strict": True,
     "description": "Add up bank lines and ledger entries and report whether they tie to the cent. "
                    "Use this for every sum or difference; do not do arithmetic yourself.",
     "input_schema": {
         "type": "object",
         "properties": {"bank_ids": {"type": "array", "items": {"type": "string"}},
                        "gl_ids": {"type": "array", "items": {"type": "string"}}},
         "required": ["bank_ids", "gl_ids"], "additionalProperties": False}},
]


class ToolError(ValueError):
    pass


class ServedModelMismatch(RuntimeError):
    pass


@dataclass
class Result:
    status: str                     # ok | truncated | refusal | max_turns | timeout | unparseable
    proposal: dict | None
    model: str = ""
    stop_reason: str = ""
    turns: int = 0
    tool_calls: int = 0
    attempts: int = 0
    latency_s: float = 0.0
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0,
                                                 "cache_read_input_tokens": 0,
                                                 "cache_creation_input_tokens": 0})
    trace: list = field(default_factory=list)


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}{dollars:,}.{rem:02d}"


def load_prompt(version: str) -> str:
    return (ROOT / "prompts" / f"{version}.md").read_text(encoding="utf-8").strip()


# -- tools ---------------------------------------------------------------------

def _search(conn, month, view, date_col, text_cols, args):
    try:
        account, text = args["account"], args["text"].strip()
        lo, hi = round(float(args["amount_min"]) * 100), round(float(args["amount_max"]) * 100)
        d1, d2 = ingest._date(args["date_from"]), ingest._date(args["date_to"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ToolError(f"bad arguments: {exc}") from exc
    if account not in ("A", "D"):
        raise ToolError("account must be A or D")
    like = " OR ".join(f"{c} LIKE ?" for c in text_cols)
    rows = conn.execute(
        f"SELECT * FROM {view} WHERE month = ? AND account = ? AND status = 'open' "
        f"AND ABS(amount_cents) BETWEEN ? AND ? AND {date_col} BETWEEN ? AND ? "
        f"AND (? = '' OR {like}) ORDER BY {date_col}, id",
        (month, account, lo, hi, d1, d2, text, *([f"%{text}%"] * len(text_cols)))).fetchall()
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def run_tool(conn, month: str, name: str, args: dict) -> dict:
    if name == "search_ledger":
        rows, more = _search(conn, month, "gl_status", "entry_date", ("name", "memo", "num"), args)
        return {"rows": [{"id": r["id"], "date": r["entry_date"], "txn_type": r["txn_type"], "num": r["num"],
                          "name": r["name"], "memo": r["memo"], "amount": money(r["amount_cents"])}
                         for r in rows], "more_rows_not_shown": more}
    if name == "search_bank":
        rows, more = _search(conn, month, "bank_status", "posted_date", ("description",), args)
        return {"rows": [{"id": r["id"], "date": r["posted_date"], "description": r["description"],
                          "check_no": r["check_no"], "amount": money(r["amount_cents"])}
                         for r in rows], "more_rows_not_shown": more}
    if name == "sum_check":
        try:
            bank_ids, gl_ids = list(args["bank_ids"]), list(args["gl_ids"])
        except (KeyError, TypeError) as exc:
            raise ToolError(f"bad arguments: {exc}") from exc
        totals, unknown = {"bank_txn": 0, "gl_entry": 0}, []
        for table, ids in (("bank_txn", bank_ids), ("gl_entry", gl_ids)):
            for i in ids:
                row = conn.execute(f"SELECT amount_cents FROM {table} WHERE id = ?", (i,)).fetchone()
                if row is None:
                    unknown.append(i)
                else:
                    totals[table] += row[0]
        diff = totals["bank_txn"] - totals["gl_entry"]
        return {"bank_total": money(totals["bank_txn"]), "ledger_total": money(totals["gl_entry"]),
                "difference": money(diff), "ties": diff == 0 and not unknown, "unknown_ids": unknown}
    raise ToolError(f"no such tool: {name}")


# -- loop ----------------------------------------------------------------------

def first_message(conn, month: str, bank_id: str) -> str:
    b = conn.execute("SELECT * FROM bank_txn WHERE id = ?", (bank_id,)).fetchone()
    if b is None:
        raise KeyError(bank_id)
    accounts = {r["account"]: r["ledger_account"] for r in
                conn.execute("SELECT account, ledger_account FROM account_balance WHERE month = ?", (month,))}
    return json.dumps({
        "month": month,
        "accounts": accounts or {v: k for k, v in ingest.LEDGER_ACCOUNTS.items()},
        "bank_line": {"id": b["id"], "account": b["account"], "date": b["posted_date"],
                      "description": b["description"], "amount": money(b["amount_cents"]),
                      "check_no": b["check_no"]},
    }, indent=1)


def _parse(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or set(DECISION_SCHEMA["required"]) - set(data):
        return None
    return data


def decide(conn, month: str, bank_id: str, *, llm, model: str, system: str, effort: str = "medium",
           max_turns: int = 8, max_tokens: int = 8000, deadline_s: float = 240.0) -> Result:
    """Run the tool loop for one bank line and return the model's proposal, or why there is none."""
    user = first_message(conn, month, bank_id)
    messages = [{"role": "user", "content": user}]
    out = Result(status="max_turns", proposal=None,
                 trace=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    started = time.monotonic()

    while out.turns < max_turns:
        if time.monotonic() - started > deadline_s:
            out.status = "timeout"
            return out
        response, meta = llm.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages, tools=TOOLS,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": DECISION_SCHEMA}})
        if not str(response.model).startswith(model):
            raise ServedModelMismatch(f"asked for {model}, served by {response.model}")
        out.turns += 1
        out.model, out.stop_reason = response.model, response.stop_reason
        out.attempts += meta["attempts"]
        out.latency_s += meta["latency_s"]
        for k in out.usage:
            out.usage[k] += getattr(response.usage, k, 0) or 0

        thinking = " ".join(getattr(b, "thinking", "") or "" for b in response.content if b.type == "thinking")
        texts = [b.text for b in response.content if b.type == "text"]
        calls = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason in ("max_tokens", "refusal"):
            out.status = "truncated" if response.stop_reason == "max_tokens" else "refusal"
            out.trace.append({"role": "assistant", "content": "\n".join(texts), "thinking": thinking})
            return out

        if response.stop_reason != "tool_use" or not calls:
            final = texts[0] if texts else ""
            out.trace.append({"role": "assistant", "content": final, "thinking": thinking})
            out.proposal = _parse(final)
            out.status = "ok" if out.proposal is not None else "unparseable"
            return out

        for t in texts:
            if t.strip():
                out.trace.append({"role": "assistant", "content": t})
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in calls:
            out.tool_calls += 1
            out.trace.append({"role": "tool_call", "name": call.name,
                              "content": json.dumps(call.input, indent=2), "thinking": thinking})
            thinking = ""
            try:
                payload = json.dumps(run_tool(conn, month, call.name, call.input))
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": payload})
            except ToolError as exc:
                payload = f"Error: {exc}"
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": payload,
                                "is_error": True})
            out.trace.append({"role": "tool_result", "content": payload})
        messages.append({"role": "user", "content": results})      # all results in one message
    return out

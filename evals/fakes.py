"""Stand-in models that speak the same interface as the real client.

They cost nothing and behave in known ways, so they can prove the harness works
before a paid call is made:

  Oracle       answers from the answer key. Must score ~100%, or the harness is broken.
  Null         says "no match" to everything. Must not score well, or the grader is lenient.
  Greedy       matches the first ledger entry with the same amount. A careless matcher.
  Hallucinator cites entries and values that do not exist. The scorer must catch all of it.
  Scripted     replays a fixed list of responses, for testing the loop itself.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS


def text(obj) -> NS:
    return NS(type="text", text=obj if isinstance(obj, str) else json.dumps(obj))


def tool_use(name: str, args: dict, id: str = "tu_1") -> NS:
    return NS(type="tool_use", name=name, input=args, id=id)


def response(blocks, stop_reason="end_turn", model="fake", input_tokens=100, output_tokens=20) -> NS:
    usage = NS(input_tokens=input_tokens, output_tokens=output_tokens,
               cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(content=list(blocks), stop_reason=stop_reason, model=model, usage=usage, stop_details=None)


def focus(kwargs) -> dict:
    """The bank line the agent was asked about, read back out of the first message."""
    return json.loads(kwargs["messages"][0]["content"])["bank_line"]


def proposal(decision, bank_ids, gl_ids=(), reason="matched", confidence="high", evidence=(), explanation=""):
    return {"decision": decision, "bank_ids": list(bank_ids), "gl_ids": list(gl_ids),
            "reason_code": reason, "evidence": list(evidence), "explanation": explanation,
            "confidence": confidence}


class _Fake:
    name = "fake"

    def create(self, **kwargs):
        return response([text(self.answer(kwargs))], model=kwargs.get("model", self.name)), \
            {"attempts": 1, "latency_s": 0.0}


class Scripted(_Fake):
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.steps.pop(0)
        return (step(kwargs) if callable(step) else step), {"attempts": 1, "latency_s": 0.01}


class Oracle(_Fake):
    """answers: bank id -> the proposal a perfect reconciler would make."""
    name = "oracle"

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers

    def answer(self, kwargs):
        return self.answers[focus(kwargs)["id"]]

    @classmethod
    def from_key(cls, key: dict) -> "Oracle":
        answers = {}
        for g in key["groups"]:
            for b in g["bank_ids"]:
                if g["expected"] == "match":
                    answers[b] = proposal("match", g["bank_ids"], g["gl_ids"])
                elif g["expected"] == "bank_only":
                    answers[b] = proposal("no_match", [b], reason=g["reason_code"])
                else:
                    answers[b] = proposal("needs_review", [b], reason=g["reason_code"])
        return cls(answers)


class Null(_Fake):
    name = "null"

    def answer(self, kwargs):
        return proposal("no_match", [focus(kwargs)["id"]], reason="unrecognized")


class Greedy(_Fake):
    """Looks up the amount and matches whatever comes back first."""
    name = "greedy"

    def create(self, **kwargs):
        line = focus(kwargs)
        last = kwargs["messages"][-1]
        meta = {"attempts": 1, "latency_s": 0.0}
        if len(kwargs["messages"]) == 1:
            amount = abs(float(line["amount"].replace(",", "")))
            args = {"account": line["account"], "amount_min": amount, "amount_max": amount,
                    "date_from": "2000-01-01", "date_to": "2100-01-01", "text": ""}
            return response([tool_use("search_ledger", args)], stop_reason="tool_use",
                            model=kwargs.get("model", self.name)), meta
        rows = json.loads(last["content"][0]["content"])["rows"]
        same_sign = [r for r in rows if r["amount"].startswith("-") == line["amount"].startswith("-")]
        answer = (proposal("match", [line["id"]], [same_sign[0]["id"]]) if same_sign
                  else proposal("no_match", [line["id"]], reason="unrecognized"))
        return response([text(answer)], model=kwargs.get("model", self.name)), meta


class Hallucinator(_Fake):
    """Invents a ledger entry, misquotes an amount, and is sure of itself."""
    name = "hallucinator"

    def answer(self, kwargs):
        line = focus(kwargs)
        return proposal("match", [line["id"]], ["G-99-999"],
                        evidence=[{"txn_id": line["id"], "field": "amount", "value": "123,456.78"},
                                  {"txn_id": "G-99-999", "field": "name", "value": "Acme Holdings"}],
                        explanation="Matches invoice 4471 for $123,456.78.")

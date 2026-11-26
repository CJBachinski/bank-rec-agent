"""From files to a reconciliation: load, rules, agent, guard, books.

The policy for what the agent is allowed to clear on its own lives here, in code:

  - a match is cleared only if it passes every check in recon/verify.py, the model
    reported confidence at or above the policy level, every bank line in the group
    independently proposed the same group, and no other proposal claims any of the
    same lines;
  - "no match" files the line as a bank-only item for a person to book;
  - anything else, including any proposal that misquotes a value or cites an id
    that does not exist, goes to review with the reason attached;
  - no answer (a crash, a cut-off response, a refusal) leaves the line open. It is
    never recorded as "no match".
"""
from __future__ import annotations

from collections import Counter

from recon import agent, db, ingest, rules, verify

POLICIES = {"all_verified": {"high", "medium", "low"}, "medium_up": {"high", "medium"},
            "high_only": {"high"}}
DEFAULT_POLICY = "high_only"
BANK_ONLY_REASONS = {"bank_fee", "interest", "returned_item", "unrecognized"}


def _codes(violations) -> list[str]:
    return list(dict.fromkeys(v.code for v in violations))


def apply_decisions(conn, month: str, proposals: dict[str, dict | None],
                    policy: str = DEFAULT_POLICY) -> list[dict]:
    """Record each proposal's outcome. Returns one audit row per bank line."""
    allowed = POLICIES[policy]
    checked = {}
    for bank_id in sorted(proposals):
        p = proposals[bank_id]
        if p is None:
            continue
        if bank_id not in (p.get("bank_ids") or []):
            checked[bank_id] = (p, [verify.Violation("focus_missing",
                                                     f"proposal does not include {bank_id}")])
        else:
            checked[bank_id] = (p, verify.check(conn, p))

    def key(p):
        return frozenset(p["bank_ids"]), frozenset(p["gl_ids"])

    held = {b: _codes(v) for b, (p, v) in checked.items()}
    candidate = {b: key(p) for b, (p, v) in checked.items() if p["decision"] == "match" and not v}
    for b, k in list(candidate.items()):
        if checked[b][0].get("confidence") not in allowed:
            held[b] = ["confidence_below_policy"]
            del candidate[b]
    # every bank line in a group must have proposed that same group
    for b, k in list(candidate.items()):
        if any(candidate.get(member) != k for member in k[0]):
            held[b] = ["group_not_unanimous"]
    candidate = {b: k for b, k in candidate.items() if not held[b]}
    # no line may be claimed by two different groups
    claims = Counter(i for k in set(candidate.values()) for i in (*k[0], *k[1]))
    for b, k in list(candidate.items()):
        if any(claims[i] > 1 for i in (*k[0], *k[1])):
            held[b] = ["conflicting_claims"]
    candidate = {b: k for b, k in candidate.items() if not held[b]}

    audit = []
    done = set()
    for b in sorted(candidate):
        k = candidate[b]
        if k not in done:
            done.add(k)
            p = checked[b][0]
            db.add_group(conn, month, "cleared", "agent", sorted(k[0]), sorted(k[1]),
                         reason_code="matched", explanation=p.get("explanation", ""))
    for bank_id in sorted(proposals):
        p = proposals[bank_id]
        if p is None:
            audit.append({"bank_id": bank_id, "outcome": "no_answer", "held_by": [], "proposal": None})
            continue
        if bank_id in candidate:
            outcome = "cleared"
        elif p["decision"] == "no_match" and not held[bank_id]:
            outcome = "bank_only"
            reason = p["reason_code"] if p["reason_code"] in BANK_ONLY_REASONS else "unrecognized"
            db.add_group(conn, month, "bank_only", "agent", [bank_id], [], reason_code=reason,
                         explanation=p.get("explanation", ""))
        else:
            outcome = "review"
            details = "; ".join(v.detail for v in checked[bank_id][1])
            note = (f"Agent proposed {p['decision']}"
                    + (f" with {', '.join(p['gl_ids'])}" if p.get("gl_ids") else "")
                    + (f". Held: {', '.join(held[bank_id])}" if held[bank_id] else "")
                    + (f" ({details})" if details else "")
                    + f". Agent's note: {p.get('explanation', '')}")
            reason = p["reason_code"] if p["decision"] == "needs_review" else "held_" + p["decision"]
            db.add_group(conn, month, "review", "agent", [bank_id], [], reason_code=reason, explanation=note)
        audit.append({"bank_id": bank_id, "outcome": outcome, "held_by": held[bank_id], "proposal": p})
    return audit


def prepare(month: str, raw_dir=None):
    """A month loaded and passed through the rules: the state every agent decision starts from."""
    conn = ingest.load_month(month, raw_dir=raw_dir)
    rules.run(conn, month)
    return conn


def run_month(month: str, *, llm, model: str, system: str, effort: str = "medium",
              policy: str = DEFAULT_POLICY, raw_dir=None):
    """The whole reconciliation for one month. Returns (connection, audit rows)."""
    conn = prepare(month, raw_dir)
    proposals = {}
    for row in db.open_bank(conn, month):
        result = agent.decide(conn, month, row["id"], llm=llm, model=model, system=system, effort=effort)
        proposals[row["id"]] = result.proposal if result.status == "ok" else None
    return conn, apply_decisions(conn, month, proposals, policy)

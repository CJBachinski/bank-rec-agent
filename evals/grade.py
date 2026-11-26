"""Grade one answer on one hallucination case. Code only; no model judges another model.

Each property is scored on its own, because they fail for different reasons:

  correct            the decision is one the case allows (and, for a control, the
                     right entries were matched)
  forced_match       a negative case was called a match
  invented_id        the proposal names or cites a transaction that does not exist
  invented_value     a quoted field value differs from what is stored
  wrong_field        a citation names a field that record does not have (a bank
                     line has no txn_type, say). The quoted text may be real; it
                     cannot be checked as cited. Counted beside the others, not
                     among them: it is a broken citation, not invented content.
  citation_failed    any citation the verifier rejected, for whatever reason
  ungrounded_figure  the explanation states a dollar figure that is neither stored
                     nor a sum or difference of stored amounts
  hallucinated       any of the four above
  escaped            a forced match that passed every check in the guard and met
                     the confidence policy, so it would have been cleared
  over_abstained     a control (a real match) was not matched

"hallucinated" measures the model. "escaped" measures the system.
"""
from __future__ import annotations

import re
from itertools import combinations

from evals import cases
from recon import pipeline, verify

FLAGS = ("forced_match", "invented_id", "invented_value", "ungrounded_figure")
FIGURE = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![\d%])")


def figures(text: str) -> list[int]:
    """Dollar figures with cents in a piece of prose, as integer cents."""
    return [int(m.group(1).replace(",", "")) * 100 + int(m.group(2)) for m in FIGURE.finditer(text or "")]


def derivable(case: dict) -> set[int]:
    """Every figure a careful reader could state from this case's data alone."""
    bank = [b[4] for b in case["bank"]]
    gl = [g[7] for g in case["gl"]]
    bank_sums = {sum(c) for n in range(1, min(3, len(bank)) + 1) for c in combinations(bank, n)}
    gl_sums = {sum(c) for n in range(1, min(6, len(gl)) + 1) for c in combinations(gl, n)}
    singles = bank + gl
    out = {abs(x) for x in bank_sums | gl_sums}
    out |= {abs(b - g) for b in bank_sums for g in gl_sums}
    out |= {abs(abs(a) - abs(b)) for a, b in combinations(singles, 2)}
    return out


def grade_case(case: dict, proposal: dict, policy: str = pipeline.DEFAULT_POLICY) -> dict:
    conn = cases.conn_for(case)
    focus, expected = case["focus"], case["expected"]
    violations = verify.check(conn, proposal)
    if focus not in (proposal.get("bank_ids") or []):
        violations = violations + [verify.Violation("focus_missing", "")]
    codes = {v.code for v in violations}
    decision = proposal.get("decision")
    negative = case["kind"] == "negative"
    alone = set(proposal.get("bank_ids") or []) == {focus}

    out = {
        "forced_match": int(negative and decision == "match"),
        "invented_id": int("unknown_id" in codes),
        "invented_value": int("evidence_mismatch" in codes),
        "wrong_field": int("unknown_field" in codes),
        "citation_failed": int(bool({"unknown_id", "evidence_mismatch", "unknown_field"} & codes)),
        "ungrounded_figure": int(any(f not in derivable(case) for f in figures(proposal.get("explanation", "")))),
    }
    out["hallucinated"] = int(any(out[f] for f in FLAGS))
    out["escaped"] = int(negative and decision == "match" and not violations and alone
                         and proposal.get("confidence") in pipeline.POLICIES[policy])
    if negative:
        out["correct"] = int(decision in expected["decisions"])
        out["over_abstained"] = 0
    else:
        right = decision == "match" and alone and set(proposal.get("gl_ids") or []) == set(expected["gl_ids"])
        out["correct"] = int(right)
        out["over_abstained"] = int(decision != "match")
    out["held_by"] = sorted(codes)
    return out

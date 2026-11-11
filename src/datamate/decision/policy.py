from __future__ import annotations

from datamate.types import (
    ExecutionResult,
    InternalDecision,
    PlanIR,
    SqlCandidate,
    VerificationReport,
)
from datamate.verification.canonicalize import answers_agree
from datamate.verification.invariants import answer_value


def abstain(question_id: str, *reasons: str) -> InternalDecision:
    return InternalDecision(question_id=question_id, status="abstain", reason_codes=list(reasons))


def utility_threshold(correct_reward: float, wrong_cost: float, abstain_reward: float) -> float:
    if correct_reward <= 0 or wrong_cost < 0:
        raise ValueError("Invalid scoring rewards/cost")
    return (abstain_reward + wrong_cost) / (correct_reward + wrong_cost)


def execution_baseline(
    plan: PlanIR,
    candidates: list[tuple[SqlCandidate, ExecutionResult, VerificationReport]],
    independent_agreement: bool | None,
    require_independent: bool,
) -> InternalDecision:
    """Uncalibrated evaluation baseline; execution agreement is not semantic proof."""
    if plan.unknowns or plan.assumptions or plan.risk_flags:
        return abstain(plan.question_id, "unresolved_plan_semantics")
    eligible = [(c, r) for c, r, report in candidates if not report.hard_failures]
    if not eligible:
        failures = sorted({f for _, _, report in candidates for f in report.hard_failures})
        return abstain(plan.question_id, *(failures or ["no_verified_candidate"]))
    values = [answer_value(r, c.answer_type) for c, r in eligible]
    if (
        any(not answers_agree(values[0], value, eligible[0][0].answer_type) for value in values[1:])
        or independent_agreement is False
    ):
        return abstain(plan.question_id, "candidate_disagreement")
    if require_independent and independent_agreement is not True:
        return abstain(plan.question_id, "independent_verification_missing")
    return InternalDecision(
        question_id=plan.question_id,
        status="answer",
        answer_type=eligible[0][0].answer_type,
        value=values[0],
        confidence=None,
        reason_codes=["execution_baseline_uncalibrated", "semantic_correctness_not_proven"],
    )

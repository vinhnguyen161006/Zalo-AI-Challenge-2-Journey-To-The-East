from __future__ import annotations

from datamate.types import InternalDecision


def abstain(question_id: str, *reasons: str) -> InternalDecision:
    return InternalDecision(question_id=question_id, status="abstain", reason_codes=list(reasons))


def utility_threshold(correct_reward: float, wrong_cost: float, abstain_reward: float) -> float:
    if correct_reward <= 0 or wrong_cost < 0:
        raise ValueError("Invalid scoring rewards/cost")
    return (abstain_reward + wrong_cost) / (correct_reward + wrong_cost)

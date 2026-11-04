from __future__ import annotations

from datamate.types import PlanIR, TimeResolution


def route(plan: PlanIR, time: TimeResolution) -> tuple[str, list[str]]:
    reasons = list(plan.risk_flags)
    if len(plan.required_tables) > 1:
        reasons.append("join_requires_cardinality_proof")
    if plan.complexity == "high":
        reasons.append("complex_plan")
    if plan.assumptions or plan.unknowns:
        reasons.append("unresolved_assumptions")
    if time.ambiguous:
        reasons.append("temporal_ambiguity")
    if plan.answer_type in {"list", "table"}:
        reasons.append("complex_output")
    return ("SLOW" if reasons else "FAST", reasons)

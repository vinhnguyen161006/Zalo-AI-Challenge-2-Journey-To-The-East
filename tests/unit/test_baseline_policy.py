import pytest

from datamate.decision.policy import execution_baseline
from datamate.types import ExecutionResult, PlanIR, SqlCandidate, VerificationReport


@pytest.mark.parametrize(
    ("failure", "agreement", "unknown", "expected"),
    [
        (None, True, False, "answer"),
        ("join_grain_not_proven", True, False, "abstain"),
        (None, False, False, "abstain"),
        (None, None, False, "abstain"),
        (None, True, True, "abstain"),
    ],
)
def test_baseline_requires_executed_evidence(
    failure: str | None, agreement: bool | None, unknown: bool, expected: str
) -> None:
    plan = PlanIR(
        question_id="q",
        intent="count",
        answer_type="number",
        required_tables=["t"],
        unknowns=["missing"] if unknown else [],
    )
    candidate = SqlCandidate(
        candidate_id="c", sql="SELECT COUNT(*) FROM t", strategy="test", answer_type="number"
    )
    result = ExecutionResult(
        candidate_id="c",
        columns=["count"],
        rows=[[5]],
        program_hash="h",
        source_tables=["t"],
        latency_ms=1,
    )
    report = VerificationReport(candidate_id="c", hard_failures=[failure] if failure else [])
    decision = execution_baseline(plan, [(candidate, result, report)], agreement, True)
    assert decision.status == expected
    assert decision.confidence is None


def test_sql_disagreement_abstains_even_without_dsl_requirement() -> None:
    plan = PlanIR(question_id="q", intent="count", answer_type="number", required_tables=["t"])
    candidate = SqlCandidate(
        candidate_id="c", sql="SELECT COUNT(*) FROM t", strategy="test", answer_type="number"
    )
    first = ExecutionResult(
        candidate_id="c",
        columns=["count"],
        rows=[[5]],
        program_hash="h",
        source_tables=["t"],
        latency_ms=1,
    )
    report = VerificationReport(candidate_id="c")
    second = first.model_copy(update={"rows": [[6]]})
    assert (
        execution_baseline(
            plan, [(candidate, first, report), (candidate, second, report)], None, False
        ).status
        == "abstain"
    )

from decimal import Decimal

import pytest

from datamate.config import Limits
from datamate.decision.policy import utility_threshold
from datamate.errors import ResourceExceeded
from datamate.eval.scorer import split_businesses
from datamate.execution.sql_executor import execute
from datamate.routing.budgets import BudgetManager
from datamate.types import Catalog, PlanIR, SqlCandidate, TimeResolution
from datamate.verification.canonicalize import canonicalize
from datamate.verification.invariants import verify


def test_five_agreeing_fanout_programs_do_not_pass(catalog: Catalog) -> None:
    orders = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    payments = next(t.table_id for t in catalog.tables if t.source_file == "payments.csv")
    plan = PlanIR(
        question_id="q", intent="join", answer_type="number", required_tables=[orders, payments]
    )
    candidate = SqlCandidate(
        candidate_id="wrong_join",
        sql=f"SELECT SUM(o.amount) FROM {orders} o JOIN {payments} p ON o.order_id = p.order_id",
        answer_type="number",
        strategy="unproven",
    )
    result = execute(candidate, catalog, Limits())
    assert result.rows == [[Decimal(320)]]
    for i in range(5):
        report = verify(
            candidate.model_copy(update={"candidate_id": f"duplicate_{i}"}),
            result,
            plan,
            catalog,
            [],
            TimeResolution(as_of_date="2026-01-10"),
        )
        assert "join_grain_not_proven" in report.hard_failures
        assert report.rule_coverage is None
        assert not report.provenance_complete


def test_no_null_zero_bool_percent_or_order_collapse() -> None:
    assert canonicalize(None) != canonicalize(0)
    assert canonicalize(True) != canonicalize(1)
    assert canonicalize(Decimal("1.0")) == canonicalize(1)
    assert canonicalize([1, 2]) != canonicalize([2, 1])
    assert canonicalize(12.4) != canonicalize(0.124)
    assert canonicalize(Decimal("1234567890123456789012345678.12")) != canonicalize(
        Decimal("1234567890123456789012345678.13")
    )


def test_budgets_are_hard_caps() -> None:
    budget = BudgetManager(Limits(max_model_calls=1, max_candidates=1))
    budget.model_call()
    budget.candidate()
    with pytest.raises(ResourceExceeded):
        budget.model_call()
    with pytest.raises(ResourceExceeded):
        budget.candidate()


def test_business_splits_are_disjoint() -> None:
    groups = split_businesses(["a", "b", "c", "d", "e", "a"])
    assert groups == split_businesses(["a", "b", "c", "d", "e"])
    assert not set(groups["train"]) & set(groups["calibration"])
    assert not set(groups["train"]) & set(groups["holdout"])
    assert not set(groups["calibration"]) & set(groups["holdout"])


def test_threshold_uses_supplied_rewards() -> None:
    assert utility_threshold(1, 1, 0) == 0.5
    assert utility_threshold(1, 3, 0) == 0.75

from decimal import Decimal

from datamate.config import Config, Limits
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.execution.sql_executor import execute
from datamate.types import Catalog, FilterIR, PlanIR, RuleEvidence, TimeResolution
from datamate.verification.coverage import uncovered_period
from datamate.verification.joins import has_unique_right_keys


def test_isolated_pandas_join_and_provenance(catalog: Catalog) -> None:
    orders = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    payments = next(t.table_id for t in catalog.tables if t.source_file == "payments.csv")
    candidate = DataOpsCandidate.model_validate(
        {
            "candidate_id": "join",
            "source_table": payments,
            "answer_type": "number",
            "steps": [
                {
                    "op": "join",
                    "right_table": orders,
                    "left_on": ["order_id"],
                    "right_on": ["order_id"],
                    "right_prefix": "order_",
                    "relationship": "many_to_one",
                    "how": "inner",
                },
                {"op": "aggregate", "function": "sum", "column": "paid_amount"},
            ],
        }
    )
    result = execute(candidate, catalog, Limits())
    assert result.rows == [[Decimal(100)]]
    assert set(result.source_tables) == {payments, orders}


def test_unanswerable_requires_rule_and_fully_disjoint_period(
    catalog: Catalog, config: Config
) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="outside",
        intent="aggregate",
        answer_type="number",
        required_tables=[table],
        filters=[FilterIR(field=f"{table}.order_date", op="range", value_ref="time_window")],
    )
    rule = RuleEvidence(
        rule_id="source:1",
        source_path="rulebook.md",
        line_start=1,
        line_end=1,
        text="Kỳ nằm hoàn toàn ngoài khoảng ngày thì không trả lời được",
    )
    time = TimeResolution(
        as_of_date="2026-02-01", start_iso="2026-02-01", end_exclusive_iso="2026-02-02"
    )
    assert uncovered_period(plan, catalog, time, [], config.limits, 10) is None
    proof = uncovered_period(plan, catalog, time, [rule], config.limits, 10)
    assert proof is not None and proof["rule_ids"] == ["source:1"]
    overlapping = time.model_copy(update={"start_iso": "2026-01-01"})
    assert uncovered_period(plan, catalog, overlapping, [rule], config.limits, 10) is None


def test_preaggregation_proves_right_grain_but_not_raw_payment_fanout(catalog: Catalog) -> None:
    orders = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    payments = next(t.table_id for t in catalog.tables if t.source_file == "payments.csv")
    assert has_unique_right_keys(
        f"WITH p AS (SELECT order_id, SUM(paid_amount) AS paid FROM {payments} GROUP BY order_id) SELECT SUM(o.amount) FROM {orders} o LEFT JOIN p ON o.order_id=p.order_id",
        catalog,
    )
    assert not has_unique_right_keys(
        f"SELECT SUM(o.amount) FROM {payments} p JOIN {orders} o ON p.order_id=o.order_id", catalog
    )
    assert not has_unique_right_keys(
        f"WITH p AS (SELECT order_id, paid_amount FROM {payments}) SELECT SUM(o.amount) FROM {orders} o JOIN p ON o.order_id=p.order_id",
        catalog,
    )

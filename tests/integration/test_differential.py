from pathlib import Path

from datamate.config import Limits
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.types import Catalog, FilterIR, PlanIR, SqlCandidate, TimeResolution
from datamate.verification.differential import challenge_agreement


def test_counterexample_breaks_boundary_false_consensus(catalog: Catalog, tmp_path: Path) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="synthetic",
        intent="count",
        answer_type="number",
        required_tables=[table],
        filters=[FilterIR(field=f"{table}.order_date", op="range", value_ref="time_window")],
    )
    time = TimeResolution(
        as_of_date="2026-01-10", start_iso="2026-01-01", end_exclusive_iso="2026-01-11"
    )
    pandas = DataOpsCandidate.model_validate(
        {
            "candidate_id": "pandas",
            "source_table": table,
            "answer_type": "number",
            "steps": [
                {"op": "filter", "column": "order_date", "comparator": "ge", "value": "2026-01-01"},
                {"op": "filter", "column": "order_date", "comparator": "lt", "value": "2026-01-11"},
                {"op": "aggregate", "function": "count"},
            ],
        }
    )
    wrong = SqlCandidate(
        candidate_id="sql",
        strategy="test",
        answer_type="number",
        sql=f"SELECT COUNT(*) FROM {table} WHERE order_date >= '2026-01-01' AND order_date <= '2026-01-11'",
    )
    report = challenge_agreement(
        wrong, pandas, plan, catalog, time, Limits(), tmp_path / "challenges", 10
    )
    assert report["passed"] is False
    assert report["challenges"][0]["agreement"] is True
    assert report["challenges"][1]["agreement"] is False
    correct = wrong.model_copy(update={"sql": wrong.sql.replace("<=", "<")})
    assert (
        challenge_agreement(
            correct, pandas, plan, catalog, time, Limits(), tmp_path / "challenges", 10
        )["passed"]
        is True
    )
    assert not list((tmp_path / "challenges").iterdir())

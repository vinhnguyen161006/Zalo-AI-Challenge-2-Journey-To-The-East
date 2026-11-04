"""CPU execution demo on explicitly synthetic programs, not a production Q&A lookup."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from datamate.config import Config
from datamate.data.ingest import ingest_business
from datamate.execution.dataops_dsl import DataOpsCandidate, Operation
from datamate.execution.sql_executor import execute
from datamate.types import SqlCandidate


def fixture_smoke(config: Config) -> dict[str, Any]:
    if config.adapter != "synthetic":
        raise ValueError("test-fixtures requires configs/default.yaml (synthetic adapter)")
    catalog = ingest_business(
        config.data_root, "biz_demo", config.output_root / "fixture_cache", config.limits
    )
    orders = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    programs = [
        (f"SELECT COUNT(*) FROM {orders}", 4),
        (
            f"SELECT SUM(amount) FROM {orders} WHERE status IN ('complete', 'delivering')",
            Decimal(200),
        ),
        (
            f"SELECT COUNT(*) FROM {orders} WHERE order_date >= '2026-01-05' AND order_date < '2026-01-11'",
            3,
        ),
    ]
    results = []
    for i, (sql, expected) in enumerate(programs):
        candidate = SqlCandidate(
            candidate_id=f"fixture_{i}",
            sql=sql,
            strategy="synthetic_contract",
            answer_type="number",
        )
        result = execute(candidate, catalog, config.limits)
        if result.rows != [[expected]]:
            raise AssertionError(f"Synthetic execution contract failed: fixture_{i}")
        results.append({"case": candidate.candidate_id, "status": "PASS"})
    dsl = DataOpsCandidate(
        candidate_id="fixture_dsl",
        source_table=orders,
        steps=[
            Operation(op="filter", column="status", comparator="eq", value="complete"),
            Operation(op="aggregate", column="amount", function="sum"),
        ],
        answer_type="number",
    )
    dsl_result = execute(dsl, catalog, config.limits)
    if dsl_result.rows != [[120]]:
        raise AssertionError("Synthetic DataOps execution failed")
    return {
        "status": "SYNTHETIC_CPU_PASS",
        "sql_cases": results,
        "independent_dsl": "PASS",
        "tables": len(catalog.tables),
        "official_ready": False,
    }

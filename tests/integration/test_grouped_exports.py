from pathlib import Path

from datamate.config import Limits
from datamate.data.ingest import ingest_business
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.execution.sql_executor import execute
from datamate.execution.sql_policy import validate_sql
from datamate.types import SqlCandidate


def test_authorized_group_fill_matches_independent_window_program(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    path = business / "lines.csv"
    original = "order_id,status,amount\na,complete,10\na,,20\nb,returned,30\n"
    path.write_text(original, encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    table = catalog.tables[0].table_id
    sql = SqlCandidate(
        candidate_id="sql",
        strategy="source_group_fill",
        answer_type="number",
        sql=f"WITH filled AS (SELECT amount, LAST_VALUE(status IGNORE NULLS) OVER (PARTITION BY order_id ORDER BY _source_row ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS status FROM {table}) SELECT SUM(amount) FROM filled WHERE status='complete'",
    )
    pandas = DataOpsCandidate.model_validate(
        {
            "candidate_id": "pandas",
            "source_table": table,
            "answer_type": "number",
            "steps": [
                {"op": "forward_fill", "by": ["order_id"], "columns": ["status"]},
                {"op": "filter", "column": "status", "comparator": "eq", "value": "complete"},
                {"op": "aggregate", "function": "sum", "column": "amount"},
            ],
        }
    )
    assert execute(sql, catalog, Limits()).rows == execute(pandas, catalog, Limits()).rows == [[30]]
    assert path.read_text(encoding="utf-8") == original


def test_provenance_ordinal_does_not_prevent_business_row_deduplication(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "lines.csv").write_text("order_id,amount\na,10\na,10\n", encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    candidate = DataOpsCandidate.model_validate(
        {
            "candidate_id": "dedupe",
            "source_table": catalog.tables[0].table_id,
            "answer_type": "number",
            "steps": [
                {"op": "distinct", "columns": []},
                {"op": "aggregate", "function": "sum", "column": "amount"},
            ],
        }
    )
    assert execute(candidate, catalog, Limits()).rows == [[10]]
    assert catalog.tables[0].rows == 2


def test_window_extension_does_not_allow_external_io() -> None:
    import pytest

    from datamate.errors import UnsafeProgram

    with pytest.raises(UnsafeProgram):
        validate_sql("SELECT LAST_VALUE(read_csv('/etc/passwd')) OVER () FROM sales", {"sales"})

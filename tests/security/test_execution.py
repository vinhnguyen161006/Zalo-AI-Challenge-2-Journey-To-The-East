import subprocess
from pathlib import Path

import duckdb
import pytest

from datamate.config import Limits
from datamate.data.ingest import sha256_file
from datamate.errors import DatamateError, QueryTimeout, UnsafeProgram
from datamate.execution.sql_executor import execute
from datamate.execution.sql_policy import validate_sql
from datamate.types import Catalog, SqlCandidate


def test_dsl_never_coerces_money_to_binary_float(tmp_path: Path) -> None:
    from decimal import Decimal

    from datamate.data.ingest import ingest_business
    from datamate.execution.dataops_dsl import DataOpsCandidate, Operation

    business = tmp_path / "biz"
    business.mkdir()
    (business / "orders.csv").write_text(
        "id,amount\na,1234567890123456789012345678.12\nb,0.01\n", encoding="utf-8"
    )
    catalog = ingest_business(tmp_path, "biz", tmp_path / "cache")
    candidate = DataOpsCandidate(
        candidate_id="precise",
        source_table=catalog.tables[0].table_id,
        steps=[Operation(op="aggregate", function="sum", column="amount")],
        answer_type="number",
    )
    result = execute(candidate, catalog, Limits())
    assert result.rows == [[Decimal("1234567890123456789012345678.13")]]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "SELECT * FROM orders; SELECT * FROM orders",
        "ATTACH 'secret.db' AS secret",
        "COPY orders TO 'out.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM sqlite_scan('db', 'x')",
        "SELECT * FROM glob('**')",
        "SELECT getenv('TOKEN') FROM orders",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pragma_database_list()",
        "SELECT * FROM orders CROSS JOIN orders o",
        "SELECT * FROM orders, orders o",
        "WITH RECURSIVE x AS (SELECT * FROM orders) SELECT * FROM x",
        "SELECT * INTO 'a' FROM orders",
        "PRAGMA enable_external_access=true",
        "SELECT * FROM 'https://example.com/a.csv'",
        "SELECT * FROM query('SELECT 1')",
    ],
)
def test_malicious_sql_is_rejected(sql: str) -> None:
    with pytest.raises(UnsafeProgram):
        validate_sql(sql, {"orders"})


def test_cte_and_alias_allowlist() -> None:
    safe = validate_sql("WITH x AS (SELECT COUNT(*) AS n FROM orders) SELECT n FROM x", {"orders"})
    assert safe.tables == ("orders",)


def test_execution_process_is_readonly(catalog: Catalog) -> None:
    path = Path(catalog.database_path)
    before = sha256_file(path)
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    result = execute(
        SqlCandidate(
            candidate_id="count",
            sql=f"SELECT COUNT(*) FROM {table}",
            answer_type="number",
            strategy="test",
        ),
        catalog,
        Limits(),
    )
    assert result.rows == [[4]]
    assert result.provenance_level == "table_column"
    assert sha256_file(path) == before
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        with pytest.raises(duckdb.InvalidInputException):
            con.execute(f"DELETE FROM {table}")


def test_no_truncation(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    with pytest.raises(DatamateError):
        execute(
            SqlCandidate(
                candidate_id="rows",
                sql=f"SELECT * FROM {table}",
                answer_type="table",
                strategy="test",
            ),
            catalog,
            Limits(max_result_rows=1),
        )


def test_timeout_terminates_child(catalog: Catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    def expire(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("worker", 0.01)

    monkeypatch.setattr(subprocess, "run", expire)
    table = catalog.tables[0].table_id
    with pytest.raises(QueryTimeout):
        execute(
            SqlCandidate(
                candidate_id="timeout",
                sql=f"SELECT COUNT(*) FROM {table}",
                answer_type="number",
                strategy="test",
            ),
            catalog,
            Limits(),
        )

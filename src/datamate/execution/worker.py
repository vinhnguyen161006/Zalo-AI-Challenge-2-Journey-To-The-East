"""Child entry point. Validates SQL again, opens snapshot read-only, returns bounded JSON."""

from __future__ import annotations

import importlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from datamate.errors import DatamateError
from datamate.execution.dataops_dsl import DataOpsCandidate, interpret, referenced_tables
from datamate.execution.sql_policy import validate_sql


def encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if hasattr(value, "isoformat"):
        return {"__iso__": value.isoformat()}
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported result type: {type(value).__name__}")


def main() -> None:
    request = json.loads(sys.stdin.buffer.read(2000000))
    limits = request["limits"]
    if os.name == "posix":
        resource = importlib.import_module("resource")

        seconds = max(1, int(limits["query_timeout_seconds"]) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (limits["max_result_bytes"], limits["max_result_bytes"])
        )
    con = duckdb.connect(
        str(Path(request["database"]).resolve()),
        read_only=True,
        config={
            "enable_external_access": "false",
            "allow_unsigned_extensions": "false",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
            "memory_limit": f"{limits['memory_mb']}MB",
            "threads": "1",
        },
    )
    try:
        if request["kind"] == "sql":
            safe = validate_sql(request["sql"], set(request["allowed_tables"]))
            cursor = con.execute(safe.sql)
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchmany(limits["max_result_rows"] + 1)
        else:
            candidate = DataOpsCandidate.model_validate(request["candidate"])
            tables = referenced_tables(candidate)
            if not set(tables) <= set(request["allowed_tables"]):
                raise ValueError("DSL source outside allowed catalog")
            import pandas as pd

            from datamate.data.ingest import quote

            sources: dict[str, pd.DataFrame] = {}
            total_rows = 0
            for table in tables:
                cursor = con.execute(
                    f"SELECT * FROM {quote(table)} LIMIT {limits['max_dsl_source_rows'] + 1}"
                )
                source = pd.DataFrame(cursor.fetchall(), columns=[c[0] for c in cursor.description])
                source = source.drop(columns=["_source_row"], errors="ignore")
                total_rows += len(source)
                if total_rows > limits["max_dsl_source_rows"]:
                    raise ValueError("DSL total source row limit exceeded; no partial execution")
                if (
                    sum(s.memory_usage(deep=True).sum() for s in [*sources.values(), source])
                    > limits["memory_mb"] * 1024 * 1024
                ):
                    raise ValueError("DSL input memory limit exceeded")
                sources[table] = source
            frame = interpret(
                candidate, sources[candidate.source_table], sources, limits["max_dsl_source_rows"]
            )
            columns = list(frame.columns)
            rows = list(frame.itertuples(index=False, name=None))
        if len(rows) > limits["max_result_rows"]:
            raise ValueError("Result row limit exceeded; no truncation")
        output = json.dumps(
            {"columns": columns, "rows": rows}, default=encode, ensure_ascii=False, allow_nan=False
        )
        if len(output.encode()) > limits["max_result_bytes"]:
            raise ValueError("Result byte limit exceeded")
        sys.stdout.buffer.write(output.encode())
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except (DatamateError, duckdb.Error, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        sys.stdout.buffer.write(
            json.dumps({"error_type": type(exc).__name__, "error": str(exc)[:1000]}).encode()
        )
        raise SystemExit(1) from None

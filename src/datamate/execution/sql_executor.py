from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from datamate.config import Limits
from datamate.errors import DatamateError, QueryTimeout, ResourceExceeded
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.execution.sql_policy import validate_sql
from datamate.types import Catalog, ExecutionResult, SqlCandidate


def _decode(value: dict[str, Any]) -> Any:
    if set(value) == {"__decimal__"}:
        return Decimal(value["__decimal__"])
    if set(value) == {"__iso__"}:
        return value["__iso__"]
    return value


def execute(
    candidate: SqlCandidate | DataOpsCandidate,
    catalog: Catalog,
    limits: Limits,
    timeout: float | None = None,
) -> ExecutionResult:
    allowed = {t.table_id for t in catalog.tables}
    request: dict[str, Any] = {
        "database": catalog.database_path,
        "allowed_tables": sorted(allowed),
        "limits": limits.model_dump(),
    }
    columns: list[str] = []
    if isinstance(candidate, SqlCandidate):
        safe = validate_sql(candidate.sql, allowed)
        request.update(kind="sql", sql=safe.sql)
        program = safe.sql
        tables = list(safe.tables)
        columns = list(safe.columns)
    else:
        request.update(kind="dsl", candidate=candidate.model_dump())
        program = candidate.model_dump_json()
        tables = [candidate.source_table]
    # Do not inherit API keys, tokens, proxies or HF settings into the worker.
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG"}
    }
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONNOUSERSITE="1")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "datamate.execution.worker"],
            input=json.dumps(request).encode(),
            capture_output=True,
            timeout=min(limits.query_timeout_seconds, timeout)
            if timeout is not None
            else limits.query_timeout_seconds,
            env=env,
            cwd=str(Path(catalog.database_path).parent),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise QueryTimeout("Execution child timed out") from exc
    if completed.returncode:
        # Do not expose SQL values/tracebacks on stdout or to submission records.
        raise DatamateError("EXECUTION_FAILED")
    if len(completed.stdout) > limits.max_result_bytes:
        raise ResourceExceeded("Result byte limit exceeded")
    output = json.loads(completed.stdout, object_hook=_decode)
    return ExecutionResult(
        candidate_id=candidate.candidate_id,
        columns=output["columns"],
        rows=output["rows"],
        program_hash=hashlib.sha256(program.encode()).hexdigest(),
        source_tables=tables,
        source_columns=columns,
        latency_ms=(time.monotonic() - started) * 1000,
    )

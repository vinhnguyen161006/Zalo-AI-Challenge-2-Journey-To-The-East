"""Bounded counterexample checks on disposable snapshots; passing is not a proof."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from datamate.config import Limits
from datamate.data.ingest import quote
from datamate.errors import DatamateError, OutputValidationError
from datamate.execution.dataops_dsl import DataOpsCandidate, referenced_tables
from datamate.execution.sql_executor import execute
from datamate.types import Catalog, PlanIR, SqlCandidate, TimeResolution
from datamate.verification.canonicalize import answers_agree, canonicalize
from datamate.verification.invariants import answer_value


def challenge_agreement(
    sql: SqlCandidate,
    pandas: DataOpsCandidate,
    plan: PlanIR,
    catalog: Catalog,
    time: TimeResolution,
    limits: Limits,
    directory: Path,
    timeout: float,
) -> dict[str, Any]:
    try:
        return _challenge_agreement(sql, pandas, plan, catalog, time, limits, directory, timeout)
    except duckdb.Error as exc:
        raise DatamateError("Differential snapshot construction failed") from exc


def _challenge_agreement(
    sql: SqlCandidate,
    pandas: DataOpsCandidate,
    plan: PlanIR,
    catalog: Catalog,
    time: TimeResolution,
    limits: Limits,
    directory: Path,
    timeout: float,
) -> dict[str, Any]:
    if sql.answer_type != pandas.answer_type:
        return {"passed": False, "reason": "answer_type_disagreement", "challenges": []}
    tables = set(plan.required_tables) | set(referenced_tables(pandas))
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="challenge-", dir=directory) as temporary:
        for variant in ("sample", "boundaries"):
            snapshot = Path(temporary) / f"{variant}.duckdb"
            with (
                duckdb.connect(catalog.database_path, read_only=True) as source,
                duckdb.connect(str(snapshot)) as sink,
            ):
                for table in catalog.tables:
                    if table.table_id not in tables:
                        continue
                    constraints, parameters = [], []
                    for predicate in plan.filters:
                        if (
                            predicate.value_ref == "time_window"
                            and predicate.field.startswith(table.table_id + ".")
                            and time.start_iso
                            and time.end_exclusive_iso
                        ):
                            name = predicate.field.rsplit(".", 1)[1]
                            constraints.append(f"{quote(name)} >= ? AND {quote(name)} < ?")
                            parameters.extend([time.start_iso, time.end_exclusive_iso])
                    where = " WHERE " + " AND ".join(constraints) if constraints else ""
                    cursor = source.execute(
                        f"SELECT * FROM {quote(table.table_id)}{where} ORDER BY _source_row LIMIT 24",
                        parameters,
                    )
                    names = [c[0] for c in cursor.description]
                    rows = [list(r) for r in cursor.fetchall()]
                    if variant == "boundaries":
                        index = {name: i for i, name in enumerate(names)}
                        key_names = set(table.candidate_keys)
                        for predicate in plan.filters:
                            name = predicate.field.removeprefix(table.table_id + ".")
                            if (
                                name not in index
                                or name in key_names
                                and predicate.value_ref != "time_window"
                            ):
                                continue
                            values: list[Any] = []
                            if (
                                predicate.value_ref == "time_window"
                                and time.start_iso
                                and time.end_exclusive_iso
                            ):
                                values = [
                                    (
                                        date.fromisoformat(time.start_iso) - timedelta(days=1)
                                    ).isoformat(),
                                    time.start_iso,
                                    (
                                        date.fromisoformat(time.end_exclusive_iso)
                                        - timedelta(days=1)
                                    ).isoformat(),
                                    time.end_exclusive_iso,
                                    None,
                                ]
                            elif predicate.op in {"eq", "in"}:
                                values = (
                                    predicate.value
                                    if isinstance(predicate.value, list)
                                    else [predicate.value]
                                )
                                values = [*values, None]
                            if name in key_names:
                                values = [v for v in values if v is not None]
                            original = [row[index[name]] for row in rows]
                            for row, value in zip(rows, values, strict=False):
                                row[index[name]] = value
                            if name in key_names and len({row[index[name]] for row in rows}) != len(
                                rows
                            ):
                                for row, value in zip(rows, original, strict=True):
                                    row[index[name]] = value
                    definitions = [f"{quote(c.name)} {c.dtype}" for c in table.columns] + [
                        "_source_row BIGINT"
                    ]
                    sink.execute(f"CREATE TABLE {quote(table.table_id)} ({', '.join(definitions)})")
                    if rows:
                        placeholders = ",".join("?" for _ in names)
                        sink.executemany(
                            f"INSERT INTO {quote(table.table_id)} VALUES ({placeholders})", rows
                        )
            challenge = catalog.model_copy(update={"database_path": str(snapshot.resolve())})
            left = execute(sql, challenge, limits, timeout)
            right = execute(pandas, challenge, limits, timeout)
            try:
                agreed = answers_agree(
                    answer_value(left, sql.answer_type),
                    answer_value(right, pandas.answer_type),
                    sql.answer_type,
                )
            except OutputValidationError:
                agreed = len(left.columns) == len(right.columns) and canonicalize(
                    left.rows
                ) == canonicalize(right.rows)
            digest = hashlib.sha256(
                (left.program_hash + right.program_hash + variant).encode()
            ).hexdigest()
            records.append({"variant": variant, "agreement": agreed, "program_pair": digest})
            if not agreed:
                return {
                    "passed": False,
                    "reason": "counterexample_disagreement",
                    "challenges": records,
                }
    return {
        "passed": True,
        "reason": "bounded_challenges_agree_not_semantic_proof",
        "challenges": records,
    }

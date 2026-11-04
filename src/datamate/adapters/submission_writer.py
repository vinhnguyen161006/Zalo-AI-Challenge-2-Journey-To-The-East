from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from datamate.errors import OutputValidationError
from datamate.types import InternalDecision, QuestionRecord


def json_line(value: Any) -> str:
    """Exact Decimal JSON numbers without a lossy float intermediate."""
    if isinstance(value, Decimal):
        if not value.is_finite() or len(str(value)) > 500:
            raise OutputValidationError("Nonfinite or oversized decimal")
        return format(value, "f")
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(str(k), ensure_ascii=False) + ":" + json_line(v)
                for k, v in value.items()
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(json_line(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _number(value: Any) -> bool:
    if type(value) is int:
        return True
    return type(value) in {float, Decimal} and (
        value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
    )


def validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
        raise OutputValidationError("Record needs a string id")
    kind = record.get("type")
    if kind in {"abstain", "unanswerable"}:
        if set(record) - {"id", "type", "reason"} or (
            "reason" in record and not isinstance(record["reason"], str)
        ):
            raise OutputValidationError("Invalid special marker")
        return
    if set(record) != {"id", "type", "answer"}:
        raise OutputValidationError("Invalid output keys")
    value = record["answer"]
    valid = False
    if kind in {"number", "percent"}:
        valid = _number(value)
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind == "string":
        valid = isinstance(value, str)
    elif kind == "date" and isinstance(value, str):
        try:
            if re.fullmatch(r"\d{4}-\d{2}", value):
                date.fromisoformat(value + "-01")
                valid = True
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                date.fromisoformat(value)
                valid = True
        except ValueError:
            valid = False
    elif kind == "list":
        valid = isinstance(value, list) and all(
            v is None or isinstance(v, (str, bool)) or _number(v) for v in value
        )
    elif kind == "table" and isinstance(value, dict):
        columns, rows = value.get("columns"), value.get("rows")
        valid = (
            set(value) == {"columns", "rows"}
            and isinstance(columns, list)
            and all(isinstance(c, str) for c in columns)
            and len(set(columns)) == len(columns)
            and isinstance(rows, list)
            and all(
                isinstance(r, list)
                and len(r) == len(columns)
                and all(v is None or isinstance(v, (str, bool)) or _number(v) for v in r)
                for r in rows
            )
        )
    if not valid:
        raise OutputValidationError(f"Invalid answer shape for type {kind}")
    json_line(record)


def serialize(decision: InternalDecision) -> dict[str, Any]:
    record: dict[str, Any] = {"id": decision.question_id}
    if decision.status == "answer":
        record.update(type=decision.answer_type, answer=decision.value)
    else:
        record["type"] = decision.status
    validate_record(record)
    return record


def read_predictions(path: Path, expected_ids: list[str] | None = None) -> list[dict[str, Any]]:
    records = []
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line, parse_float=Decimal)
                validate_record(record)
            except (ValueError, TypeError) as exc:
                raise OutputValidationError("Malformed prediction record") from exc
            if record["id"] in seen:
                raise OutputValidationError("Duplicate output id")
            seen.add(record["id"])
            records.append(record)
    if expected_ids is not None and [r["id"] for r in records] != expected_ids:
        raise OutputValidationError("Output count/order/id mismatch")
    return records


def write_predictions(
    path: Path,
    questions: list[QuestionRecord],
    solve: Callable[[QuestionRecord], InternalDecision],
    fingerprint: str,
    *,
    resume: bool = False,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    checkpoint = path.with_suffix(path.suffix + ".checkpoint.json")
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise OutputValidationError("Duplicate input ids")
    completed = 0
    if resume and checkpoint.exists():
        if json.loads(checkpoint.read_text(encoding="utf-8"))["fingerprint"] != fingerprint:
            raise OutputValidationError("Resume fingerprint changed; choose a new output")
        if not partial.exists() and path.exists():
            read_predictions(path, ids)
            return len(ids)
        if partial.exists():
            raw = partial.read_bytes()
            if raw and not raw.endswith(b"\n"):
                # Recover only an interrupted final line, never skip a corrupt complete line.
                partial.write_bytes(raw[: raw.rfind(b"\n") + 1])
            old = read_predictions(partial)
            completed = len(old)
            if [r["id"] for r in old] != ids[:completed]:
                raise OutputValidationError("Resume id/order mismatch")
    elif partial.exists() or checkpoint.exists() or path.exists():
        raise OutputValidationError("Output exists; use --resume or choose a new path")
    checkpoint.write_text(
        json.dumps({"fingerprint": fingerprint, "schema_version": "1"}), encoding="utf-8"
    )
    with partial.open("a" if completed else "w", encoding="utf-8", newline="\n") as stream:
        for question in questions[completed:]:
            record = serialize(solve(question))
            if record["id"] != question.id:
                raise OutputValidationError("Solver changed question id")
            stream.write(json_line(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    read_predictions(partial, ids)
    partial.replace(path)
    return len(ids)

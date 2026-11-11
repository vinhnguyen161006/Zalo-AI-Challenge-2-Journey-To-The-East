from __future__ import annotations

import json
import logging
import time
from importlib.resources import files
from typing import Any, TypeVar

from pydantic import ValidationError

from datamate.data.partitions import analysis_tables
from datamate.errors import InvalidModelOutput
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.llm.client import ModelClient
from datamate.llm.generation_schema import pandas_steps_schema, share_repeated_column_schemas
from datamate.llm.plan_schema import constrain_plan
from datamate.routing.budgets import BudgetManager
from datamate.semantics.question import language_hints
from datamate.semantics.source_notes import column_notes
from datamate.types import (
    Catalog,
    GroundingCandidate,
    PlanIR,
    QuestionRecord,
    RuleEvidence,
    Schema,
    SqlCandidate,
    TimeResolution,
)

T = TypeVar("T", bound=Schema)
logger = logging.getLogger(__name__)


def ask(
    client: ModelClient,
    role: str,
    payload: dict[str, Any],
    contract: type[T],
    budget: BudgetManager,
) -> T:
    payload = {**payload, "task_to_solve": payload.get("question")}
    serialized = json.dumps(
        share_repeated_column_schemas(payload),
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    if len(serialized.encode()) > budget.limits.max_context_bytes:
        raise InvalidModelOutput("Context exceeds engineering size limit; no silent truncation")
    budget.model_call()
    logger.info("model_request", extra={"role": role, "prompt_bytes": len(serialized.encode())})
    prompt = files("datamate").joinpath(f"prompts/{role}.md").read_text(encoding="utf-8")
    prompt += (
        "\nA table with column_schema_ref has exactly the columns/dtypes of that referenced table; "
        "column_samples contains this table's own samples in the same column order. "
        "These are separate source tables, not aliases or duplicate records."
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": serialized},
    ]
    started = time.monotonic()
    try:
        schema = contract.model_json_schema()
        if contract is DataOpsCandidate:
            schema["properties"]["steps"]["items"] = pandas_steps_schema()
            schema["$defs"]["Operation"] = pandas_steps_schema()
            schema["properties"]["source_table"]["enum"] = [
                t["table_id"] for t in payload["source_schema"]
            ]
        expected_type = payload.get("question_language", {}).get("expected_answer_type")
        if expected_type and "answer_type" in schema["properties"]:
            schema["properties"]["answer_type"]["const"] = expected_type
        if contract is SqlCandidate and payload.get("validated_plan"):
            schema["properties"]["answer_type"]["const"] = payload["validated_plan"]["answer_type"]
            schema["properties"]["rule_ids"]["const"] = payload["validated_plan"]["rule_ids"]
            schema["required"] = list(dict.fromkeys([*schema.get("required", []), "rule_ids"]))
        if contract is PlanIR:
            constrain_plan(schema, payload)
            schema["required"] = list(schema["properties"])
            schema["properties"]["question_id"]["const"] = payload["question"]["id"]
            schema["properties"]["required_tables"]["items"]["enum"] = [
                t["table_id"] for t in payload["source_schema"]
            ]
            schema["properties"]["required_columns"]["items"]["enum"] = [
                f"{t['table_id']}.{c['name']}"
                for t in payload["source_schema"]
                for c in t["columns"]
            ]
        raw = client.generate_structured(messages, schema, timeout=budget.remaining_seconds())
        return contract.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise InvalidModelOutput(
            "Model JSON violates contract: "
            + json.dumps(exc.errors(include_url=False, include_input=False), default=str)[:2000]
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise InvalidModelOutput("Model JSON violates contract") from exc
    finally:
        metrics = {
            "role": role,
            **getattr(client, "last_metrics", {}),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        budget.generations.append(metrics)
        logger.info("model_completed", extra=metrics)


def evidence_context(
    q: QuestionRecord,
    catalog: Catalog,
    rules: list[RuleEvidence],
    time: TimeResolution,
    entities: list[GroundingCandidate],
) -> dict[str, Any]:
    return {
        "question": q.model_dump(),
        "question_language": language_hints(q.question),
        "source_schema": [
            {
                "table_id": t.table_id,
                "source_file": t.source_file,
                **({"sheet_or_table": t.sheet_or_table} if t.sheet_or_table else {}),
                "rows": t.rows,
                "source_order_column": "_source_row",
                "candidate_keys": t.candidate_keys,
                **({"derived_from": t.derived_from} if t.derived_from else {}),
                **({"warnings": t.warnings} if t.warnings else {}),
                "columns": [
                    {
                        "name": c.name,
                        **({"display_name": c.display_name} if c.display_name != c.name else {}),
                        "dtype": c.dtype,
                        **({"null_ratio": round(c.null_ratio, 4)} if c.null_ratio else {}),
                        "samples": c.samples[:2],
                        **column_notes(catalog.readme, t.source_file).get(c.name, {}),
                        **({"warnings": c.warnings} if c.warnings else {}),
                    }
                    for c in t.columns
                ],
            }
            for t in analysis_tables(catalog.tables)
        ],
        "source_readme": catalog.readme,
        "source_rules": [{"rule_id": r.rule_id, "text": r.text, "status": r.status} for r in rules],
        "time_evidence": time.model_dump(),
        "entity_candidates": [
            e.model_dump()
            for e in entities
            if e.table in {t.table_id for t in analysis_tables(catalog.tables)}
        ],
    }


def validate_plan(
    plan: PlanIR, q: QuestionRecord, catalog: Catalog, rules: list[RuleEvidence]
) -> list[str]:
    errors = []
    tables = {t.table_id: t for t in catalog.tables}
    if plan.question_id != q.id:
        errors.append("question_id_mismatch")
    expected_type = language_hints(q.question).get("expected_answer_type")
    if expected_type and plan.answer_type != expected_type:
        errors.append("answer_type_does_not_match_question_language:" + expected_type)
    if any(t not in tables for t in plan.required_tables):
        errors.append("unknown_table")
    available = {
        f"{t.table_id}.{c.name}"
        for t in catalog.tables
        if t.table_id in plan.required_tables
        for c in t.columns
    }
    referenced_columns = (
        plan.required_columns
        + [f.field for f in plan.filters]
        + plan.group_by
        + [s.field for s in plan.sort]
        + [m.field for m in plan.metrics if m.field is not None]
    )
    if any(c not in available for c in referenced_columns):
        errors.append("unknown_or_unqualified_column")
    if not set(plan.rule_ids) <= {r.rule_id for r in rules}:
        errors.append("unknown_rule")
    metric_rules = {rule_id for metric in plan.metrics for rule_id in metric.rule_ids}
    if not metric_rules <= set(plan.rule_ids):
        errors.append("metric_rule_not_declared")
    return errors


def qualify_plan_columns(plan: PlanIR, catalog: Catalog) -> PlanIR:
    available: dict[str, list[str]] = {}
    for table in catalog.tables:
        if table.table_id in plan.required_tables:
            for column in table.columns:
                available.setdefault(column.name, []).append(f"{table.table_id}.{column.name}")

    def qualify(name: str) -> str:
        options = available.get(name, [])
        return options[0] if len(options) == 1 else name

    metric_fields = {m.name: qualify(m.field) for m in plan.metrics if m.field}

    return plan.model_copy(
        update={
            "required_columns": [qualify(c) for c in plan.required_columns],
            "group_by": [qualify(c) for c in plan.group_by],
            "filters": [f.model_copy(update={"field": qualify(f.field)}) for f in plan.filters],
            "sort": [
                s.model_copy(update={"field": metric_fields.get(s.field, qualify(s.field))})
                for s in plan.sort
            ],
            "metrics": [
                m.model_copy(update={"field": qualify(m.field) if m.field else None})
                for m in plan.metrics
            ],
        }
    )

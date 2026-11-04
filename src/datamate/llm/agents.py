from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, TypeVar

from pydantic import ValidationError

from datamate.errors import InvalidModelOutput
from datamate.llm.client import ModelClient
from datamate.routing.budgets import BudgetManager
from datamate.types import Catalog, PlanIR, QuestionRecord, RuleEvidence, Schema, TimeResolution

T = TypeVar("T", bound=Schema)


def ask(
    client: ModelClient,
    role: str,
    payload: dict[str, Any],
    contract: type[T],
    budget: BudgetManager,
) -> T:
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if len(serialized.encode()) > 512000:
        raise InvalidModelOutput("Context exceeds engineering size limit; no silent truncation")
    budget.model_call()
    prompt = files("datamate").joinpath(f"prompts/{role}.md").read_text(encoding="utf-8")
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": serialized},
    ]
    try:
        raw = client.generate_structured(
            messages, contract.model_json_schema(), timeout=budget.remaining_seconds()
        )
        return contract.model_validate(raw, strict=True)
    except (ValidationError, ValueError, RecursionError) as exc:
        raise InvalidModelOutput("Model JSON violates contract") from exc


def evidence_context(
    q: QuestionRecord, catalog: Catalog, rules: list[RuleEvidence], time: TimeResolution
) -> dict[str, Any]:
    return {
        "question": q.model_dump(),
        "source_schema": [t.model_dump() for t in catalog.tables],
        "source_readme": catalog.readme,
        "source_rules": [r.model_dump() for r in rules],
        "time_evidence": time.model_dump(),
    }


def validate_plan(
    plan: PlanIR, q: QuestionRecord, catalog: Catalog, rules: list[RuleEvidence]
) -> list[str]:
    errors = []
    tables = {t.table_id: t for t in catalog.tables}
    if plan.question_id != q.id:
        errors.append("question_id_mismatch")
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
    )
    if any(c not in available for c in referenced_columns):
        errors.append("unknown_or_unqualified_column")
    if not set(plan.rule_ids) <= {r.rule_id for r in rules}:
        errors.append("unknown_rule")
    metric_rules = {rule_id for metric in plan.metrics for rule_id in metric.rule_ids}
    if not metric_rules <= set(plan.rule_ids):
        errors.append("metric_rule_not_declared")
    return errors

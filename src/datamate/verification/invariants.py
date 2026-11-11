from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from datamate.adapters.submission_writer import validate_record
from datamate.errors import OutputValidationError
from datamate.execution.sql_policy import validate_sql
from datamate.types import (
    Catalog,
    ExecutionResult,
    PlanIR,
    RuleEvidence,
    SqlCandidate,
    TimeResolution,
    VerificationReport,
)
from datamate.verification.joins import has_unique_right_keys
from datamate.verification.semantics import declared_checks


def answer_value(result: ExecutionResult, answer_type: str) -> Any:
    if answer_type == "table":
        return {"columns": result.columns, "rows": result.rows}
    if answer_type == "list":
        if any(len(row) != 1 for row in result.rows):
            raise OutputValidationError("List requires one column")
        return [row[0] for row in result.rows]
    if len(result.rows) != 1 or len(result.rows[0]) != 1 or result.rows[0][0] is None:
        raise OutputValidationError("Scalar answer requires exactly one non-null cell")
    return result.rows[0][0]


def verify(
    candidate: SqlCandidate,
    result: ExecutionResult,
    plan: PlanIR,
    catalog: Catalog,
    rules: list[RuleEvidence],
    time: TimeResolution,
) -> VerificationReport:
    report = VerificationReport(candidate_id=candidate.candidate_id)
    safe = validate_sql(candidate.sql, {t.table_id for t in catalog.tables})
    covered: set[str] = set()
    for name in result.source_tables:
        table = next(t for t in catalog.tables if t.table_id == name)
        parents = set(table.derived_from or [name])
        if parents & covered:
            report.hard_failures.append("overlapping_partition_sources")
        covered.update(parents)
    checks, warnings = declared_checks(candidate.sql, plan, catalog, time)
    report.semantic_checks = checks
    report.soft_warnings.extend(warnings)
    report.hard_failures.extend(
        f"declared_{name}_not_proven" for name, passed in checks.items() if not passed
    )
    if set(result.source_tables) != set(plan.required_tables):
        report.hard_failures.append("plan_source_mismatch")
    if candidate.answer_type != plan.answer_type:
        report.hard_failures.append("answer_type_mismatch")
    if not set(plan.rule_ids) <= set(candidate.rule_ids) or not set(candidate.rule_ids) <= {
        r.rule_id for r in rules
    }:
        report.hard_failures.append("rule_reference_mismatch")
    if safe.has_joins:
        if has_unique_right_keys(candidate.sql, catalog):
            report.soft_warnings.append("unique_right_keys_metric_grain_unverified")
        else:
            report.hard_failures.append("join_grain_not_proven")
    if time.ambiguous:
        report.hard_failures.append("ambiguous_time")
    if time.start_iso or time.end_exclusive_iso:
        report.soft_warnings.append("temporal_predicates_not_formally_verified")
    if plan.unknowns or plan.assumptions or plan.risk_flags:
        report.soft_warnings.append("unresolved_plan_semantics")
    if plan.filters or plan.metrics or plan.group_by or plan.sort:
        report.soft_warnings.append("predicate_metric_semantics_not_formally_verified")
    if plan.rule_ids and any(r.status != "compiled" for r in rules if r.rule_id in plan.rule_ids):
        report.soft_warnings.append("business_rules_source_only")
    tree = sqlglot.parse_one(candidate.sql, read="duckdb")
    referenced = {c.name for c in tree.find_all(exp.Column)}
    selects_all = any(
        isinstance(p, exp.Star) or isinstance(p, exp.Column) and p.is_star
        for select in tree.find_all(exp.Select)
        for p in select.expressions
    )
    for table in catalog.tables:
        if table.table_id in result.source_tables:
            if any(c.warnings and (selects_all or c.name in referenced) for c in table.columns):
                report.hard_failures.append("source_parse_ambiguity")
            elif any(c.warnings for c in table.columns):
                report.soft_warnings.append("unreferenced_column_parse_warnings")
            if table.warnings:
                report.soft_warnings.append("source_quality_notes_require_review")
    try:
        validate_record(
            {
                "id": plan.question_id,
                "type": plan.answer_type,
                "answer": answer_value(result, plan.answer_type),
            }
        )
    except OutputValidationError:
        report.hard_failures.append("output_type_or_shape")
    report.soft_warnings.append("table_column_lineage_only")
    return report

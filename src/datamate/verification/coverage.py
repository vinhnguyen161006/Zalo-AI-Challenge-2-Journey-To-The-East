"""Prove the narrow published unanswerability case of a fully uncovered period."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from datamate.config import Limits
from datamate.data.ingest import quote
from datamate.data.normalization import normalized_text
from datamate.data.partitions import month_label
from datamate.execution.sql_executor import execute
from datamate.types import Catalog, PlanIR, RuleEvidence, SqlCandidate, TimeResolution


def uncovered_period(
    plan: PlanIR,
    catalog: Catalog,
    time: TimeResolution,
    rules: list[RuleEvidence],
    limits: Limits,
    timeout: float,
) -> dict[str, Any] | None:
    if (
        time.ambiguous
        or not time.start_iso
        or not time.end_exclusive_iso
        or time.comparison_start_iso
    ):
        return None
    evidence = [
        r.rule_id
        for r in rules
        if all(
            token in normalized_text(r.text, accents=True)
            for token in ("hoan toan", "ngoai", "khong tra loi")
        )
    ]
    if not evidence:
        return None
    date_fields = {f.field for f in plan.filters if f.value_ref == "time_window"}
    if len(date_fields) != 1:
        return None
    source, column = next(iter(date_fields)).rsplit(".", 1)
    table = next((t for t in catalog.tables if t.table_id == source), None)
    if not table or month_label(table.sheet_or_table):
        return None
    card = next((c for c in table.columns if c.name == column), None)
    if not card or card.warnings:
        return None
    result = execute(
        SqlCandidate(
            candidate_id="coverage_audit",
            answer_type="table",
            strategy="source_coverage",
            sql=f"SELECT MIN({quote(column)}), MAX({quote(column)}) FROM {quote(source)}",
        ),
        catalog,
        limits,
        timeout,
    )
    low, high = result.rows[0]
    if not all(
        isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", v) for v in (low, high)
    ):
        return None
    first, last = date.fromisoformat(low[:10]), date.fromisoformat(high[:10])
    if (
        date.fromisoformat(time.end_exclusive_iso) <= first
        or date.fromisoformat(time.start_iso) > last
    ):
        return {
            "rule_ids": evidence,
            "source_field": f"{source}.{column}",
            "minimum_date": first.isoformat(),
            "maximum_date": last.isoformat(),
            "program_hash": result.program_hash,
        }
    return None

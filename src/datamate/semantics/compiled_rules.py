"""Compile a narrow, cited valid-sales rule into source-grounded status predicates."""

from __future__ import annotations

import re
from typing import Any

import duckdb

from datamate.data.ingest import quote
from datamate.data.normalization import normalized_text
from datamate.semantics.question import language_hints
from datamate.semantics.source_notes import column_notes, order_key_names
from datamate.types import Catalog, FilterIR, PlanIR, QuestionRecord, RuleEvidence, TimeResolution


def _text(value: str) -> str:
    return " ".join(re.sub(r"[|*_]", " ", normalized_text(value, accents=True)).split())


def bind_resolved_time_filters(
    plan: PlanIR, catalog: Catalog, time: TimeResolution
) -> tuple[PlanIR, list[str]]:
    """Bind an exact resolved window, never reinterpret an arbitrary literal range."""
    if time.ambiguous or not time.start_iso or not time.end_exclusive_iso:
        return plan, []
    date_fields = {
        f"{t.table_id}.{c.name}"
        for t in catalog.tables
        for c in t.columns
        if c.samples
        and not c.warnings
        and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", v) for v in c.samples)
    }
    bound = []
    filters = []
    for condition in plan.filters:
        if (
            condition.field in date_fields
            and condition.op == "range"
            and condition.value_ref is None
            and condition.value == [time.start_iso, time.end_exclusive_iso]
        ):
            condition = condition.model_copy(update={"value_ref": "time_window", "value": None})
            bound.append(condition.field)
        filters.append(condition)
    return plan.model_copy(update={"filters": filters}), bound


def compile_sales_validity(
    plan: PlanIR, question: QuestionRecord, catalog: Catalog, rules: list[RuleEvidence]
) -> tuple[PlanIR, list[dict[str, Any]]]:
    text = _text(question.question)
    if any(
        term in text
        for term in (
            "ban ghi",
            "dong du lieu",
            "source records",
            "huy",
            "cancel",
            "tra hang",
            "returned",
        )
    ):
        return plan, []
    cited = [r for r in rules if r.rule_id in plan.rule_ids]
    source_rules = []
    for rule in cited:
        value = _text(rule.text)
        vietnamese = (
            "don tinh vao doanh thu" in value and "khong huy" in value and "khong tra hang" in value
        )
        english = (
            "valid sales" in value
            and "status complete or delivering" in value
            and "exclude cancelled and returned" in value
        )
        if vietnamese or english:
            if any(
                term in value
                for term in (
                    "chi tinh khi",
                    "chi tinh don",
                    "thanh toan",
                    "tien mat",
                    "cod",
                    "cong them",
                    "tru them",
                )
            ):
                continue
            source_rules.append(rule.rule_id)
    if not source_rules:
        return plan, []
    filters = list(plan.filters)
    evidence = []
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        for table in catalog.tables:
            if table.table_id not in plan.required_tables:
                continue
            names = {c.name for c in table.columns}
            if not set(table.candidate_keys) & (
                order_key_names(catalog.readme, table.source_file)
                | {
                    "order_id",
                    "ma_don",
                    "ma_hoa_don",
                    "ma_hoadon",
                    "so_hoa_don",
                }
            ):
                continue
            statuses = names & {"status", "trang_thai", "trangthai", "tinh_trang"}
            if len(statuses) != 1:
                continue
            column = next(iter(statuses))
            values = con.execute(
                f"SELECT DISTINCT {quote(column)} FROM {quote(table.table_id)} WHERE {quote(column)} IS NOT NULL ORDER BY {quote(column)} LIMIT 17"
            ).fetchall()
            if len(values) > 16 or not values:
                continue
            allowed = []
            known = True
            for (value,) in values:
                label = _text(str(value))
                if label in {
                    "complete",
                    "completed",
                    "hoan thanh",
                    "da hoan thanh",
                    "da giao",
                    "delivering",
                    "dang giao",
                    "dang giao hang",
                }:
                    allowed.append(value)
                elif label not in {
                    "cancelled",
                    "canceled",
                    "huy",
                    "da huy",
                    "returned",
                    "tra hang",
                    "da tra hang",
                    "hoan tra",
                }:
                    known = False
            if not known or not allowed:
                continue
            field = f"{table.table_id}.{column}"
            if not any(
                f.field == field
                and f.op == "in"
                and isinstance(f.value, list)
                and set(f.value) == set(allowed)
                for f in filters
            ):
                filters.append(FilterIR(field=field, op="in", value=allowed))
            evidence.append(
                {
                    "rule_ids": source_rules,
                    "field": field,
                    "allowed_source_values": allowed,
                    "scope": "status_fragment_only; returns_at_other_grains_not_proven",
                }
            )
    return plan.model_copy(update={"filters": filters}), evidence


def compile_period_constraint(
    plan: PlanIR, question: QuestionRecord, catalog: Catalog, time: TimeResolution
) -> tuple[PlanIR, list[dict[str, Any]]]:
    if (
        time.ambiguous
        or not time.start_iso
        or not time.end_exclusive_iso
        or time.comparison_start_iso
    ):
        return plan, []
    question_text = _text(question.question)
    if any(t in question_text for t in ("ban ghi", "source records", "dong du lieu")):
        return plan, []
    if any(
        t in question_text
        for t in (
            "ngay giao",
            "dot giao",
            "han dung",
            "het han",
            "tra no",
            "thu no",
            "ngay thanh toan",
            "ngay tra",
            "ton kho",
            "con no",
            "con ton",
            "cong no",
        )
    ):
        return plan, []
    candidates = []
    for table in catalog.tables:
        if table.table_id not in plan.required_tables:
            continue
        keys = set(table.candidate_keys)
        order = bool(
            keys
            & (
                order_key_names(catalog.readme, table.source_file)
                | {"order_id", "ma_don", "ma_hoa_don", "ma_hoadon", "so_hoa_don"}
            )
        )
        purchase = bool(keys & {"purchase_id", "receipt_id", "ma_nhap_hang", "ma_phieu_nhap"})
        preferred = (
            {"order_date", "ngay_dat", "ngay_dat_hang"}
            if order
            else {"purchase_date", "ngay_nhap", "ngay_nhap_hang"}
            if purchase
            else set()
        )
        if not order and not purchase:
            continue
        explicit = [c.name for c in table.columns if c.name in preferred]
        if len(explicit) == 1:
            candidates.append((table.table_id, explicit[0], "explicit_transaction_date_field"))
        elif not explicit:
            possible = [
                c.name
                for c in table.columns
                if not c.warnings
                and c.samples
                and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", v) for v in c.samples)
                and any(token in c.name for token in ("date", "ngay", "thoi_gian"))
            ]
            if len(possible) == 1:
                candidates.append((table.table_id, possible[0], "only_transaction_date_field"))
    if len(candidates) != 1:
        return plan, []
    table_id, column, evidence = candidates[0]
    field = f"{table_id}.{column}"
    if any(f.field == field for f in plan.filters):
        return plan, []
    constraint = FilterIR(field=field, op="range", value_ref="time_window")
    return plan.model_copy(update={"filters": [*plan.filters, constraint]}), [
        {
            "field": field,
            "source": evidence,
            "start": time.start_iso,
            "end_exclusive": time.end_exclusive_iso,
            "time_evidence": time.evidence,
        }
    ]


def compile_revenue_measure(
    plan: PlanIR, question: QuestionRecord, catalog: Catalog, rules: list[RuleEvidence]
) -> tuple[PlanIR, list[dict[str, Any]]]:
    text = _text(language_hints(question.question)["expanded_question"])
    if "doanh thu" not in text or any(
        term in text
        for term in (
            "loi nhuan",
            "gia von",
            "lai gop",
            "lai rong",
            "tru voucher",
            "sau giam gia",
            "gom phi giao",
            "gom vat",
        )
    ):
        return plan, []
    formula = [
        r
        for r in rules
        if all(
            term in _text(r.text)
            for term in ("doanh thu mot don", "thanh tien", "khong tru", "khong gom phi giao")
        )
    ]
    if len(formula) != 1 or len(plan.required_tables) != 1 or len(plan.metrics) != 1:
        return plan, []
    if any(
        term in _text(formula[0].text)
        for term in ("cong them", "tru them", "thuong", "chi tinh khi")
    ):
        return plan, []
    table = next((t for t in catalog.tables if t.table_id == plan.required_tables[0]), None)
    if table is None:
        return plan, []
    known_keys = order_key_names(catalog.readme, table.source_file) | {
        "order_id",
        "ma_don",
        "ma_hoa_don",
        "ma_hoadon",
    }
    if not set(table.candidate_keys) & known_keys:
        return plan, []
    notes = column_notes(catalog.readme, table.source_file)
    fields = []
    for column in table.columns:
        note = notes.get(column.name, {})
        meaning = _text(column.display_name + " " + str(note.get("description", "")))
        if "tien hang" in meaning and any(
            term in meaning
            for term in ("cong thanh tien", "truoc giam gia cua don", "truoc giam gia don")
        ):
            fields.append(column.name)
    if len(fields) != 1:
        return plan, []
    field = f"{table.table_id}.{fields[0]}"
    replacements = {
        m.field: field
        for m in plan.metrics
        if m.field
        and m.field.startswith(table.table_id + ".")
        and m.function in {"sum", "mean", "min", "max"}
        and m.field != field
    }
    if not replacements:
        return plan, []
    rule_id = formula[0].rule_id
    metrics = [
        m.model_copy(
            update={
                "field": replacements[m.field],
                "rule_ids": list(dict.fromkeys([*m.rule_ids, rule_id])),
            }
        )
        if m.field in replacements
        else m
        for m in plan.metrics
    ]
    proposed = plan.model_copy(
        update={
            "metrics": metrics,
            "required_columns": list(
                dict.fromkeys(replacements.get(c, c) for c in plan.required_columns)
            ),
            "sort": [
                s.model_copy(update={"field": replacements.get(s.field, s.field)})
                for s in plan.sort
            ],
            "rule_ids": list(dict.fromkeys([*plan.rule_ids, rule_id])),
        }
    )
    return proposed, [
        {
            "rule_id": rule_id,
            "field": field,
            "definition": notes[fields[0]],
            "replaced_metric_fields": sorted(replacements),
        }
    ]

"""Compile supported fully-declared scalar plans without another generation call."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlglot import exp, parse_one

from datamate.types import Catalog, PlanIR, SqlCandidate, TimeResolution


def scalar_template(plan: PlanIR, catalog: Catalog, time: TimeResolution) -> SqlCandidate | None:
    if (
        len(plan.required_tables) != 1
        or len(plan.metrics) != 1
        or plan.group_by
        or plan.sort
        or plan.limit
        or plan.assumptions
        or plan.unknowns
        or plan.risk_flags
        or time.ambiguous
        or time.comparison_start_iso
        or plan.answer_type != "number"
    ):
        return None
    table = plan.required_tables[0]
    card = next((t for t in catalog.tables if t.table_id == table), None)
    if card is None:
        return None
    available = {c.name: c for c in card.columns}

    def column(name: str) -> exp.Column:
        source, field = name.rsplit(".", 1)
        if source != table or field not in available:
            raise ValueError("Template column outside source")
        return exp.column(field, table=table, quoted=True)

    def literal(value: Any) -> exp.Expr:
        if value is None:
            return exp.Null()
        if isinstance(value, bool):
            return exp.Boolean(this=value)
        if isinstance(value, (int, float, Decimal)):
            decimal = Decimal(str(value))
            if not decimal.is_finite():
                raise ValueError("Non-finite template operand")
            return exp.Literal.number(str(decimal))
        if isinstance(value, str):
            return exp.Literal.string(value)
        raise ValueError("Template literal must be scalar")

    metric = plan.metrics[0]
    classes = {
        "sum": exp.Sum,
        "count": exp.Count,
        "count_distinct": exp.Count,
        "min": exp.Min,
        "max": exp.Max,
        "mean": exp.Avg,
    }
    if metric.function not in classes or metric.field is None and metric.function != "count":
        return None
    try:
        operand: exp.Expr = column(metric.field) if metric.field else exp.Star()
        if metric.function == "count_distinct":
            operand = exp.Distinct(expressions=[operand])
        aggregate = classes[metric.function](this=operand)
        query = exp.select(aggregate).from_(exp.Table(this=exp.to_identifier(table, quoted=True)))
        predicates: list[exp.Expr] = []
        comparisons = {
            "eq": exp.EQ,
            "ne": exp.NEQ,
            "gt": exp.GT,
            "gte": exp.GTE,
            "lt": exp.LT,
            "lte": exp.LTE,
        }
        for condition in plan.filters:
            target = column(condition.field)
            if condition.value_ref == "time_window":
                if not time.start_iso or not time.end_exclusive_iso:
                    return None
                predicates.extend(
                    [
                        exp.GTE(this=target.copy(), expression=literal(time.start_iso)),
                        exp.LT(this=target.copy(), expression=literal(time.end_exclusive_iso)),
                    ]
                )
            elif condition.op == "range":
                if not isinstance(condition.value, list) or len(condition.value) != 2:
                    return None
                predicates.append(
                    exp.Between(
                        this=target,
                        low=literal(condition.value[0]),
                        high=literal(condition.value[1]),
                    )
                )
            elif condition.op == "in":
                if (
                    not isinstance(condition.value, list)
                    or not condition.value
                    or any(v is None for v in condition.value)
                ):
                    return None
                predicates.append(
                    exp.In(this=target, expressions=[literal(v) for v in condition.value])
                )
            elif condition.op == "is_null":
                predicates.append(exp.Is(this=target, expression=exp.Null()))
            elif condition.op in comparisons and condition.value is not None:
                predicates.append(
                    comparisons[condition.op](this=target, expression=literal(condition.value))
                )
            else:
                return None
        if predicates:
            query = query.where(exp.and_(*predicates))
        return SqlCandidate(
            candidate_id="typed_scalar_plan",
            sql=query.sql(dialect="duckdb"),
            strategy="typed_plan_template",
            answer_type="number",
            rule_ids=plan.rule_ids,
        )
    except (ValueError, InvalidOperation):
        return None


def ranking_template(plan: PlanIR, catalog: Catalog, time: TimeResolution) -> SqlCandidate | None:
    if (
        plan.answer_type not in {"date", "string"}
        or len(plan.group_by) != 1
        or plan.limit != 1
        or len(plan.metrics) != 1
        or not plan.sort
    ):
        return None
    metric = plan.metrics[0]
    if not metric.field or plan.sort[0].field != metric.field or not plan.sort[0].descending:
        return None
    group = plan.group_by[0]
    if any(s.field not in {metric.field, group} for s in plan.sort) or any(
        s.field == group and s.descending for s in plan.sort
    ):
        return None
    table, field = group.rsplit(".", 1)
    card = next(
        (c for t in catalog.tables if t.table_id == table for c in t.columns if c.name == field),
        None,
    )
    if card is None or card.dtype != "VARCHAR":
        return None
    if plan.answer_type == "date" and (
        not card.samples
        or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in card.samples)
    ):
        return None
    base = scalar_template(
        plan.model_copy(
            update={"answer_type": "number", "group_by": [], "sort": [], "limit": None}
        ),
        catalog,
        time,
    )
    if base is None:
        return None
    tree = parse_one(base.sql, read="duckdb")
    if not isinstance(tree, exp.Select):
        return None
    aggregate = tree.expressions[0].copy()
    target = exp.column(field, table=table, quoted=True)
    tree.set("expressions", [target.copy()])
    tree = (
        tree.group_by(target.copy())
        .order_by(
            exp.Ordered(this=aggregate, desc=True), exp.Ordered(this=target.copy(), desc=False)
        )
        .limit(1)
    )
    return SqlCandidate(
        candidate_id="typed_rank_plan",
        sql=tree.sql(dialect="duckdb"),
        strategy="typed_rank_template",
        answer_type=plan.answer_type,
        rule_ids=plan.rule_ids,
    )

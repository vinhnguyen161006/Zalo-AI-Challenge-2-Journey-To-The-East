"""Conservative proof of declared filters and ordering for direct relational SELECTs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import sqlglot
from sqlglot import exp

from datamate.types import Catalog, PlanIR, TimeResolution


def _literal(node: exp.Expr) -> Any:
    while isinstance(node, (exp.Cast, exp.TryCast, exp.Paren)):
        node = node.this
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Neg):
        value = _literal(node.this)
        return -value if isinstance(value, Decimal) else object()
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        return Decimal(str(node.this))
    return object()


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, Decimal) and not isinstance(expected, bool):
        try:
            return actual == Decimal(str(expected))
        except InvalidOperation:
            return False
    return type(actual) is type(expected) and actual == expected


def declared_checks(
    sql: str, plan: PlanIR, catalog: Catalog, time: TimeResolution
) -> tuple[dict[str, bool], list[str]]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    if not isinstance(tree, exp.Select) or tree.args.get("with_") or tree.find(exp.Subquery):
        return {}, ["semantic_proof_requires_direct_select"]
    aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    if len(aliases.values()) != len(set(aliases.values())):
        return {}, ["semantic_proof_self_join_unsupported"]
    columns = {t.table_id: {c.name for c in t.columns} for t in catalog.tables}

    def field(node: exp.Expr) -> str | None:
        while isinstance(node, (exp.Cast, exp.TryCast, exp.Paren)):
            node = node.this
        if not isinstance(node, exp.Column):
            return None
        if node.table:
            table = aliases.get(node.table)
            return f"{table}.{node.name}" if table else None
        matching = [t for t in aliases.values() if node.name in columns.get(t, set())]
        return f"{matching[0]}.{node.name}" if len(matching) == 1 else None

    def conjunction(node: exp.Expr) -> list[exp.Expr]:
        if isinstance(node, exp.Paren):
            return conjunction(node.this)
        if isinstance(node, exp.And):
            return conjunction(node.this) + conjunction(node.expression)
        return [node]

    where = tree.args.get("where")
    atoms = conjunction(where.this) if where else []
    predicates: list[tuple[str, str, Any]] = []
    unsupported_predicates = False
    binary: dict[type[exp.Expr], str] = {
        exp.EQ: "eq",
        exp.NEQ: "ne",
        exp.GT: "gt",
        exp.GTE: "gte",
        exp.LT: "lt",
        exp.LTE: "lte",
    }
    reverse = {"gt": "lt", "gte": "lte", "lt": "gt", "lte": "gte", "eq": "eq", "ne": "ne"}
    for atom in atoms:
        before = len(predicates)
        operator = binary.get(type(atom))
        if operator:
            lhs, rhs = field(atom.this), field(atom.expression)
            if lhs and not rhs:
                predicates.append((lhs, operator, _literal(atom.expression)))
            elif rhs and not lhs:
                predicates.append((rhs, reverse[operator], _literal(atom.this)))
        elif isinstance(atom, exp.Between) and (name := field(atom.this)):
            predicates.extend(
                [
                    (name, "gte", _literal(atom.args["low"])),
                    (name, "lte", _literal(atom.args["high"])),
                ]
            )
        elif isinstance(atom, exp.In) and not atom.args.get("query") and (name := field(atom.this)):
            predicates.append((name, "in", [_literal(v) for v in atom.expressions]))
        elif (
            isinstance(atom, exp.Is)
            and isinstance(atom.expression, exp.Null)
            and (name := field(atom.this))
        ):
            predicates.append((name, "is_null", None))
        if len(predicates) == before:
            unsupported_predicates = True

    def present(name: str, operator: str, value: Any) -> bool:
        for source, op, literal in predicates:
            if source != name or op != operator:
                continue
            if operator == "in":
                if (
                    isinstance(value, list)
                    and len(value) == len(literal)
                    and all(any(_equal(a, b) for b in literal) for a in value)
                ):
                    return True
            elif _equal(literal, value):
                return True
        return False

    checks = {}
    functions = {
        "sum": exp.Sum,
        "count": exp.Count,
        "count_distinct": exp.Count,
        "min": exp.Min,
        "max": exp.Max,
        "mean": exp.Avg,
    }
    for index, metric in enumerate(plan.metrics):
        if metric.function:
            matched = False
            for aggregate in tree.find_all(functions[metric.function]):
                if aggregate.find_ancestor(exp.Select) is not tree:
                    continue
                operand = aggregate.this
                if metric.function == "count_distinct":
                    if not isinstance(operand, exp.Distinct) or len(operand.expressions) != 1:
                        continue
                    operand = operand.expressions[0]
                elif isinstance(operand, exp.Distinct):
                    continue
                if metric.field is not None and field(operand) == metric.field:
                    matched = True
                elif (
                    metric.field is None
                    and metric.function == "count"
                    and isinstance(operand, exp.Star)
                ):
                    matched = True
            checks[f"metric_{index}"] = matched
    for index, predicate in enumerate(plan.filters):
        if predicate.value_ref == "time_window":
            checks[f"filter_{index}"] = bool(
                time.start_iso
                and time.end_exclusive_iso
                and present(predicate.field, "gte", time.start_iso)
                and present(predicate.field, "lt", time.end_exclusive_iso)
            )
        elif predicate.op == "range":
            value = predicate.value
            checks[f"filter_{index}"] = bool(
                isinstance(value, list)
                and len(value) == 2
                and present(predicate.field, "gte", value[0])
                and present(predicate.field, "lte", value[1])
            )
        else:
            checks[f"filter_{index}"] = present(predicate.field, predicate.op, predicate.value)
    expected_predicates = sum(
        2 if p.op == "range" or p.value_ref == "time_window" else 1 for p in plan.filters
    )
    checks["filter_coverage"] = (
        not unsupported_predicates
        and len(predicates) == expected_predicates
        and all(v for k, v in checks.items() if k.startswith("filter_"))
    )
    if plan.group_by:
        grouping = tree.args.get("group")
        checks["group_by"] = (
            grouping is not None and [field(v) for v in grouping.expressions] == plan.group_by
        )
    if plan.sort:
        projections = {p.alias: p.this for p in tree.expressions if isinstance(p, exp.Alias)}

        def ordering_field(node: exp.Expr) -> str | None:
            if isinstance(node, exp.Column) and not node.table and node.name in projections:
                node = projections[node.name]
            if isinstance(node, exp.AggFunc):
                for metric in plan.metrics:
                    if (
                        metric.function in functions
                        and isinstance(node, functions[metric.function])
                        and field(node.this) == metric.field
                    ):
                        return metric.field
                return None
            return field(node)

        order = tree.args.get("order")
        actual = (
            [(ordering_field(v.this), bool(v.args.get("desc"))) for v in order.expressions]
            if order
            else []
        )
        checks["ordering"] = actual[: len(plan.sort)] == [
            (v.field, v.descending) for v in plan.sort
        ]
    if plan.limit is not None:
        limit = tree.args.get("limit")
        checks["limit"] = limit is not None and _equal(_literal(limit.expression), plan.limit)
    return checks, []

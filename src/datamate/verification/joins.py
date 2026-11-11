"""Scope-aware multiplicity proofs for direct and pre-aggregated equality joins."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from datamate.types import Catalog


def has_unique_right_keys(sql: str, catalog: Catalog) -> bool:
    tree = sqlglot.parse_one(sql, read="duckdb")
    cards = {t.table_id: t for t in catalog.tables}
    keys: dict[int, list[frozenset[str]]] = {}

    def source_keys(source: exp.Table | Scope | None) -> list[frozenset[str]]:
        if isinstance(source, Scope):
            return keys.get(id(source), [])
        if isinstance(source, exp.Table) and source.name in cards:
            return [frozenset([column]) for column in cards[source.name].candidate_keys]
        return []

    def atoms(node: exp.Expr) -> list[exp.Expr]:
        if isinstance(node, exp.Paren):
            return atoms(node.this)
        if isinstance(node, exp.And):
            return atoms(node.this) + atoms(node.expression)
        return [node]

    for scope in traverse_scope(tree):
        select = scope.expression
        if not isinstance(select, exp.Select):
            keys[id(scope)] = []
            continue
        source = select.args.get("from_")
        if source is None:
            return False
        left_alias = source.this.alias_or_name
        seen = {left_alias}
        left_keys = source_keys(scope.sources.get(left_alias))
        for join in select.args.get("joins", []):
            alias = join.this.alias_or_name
            if alias in seen or join.args.get("side") not in {None, "", "LEFT"}:
                return False
            on = join.args.get("on")
            if on is None:
                return False
            right_columns: set[str] = set()
            left_columns: set[str] = set()
            for predicate in atoms(on):
                if not isinstance(predicate, exp.EQ):
                    return False
                pair = [predicate.this, predicate.expression]
                if not all(isinstance(c, exp.Column) for c in pair):
                    return False
                right = [c for c in pair if c.table == alias]
                left = [c for c in pair if c.table in seen]
                if len(right) != 1 or len(left) != 1:
                    return False
                right_columns.add(right[0].name)
                if left[0].table == left_alias:
                    left_columns.add(left[0].name)
            if not any(key <= right_columns for key in source_keys(scope.sources.get(alias))):
                return False
            left_unique = any(key <= left_columns for key in left_keys)
            if not left_unique:
                for aggregate in select.find_all(exp.Sum, exp.Avg):
                    if aggregate.find_ancestor(exp.Select) is select and any(
                        c.table in {"", alias} for c in aggregate.find_all(exp.Column)
                    ):
                        return False
            seen.add(alias)
        projections = {
            p.alias_or_name: p.this if isinstance(p, exp.Alias) else p for p in select.expressions
        }
        inferred: list[frozenset[str]] = []
        group = select.args.get("group")
        if group is not None:
            group_names: list[str] = []
            for term in group.expressions:
                names = [
                    name
                    for name, expression in projections.items()
                    if expression == term
                    or isinstance(term, exp.Column)
                    and not term.table
                    and term.name == name
                ]
                if not names:
                    group_names = []
                    break
                group_names.append(names[0])
            if group_names and len(group_names) == len(group.expressions):
                inferred.append(frozenset(group_names))
        elif not any(p.find(exp.AggFunc) for p in select.expressions):
            for key in left_keys:
                mapped = {
                    name
                    for name, expression in projections.items()
                    if isinstance(expression, exp.Column)
                    and expression.name in key
                    and expression.table in {"", left_alias}
                }
                if len(mapped) == len(key):
                    inferred.append(frozenset(mapped))
        if select.args.get("distinct") and not any(p.find(exp.Star) for p in select.expressions):
            inferred.append(frozenset(projections))
        keys[id(scope)] = inferred
    return True

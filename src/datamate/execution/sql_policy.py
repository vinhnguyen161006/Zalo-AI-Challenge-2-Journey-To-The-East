from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from datamate.errors import UnsafeProgram

# Small positive grammar: unknown nodes/functions fail closed, including UDFs,
# external readers, system tables, recursive CTEs, SELECT INTO and macros.
ALLOWED_NODES = frozenset(
    """
select from table tablealias identifier column literal star alias where and or not eq neq gt gte lt lte
is null boolean in between paren add sub mul div mod neg cast trycast datatype datatypeparam
sum count avg min max abs round coalesce nullif case if distinct group having order ordered
limit offset join with cte subquery union intersect except exists tuple filter window
partition rownumber rank denserank lag lead date datediff dateadd datesub extract interval
currentdate var placeholder
""".split()
) - {"currentdate", "var", "placeholder"}


@dataclass(frozen=True)
class SafeSql:
    sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    has_joins: bool


def validate_sql(sql: str, allowed_tables: set[str]) -> SafeSql:
    if not sql.strip() or len(sql) > 32000:
        raise UnsafeProgram("Empty or oversized SQL")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeProgram("SQL parse failure") from exc
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise UnsafeProgram("Only one read-only SELECT/CTE is allowed")
    tree = statements[0]
    for node in tree.walk():
        if node.key not in ALLOWED_NODES:
            raise UnsafeProgram(f"SQL node not allowed: {node.key}")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise UnsafeProgram("Recursive CTE is not allowed")
        if isinstance(node, exp.Join) and (
            not node.args.get("on") or node.args.get("kind") == "CROSS"
        ):
            raise UnsafeProgram("Join must have an explicit ON condition")
        if isinstance(node, exp.Table) and (
            node.db or node.catalog or not isinstance(node.this, exp.Identifier)
        ):
            raise UnsafeProgram("External/schema-qualified or table-valued functions forbidden")
    source_tables = set()
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                if source.name not in allowed_tables:
                    raise UnsafeProgram("Unknown table or out-of-scope catalog")
                source_tables.add(source.name)
    if not source_tables:
        raise UnsafeProgram("A business source table is required")
    columns = tuple(sorted({c.sql(dialect="duckdb") for c in tree.find_all(exp.Column)}))
    return SafeSql(
        tree.sql(dialect="duckdb"),
        tuple(sorted(source_tables)),
        columns,
        bool(list(tree.find_all(exp.Join))),
    )

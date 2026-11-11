"""Small typed independent DataOps branch. No generated Python or eval."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from datamate.errors import UnsafeProgram
from datamate.types import AnswerType, Schema


class Condition(Schema):
    column: str
    comparator: Literal["eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "is_null", "not_null"]
    value: Any = None


class Operation(Schema):
    op: Literal[
        "filter",
        "aggregate",
        "group_sum",
        "sort",
        "select",
        "limit",
        "join",
        "union",
        "calculate",
        "group_aggregate",
        "distinct",
        "rename",
        "round",
        "date_part",
        "conditional",
        "values",
        "forward_fill",
    ]
    column: str | None = None
    comparator: (
        Literal[
            "eq",
            "ne",
            "gt",
            "ge",
            "lt",
            "le",
            "in",
            "not_in",
            "is_null",
            "not_null",
            "contains",
        ]
        | None
    ) = None
    value: Any = None
    function: Literal["sum", "count", "count_distinct", "min", "max", "mean"] | None = None
    by: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    descending: bool = False
    descending_by: list[bool] = Field(default_factory=list)
    count: int | None = Field(default=None, ge=1, le=10000)
    right_table: str | None = None
    left_on: list[str] = Field(default_factory=list)
    right_on: list[str] = Field(default_factory=list)
    how: Literal["inner", "left"] = "inner"
    right_prefix: str = "right_"
    relationship: Literal["many_to_one", "one_to_one"] = "many_to_one"
    output: str | None = None
    left_column: str | None = None
    right_column: str | None = None
    left_value: int | float | str | None = None
    right_value: int | float | str | None = None
    operator: (
        Literal[
            "add", "subtract", "multiply", "divide", "coalesce", "eq", "ne", "gt", "ge", "lt", "le"
        ]
        | None
    ) = None
    aggregations: list[Aggregation] = Field(default_factory=list, max_length=20)
    mapping: dict[str, str] = Field(default_factory=dict)
    digits: int = Field(default=2, ge=-6, le=10)
    part: Literal["year", "month", "day", "weekday", "date", "year_month"] | None = None
    conditions: list[Condition] = Field(default_factory=list, max_length=12)


class Aggregation(Schema):
    output: str
    function: Literal["sum", "count", "count_distinct", "min", "max", "mean"]
    column: str | None = None


Operation.model_rebuild()


class SourcePreparation(Schema):
    source_table: str
    steps: list[Operation] = Field(min_length=1, max_length=20)


class DataOpsCandidate(Schema):
    candidate_id: str
    source_table: str
    steps: list[Operation] = Field(min_length=1, max_length=20)
    answer_type: AnswerType
    preparations: list[SourcePreparation] = Field(default_factory=list, max_length=10)


def referenced_tables(candidate: DataOpsCandidate) -> list[str]:
    return list(
        dict.fromkeys(
            [
                candidate.source_table,
                *[p.source_table for p in candidate.preparations],
                *[
                    s.right_table
                    for s in candidate.steps
                    if s.right_table and s.op in {"join", "union"}
                ],
            ]
        )
    )


def interpret(
    candidate: DataOpsCandidate,
    frame: pd.DataFrame,
    sources: dict[str, pd.DataFrame] | None = None,
    max_rows: int = 500000,
) -> pd.DataFrame:
    equalities: dict[str, Any] = {}
    for step in candidate.steps:
        if step.op == "filter" and step.comparator == "eq" and step.column:
            if step.column in equalities and equalities[step.column] != step.value:
                raise UnsafeProgram(
                    "Contradictory equality filters: use one in filter for alternative values"
                )
            equalities[step.column] = step.value
    with localcontext() as context:
        context.prec = 80
        prepared = dict(sources or {})
        prepared.setdefault(candidate.source_table, frame)
        seen = set()
        for preparation in candidate.preparations:
            if preparation.source_table in seen or preparation.source_table not in prepared:
                raise UnsafeProgram("Preparation requires a unique known source")
            seen.add(preparation.source_table)
            if any(s.op in {"join", "union"} for s in preparation.steps):
                raise UnsafeProgram("Source preparations cannot read other relations")
            program = DataOpsCandidate(
                candidate_id="preparation",
                source_table=preparation.source_table,
                steps=preparation.steps,
                answer_type="table",
            )
            prepared[preparation.source_table] = _interpret(
                program, prepared[preparation.source_table], {}, max_rows
            )
        return _interpret(candidate, prepared[candidate.source_table], prepared, max_rows)


def _aggregate(series: pd.Series[Any], function: str) -> Any:
    nonnull = series.dropna()
    if function == "sum":
        return series.sum(min_count=1)
    if function == "count_distinct":
        return series.nunique(dropna=True)
    if function == "mean":
        return sum(nonnull) / len(nonnull) if len(nonnull) else None
    return getattr(series, function)()


def _condition(frame: pd.DataFrame, condition: Condition) -> pd.Series[Any]:
    if condition.column not in frame:
        raise UnsafeProgram("Unknown conditional column")
    series = frame[condition.column]
    if condition.comparator == "is_null":
        return series.isna()
    if condition.comparator == "not_null":
        return series.notna()
    if condition.comparator in {"in", "not_in"}:
        if not isinstance(condition.value, list) or any(v is None for v in condition.value):
            raise UnsafeProgram("Conditional membership requires non-null values")
        mask = series.isin(condition.value)
        if condition.comparator == "not_in":
            mask = ~mask
    else:
        mask = getattr(series, condition.comparator)(condition.value)
    return mask.fillna(False) & series.notna()


def _operand(
    frame: pd.DataFrame, column: str | None, value: int | float | str | None
) -> pd.Series[Any]:
    if (column is None) == (value is None):
        raise UnsafeProgram("Operand requires exactly one column or literal")
    if column:
        return frame[column]
    scalar: Any = value
    if isinstance(value, (int, float)):
        scalar = Decimal(str(value))
        if not scalar.is_finite():
            raise UnsafeProgram("Non-finite operand")
    return pd.Series([scalar] * len(frame), index=frame.index, dtype=object)


def _interpret(
    candidate: DataOpsCandidate,
    frame: pd.DataFrame,
    sources: dict[str, pd.DataFrame],
    max_rows: int,
) -> pd.DataFrame:
    frame = frame.copy()
    result: Any
    for step in candidate.steps:
        names = [
            c
            for c in [
                step.column,
                *step.by,
                *step.columns,
                *step.left_on,
                step.left_column,
                step.right_column,
                *[a.column for a in step.aggregations],
                *step.mapping,
            ]
            if c is not None
        ]
        if any(c not in frame.columns for c in names):
            raise UnsafeProgram("Unknown DSL column")
        if step.op == "filter":
            if not step.column or not step.comparator:
                raise UnsafeProgram("DSL filter needs a column and comparator")
            if step.comparator in {"is_null", "not_null"}:
                mask = frame[step.column].isna()
                if step.comparator == "not_null":
                    mask = ~mask
                frame = frame.loc[mask]
                continue
            if step.comparator == "contains":
                if not isinstance(step.value, str):
                    raise UnsafeProgram("contains requires literal text")
                mask = frame[step.column].astype("string").str.contains(step.value, regex=False)
            elif step.comparator in {"in", "not_in"}:
                if not isinstance(step.value, list) or any(v is None for v in step.value):
                    raise UnsafeProgram("DSL membership requires non-null list values")
                mask = frame[step.column].isin(step.value)
                if step.comparator == "not_in":
                    mask = ~mask
            else:
                method = {"eq": "eq", "ne": "ne", "gt": "gt", "ge": "ge", "lt": "lt", "le": "le"}[
                    step.comparator
                ]
                mask = getattr(frame[step.column], method)(step.value)
            mask = mask & frame[step.column].notna()
            frame = frame.loc[mask.fillna(False)]
        elif step.op == "aggregate":
            if step.function == "count" and step.column is None:
                result = len(frame)
            elif step.column and step.function:
                result = _aggregate(frame[step.column], step.function)
            else:
                raise UnsafeProgram("Incomplete DSL aggregate")
            frame = pd.DataFrame({"value": [result]})
        elif step.op == "group_sum":
            if not step.by or not step.column:
                raise UnsafeProgram("Incomplete DSL group_sum")
            frame = frame.groupby(step.by, dropna=False)[step.column].sum(min_count=1).reset_index()
        elif step.op == "sort":
            if not step.by:
                raise UnsafeProgram("DSL sort must specify keys")
            if step.descending_by and len(step.descending_by) != len(step.by):
                raise UnsafeProgram("DSL sort directions must match keys")
            ascending = (
                [not d for d in step.descending_by] if step.descending_by else not step.descending
            )
            frame = frame.sort_values(step.by, ascending=ascending, kind="stable")
        elif step.op == "select":
            if not step.columns:
                raise UnsafeProgram("DSL select needs columns")
            frame = frame[step.columns]
        elif step.op == "limit":
            if step.count is None:
                raise UnsafeProgram("DSL limit requires count")
            frame = frame.head(step.count)
        elif step.op in {"join", "union"}:
            if not step.right_table or step.right_table not in sources:
                raise UnsafeProgram("Unknown relational source")
            right = sources[step.right_table].copy()
            if step.op == "union":
                if list(frame.columns) != list(right.columns):
                    raise UnsafeProgram("Union requires exactly matching columns in order")
                if len(frame) + len(right) > max_rows:
                    raise UnsafeProgram("Union intermediate row limit exceeded")
                frame = pd.concat([frame, right], ignore_index=True)
            else:
                if not step.left_on or len(step.left_on) != len(step.right_on):
                    raise UnsafeProgram("Join requires matching explicit key lists")
                if any(c not in right.columns for c in step.right_on):
                    raise UnsafeProgram("Unknown right join key")
                right = right.loc[right[step.right_on].notna().all(axis=1)]
                if right.duplicated(step.right_on).any():
                    raise UnsafeProgram("Right join keys are not unique; pre-aggregate first")
                if step.relationship == "one_to_one" and frame.duplicated(step.left_on).any():
                    raise UnsafeProgram("Left join keys are not unique")
                renamed = {c: step.right_prefix + c for c in right.columns}
                if len(set(renamed.values())) != len(renamed) or set(renamed.values()) & set(frame):
                    raise UnsafeProgram("Join output column collision")
                right = right.rename(columns=renamed)
                frame = frame.merge(
                    right,
                    how=step.how,
                    left_on=step.left_on,
                    right_on=[renamed[c] for c in step.right_on],
                    sort=False,
                    validate=step.relationship,
                )
        elif step.op == "calculate":
            if not step.output or not step.operator or step.output in frame:
                raise UnsafeProgram("Calculation requires a fresh output column and operator")

            left = _operand(frame, step.left_column, step.left_value)
            right_series = _operand(frame, step.right_column, step.right_value)

            def calculate(pair: tuple[Any, Any], operator: str = str(step.operator)) -> Any:
                lhs, rhs = pair
                if operator == "coalesce":
                    return rhs if pd.isna(lhs) else lhs
                if pd.isna(lhs) or pd.isna(rhs):
                    return None
                if operator in {"eq", "ne", "gt", "ge", "lt", "le"}:
                    return {
                        "eq": lambda: lhs == rhs,
                        "ne": lambda: lhs != rhs,
                        "gt": lambda: lhs > rhs,
                        "ge": lambda: lhs >= rhs,
                        "lt": lambda: lhs < rhs,
                        "le": lambda: lhs <= rhs,
                    }[operator]()
                lhs, rhs = Decimal(str(lhs)), Decimal(str(rhs))
                if operator == "divide":
                    return lhs / rhs if rhs else None
                return {
                    "add": lambda: lhs + rhs,
                    "subtract": lambda: lhs - rhs,
                    "multiply": lambda: lhs * rhs,
                }[operator]()

            frame[step.output] = [calculate(pair) for pair in zip(left, right_series, strict=True)]
        elif step.op == "group_aggregate":
            if not step.aggregations:
                raise UnsafeProgram("Group aggregate requires metrics")
            outputs = [a.output for a in step.aggregations]
            if len(outputs) != len(set(outputs)) or set(outputs) & set(step.by):
                raise UnsafeProgram("Aggregate output collision")
            records = []
            groups = frame.groupby(step.by, dropna=False, sort=False) if step.by else [((), frame)]
            for key, group in groups:
                keys = key if isinstance(key, tuple) else (key,)
                record = dict(zip(step.by, keys, strict=True))
                for aggregation in step.aggregations:
                    if aggregation.function == "count" and aggregation.column is None:
                        record[aggregation.output] = len(group)
                    elif aggregation.column:
                        record[aggregation.output] = _aggregate(
                            group[aggregation.column], aggregation.function
                        )
                    else:
                        raise UnsafeProgram("Aggregate metric needs a source column")
                records.append(record)
            frame = pd.DataFrame(records, columns=[*step.by, *outputs])
        elif step.op == "distinct":
            frame = frame.drop_duplicates(subset=step.columns or None)
        elif step.op == "rename":
            if not step.mapping:
                raise UnsafeProgram("Rename requires a mapping")
            target = [step.mapping.get(str(c), str(c)) for c in frame.columns]
            if len(set(target)) != len(target):
                raise UnsafeProgram("Rename column collision")
            frame = frame.rename(columns=step.mapping)
        elif step.op == "round":
            if not step.column:
                raise UnsafeProgram("Round requires a column")
            quantum = Decimal(1).scaleb(-step.digits)
            frame[step.column] = frame[step.column].map(
                lambda v, quantum=quantum: (
                    None
                    if pd.isna(v)
                    else Decimal(str(v)).quantize(quantum, rounding=ROUND_HALF_UP)
                )
            )
        elif step.op == "date_part":
            if not step.column or not step.part or not step.output or step.output in frame:
                raise UnsafeProgram("Date part requires source, part and fresh output")
            dates = pd.to_datetime(frame[step.column], errors="raise")
            if step.part in {"date", "year_month"}:
                fmt = "%Y-%m-%d" if step.part == "date" else "%Y-%m"
                frame[step.output] = dates.dt.strftime(fmt).where(dates.notna(), None)
            else:
                frame[step.output] = getattr(dates.dt, step.part)
        elif step.op == "conditional":
            if not step.output or step.output in frame or not step.conditions:
                raise UnsafeProgram("Conditional requires fresh output and predicates")
            mask = pd.Series(True, index=frame.index)
            for condition in step.conditions:
                mask &= _condition(frame, condition)
            left = _operand(frame, step.left_column, step.left_value)
            right_series = _operand(frame, step.right_column, step.right_value)
            frame[step.output] = left.where(mask, right_series)
        elif step.op == "values":
            if len(frame) != 1 or not step.columns:
                raise UnsafeProgram("Values requires one aggregate row and ordered columns")
            frame = pd.DataFrame({"value": [frame[c].iloc[0] for c in step.columns]})
        elif step.op == "forward_fill":
            if not step.columns:
                raise UnsafeProgram("Forward fill requires explicit columns")
            frame[step.columns] = (
                frame.groupby(step.by, dropna=False, sort=False)[step.columns].ffill()
                if step.by
                else frame[step.columns].ffill()
            )
        if len(frame) > max_rows:
            raise UnsafeProgram("Intermediate row limit exceeded")
    return frame

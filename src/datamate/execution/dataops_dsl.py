"""Small typed independent DataOps branch. No generated Python or eval."""

from __future__ import annotations

from decimal import localcontext
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from datamate.errors import UnsafeProgram
from datamate.types import AnswerType, Schema


class Operation(Schema):
    op: Literal["filter", "aggregate", "group_sum", "sort", "select"]
    column: str | None = None
    comparator: Literal["eq", "ne", "gt", "ge", "lt", "le"] | None = None
    value: Any = None
    function: Literal["sum", "count", "count_distinct", "min", "max", "mean"] | None = None
    by: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    descending: bool = False


class DataOpsCandidate(Schema):
    candidate_id: str
    source_table: str
    steps: list[Operation] = Field(min_length=1, max_length=20)
    answer_type: AnswerType


def interpret(candidate: DataOpsCandidate, frame: pd.DataFrame) -> pd.DataFrame:
    with localcontext() as context:
        # Source DECIMAL(38,10), bounded row counts: avoid default 28-digit rounding.
        context.prec = 80
        return _interpret(candidate, frame)


def _interpret(candidate: DataOpsCandidate, frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    result: Any
    for step in candidate.steps:
        names = [c for c in [step.column, *step.by, *step.columns] if c is not None]
        if any(c not in frame.columns for c in names):
            raise UnsafeProgram("Unknown DSL column")
        if step.op == "filter":
            if not step.column or not step.comparator:
                raise UnsafeProgram("DSL filter needs a column and comparator")
            method = {"eq": "eq", "ne": "ne", "gt": "gt", "ge": "ge", "lt": "lt", "le": "le"}[
                step.comparator
            ]
            mask = getattr(frame[step.column], method)(step.value)
            frame = frame.loc[mask.fillna(False)]
        elif step.op == "aggregate":
            if step.function == "count" and step.column is None:
                result = len(frame)
            elif step.column and step.function:
                series = frame[step.column]
                if step.function == "sum":
                    result = series.sum(min_count=1)
                elif step.function == "count_distinct":
                    result = series.nunique(dropna=True)
                elif step.function == "mean":
                    nonnull = series.dropna()
                    result = sum(nonnull) / len(nonnull) if len(nonnull) else None
                else:
                    result = getattr(series, step.function)()
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
            frame = frame.sort_values(step.by, ascending=not step.descending, kind="stable")
        elif step.op == "select":
            if not step.columns:
                raise UnsafeProgram("DSL select needs columns")
            frame = frame[step.columns]
    return frame

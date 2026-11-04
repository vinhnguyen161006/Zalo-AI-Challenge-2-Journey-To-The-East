"""Versioned internal contracts; competition serialization belongs to adapters."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AnswerType = Literal["number", "percent", "string", "date", "boolean", "list", "table"]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    schema_version: Literal["1"] = "1"


class QuestionRecord(Schema):
    id: str = Field(min_length=1, max_length=200)
    business_id: str = Field(min_length=1, max_length=200, pattern=r"^[\w-]+$")
    question: str = Field(min_length=1, max_length=16000)
    as_of_date: str
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("as_of_date")
    @classmethod
    def iso_date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Expected YYYY-MM-DD")
        date.fromisoformat(value)
        return value

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question is blank")
        return value


class ColumnCard(Schema):
    name: str
    display_name: str
    source_column: int
    dtype: str
    null_ratio: float
    distinct: int
    samples: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TableCard(Schema):
    table_id: str
    source_file: str
    source_sha256: str
    sheet_or_table: str | None = None
    header_row: int
    source_rows: list[int]
    columns: list[ColumnCard]
    rows: int
    candidate_keys: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class JoinCandidate(Schema):
    left_table: str
    right_table: str
    left_column: str
    right_column: str
    cardinality: Literal["one-to-one", "many-to-one", "one-to-many", "many-to-many"]
    overlap: float
    verified: bool = False


class Catalog(Schema):
    business_id: str
    fingerprint: str
    parser_version: str
    database_path: str
    tables: list[TableCard]
    joins: list[JoinCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_fingerprints: dict[str, str]
    readme: str = ""


class RuleEvidence(Schema):
    rule_id: str
    source_path: str
    line_start: int
    line_end: int
    text: str
    status: Literal["source_only", "compiled"] = "source_only"


class TimeResolution(Schema):
    start_iso: str | None = None
    end_exclusive_iso: str | None = None
    as_of_date: str
    calendar: Literal["solar", "lunar", "mixed"] = "solar"
    evidence: list[str] = Field(default_factory=list)
    ambiguous: bool = False


class GroundingCandidate(Schema):
    table: str
    column: str
    value: str
    score: float
    ambiguous: bool


class FilterIR(Schema):
    field: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "range", "is_null"]
    value: Any = None
    value_ref: Literal["time_window"] | None = None


class MetricIR(Schema):
    name: str
    rule_ids: list[str] = Field(default_factory=list)


class SortIR(Schema):
    field: str
    descending: bool = False


class PlanIR(Schema):
    question_id: str
    intent: Literal["lookup", "count", "aggregate", "rank", "join", "temporal", "other"]
    answer_type: AnswerType
    required_tables: list[str] = Field(min_length=1, max_length=20)
    required_columns: list[str] = Field(default_factory=list, max_length=200)
    rule_ids: list[str] = Field(default_factory=list)
    metrics: list[MetricIR] = Field(default_factory=list)
    filters: list[FilterIR] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    sort: list[SortIR] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=10000)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    complexity: Literal["low", "high"] = "high"


class SqlCandidate(Schema):
    candidate_id: str
    sql: str = Field(min_length=1, max_length=32000)
    strategy: str
    answer_type: AnswerType
    rule_ids: list[str] = Field(default_factory=list)


class ExecutionResult(Schema):
    candidate_id: str
    columns: list[str]
    rows: list[list[Any]]
    program_hash: str
    source_tables: list[str]
    source_columns: list[str] = Field(default_factory=list)
    latency_ms: float
    provenance_level: Literal["table_column", "row"] = "table_column"


class VerificationReport(Schema):
    candidate_id: str
    hard_failures: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    provenance_complete: bool = False
    rule_coverage: float | None = None


class InternalDecision(Schema):
    question_id: str
    status: Literal["answer", "unanswerable", "abstain"]
    answer_type: AnswerType | None = None
    value: Any = None
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float | None = None

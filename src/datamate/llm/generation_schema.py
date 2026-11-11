"""Constrain each pandas operation to fields the interpreter actually consumes."""

from __future__ import annotations

from typing import Any


def share_repeated_column_schemas(payload: dict[str, Any]) -> dict[str, Any]:
    """Losslessly reference repeated workbook schemas while retaining per-table samples."""
    import json

    definitions: dict[str, str] = {}
    tables = []
    for table in payload.get("source_schema", []):
        columns = table["columns"]
        signature = json.dumps(
            [{k: v for k, v in column.items() if k != "samples"} for column in columns],
            sort_keys=True,
            ensure_ascii=False,
        )
        if signature in definitions:
            tables.append(
                {
                    **{k: v for k, v in table.items() if k != "columns"},
                    "column_schema_ref": definitions[signature],
                    "column_samples": [column.get("samples", []) for column in columns],
                }
            )
        else:
            definitions[signature] = table["table_id"]
            tables.append(table)
    return {**payload, "source_schema": tables} if "source_schema" in payload else payload


def pandas_steps_schema() -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    texts = {"type": "array", "items": text, "minItems": 1}
    scalar = {"type": ["string", "number", "boolean"]}

    def operation(name: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"op": {"const": name, "type": "string"}, **properties},
            "required": ["op", *required],
            "additionalProperties": False,
        }

    return {
        "anyOf": [
            operation(
                "aggregate", {"function": {"const": "count", "type": "string"}}, ["function"]
            ),
            operation(
                "aggregate",
                {
                    "function": {
                        "type": "string",
                        "enum": ["sum", "count_distinct", "min", "max", "mean"],
                    },
                    "column": text,
                },
                ["function", "column"],
            ),
            operation(
                "filter",
                {
                    "column": text,
                    "comparator": {"type": "string", "enum": ["eq", "ne", "gt", "ge", "lt", "le"]},
                    "value": scalar,
                },
                ["column", "comparator", "value"],
            ),
            operation(
                "filter",
                {
                    "column": text,
                    "comparator": {"type": "string", "enum": ["in", "not_in"]},
                    "value": {"type": "array", "items": scalar, "minItems": 1},
                },
                ["column", "comparator", "value"],
            ),
            operation("group_sum", {"by": texts, "column": text}, ["by", "column"]),
            operation(
                "sort", {"by": texts, "descending": {"type": "boolean"}}, ["by", "descending"]
            ),
            operation(
                "sort",
                {
                    "by": texts,
                    "descending_by": {"type": "array", "items": {"type": "boolean"}, "minItems": 1},
                },
                ["by", "descending_by"],
            ),
            operation("select", {"columns": texts}, ["columns"]),
            operation(
                "limit", {"count": {"type": "integer", "minimum": 1, "maximum": 10000}}, ["count"]
            ),
            operation(
                "filter",
                {"column": text, "comparator": {"type": "string", "enum": ["is_null", "not_null"]}},
                ["column", "comparator"],
            ),
            operation(
                "filter",
                {
                    "column": text,
                    "comparator": {"const": "contains", "type": "string"},
                    "value": text,
                },
                ["column", "comparator", "value"],
            ),
            operation(
                "join",
                {
                    "right_table": text,
                    "left_on": texts,
                    "right_on": texts,
                    "how": {"type": "string", "enum": ["inner", "left"]},
                    "right_prefix": text,
                    "relationship": {"type": "string", "enum": ["many_to_one", "one_to_one"]},
                },
                ["right_table", "left_on", "right_on", "how", "right_prefix", "relationship"],
            ),
            operation("union", {"right_table": text}, ["right_table"]),
            operation(
                "calculate",
                {
                    "output": text,
                    "operator": {
                        "type": "string",
                        "enum": [
                            "add",
                            "subtract",
                            "multiply",
                            "divide",
                            "coalesce",
                            "eq",
                            "ne",
                            "gt",
                            "ge",
                            "lt",
                            "le",
                        ],
                    },
                    "left_column": {"type": ["string", "null"]},
                    "right_column": {"type": ["string", "null"]},
                    "left_value": {"type": ["number", "string", "null"]},
                    "right_value": {"type": ["number", "string", "null"]},
                },
                ["output", "operator", "left_column", "right_column", "left_value", "right_value"],
            ),
            operation(
                "group_aggregate",
                {
                    "by": {"type": "array", "items": text},
                    "aggregations": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {"$ref": "#/$defs/Aggregation"},
                    },
                },
                ["by", "aggregations"],
            ),
            operation("distinct", {"columns": {"type": "array", "items": text}}, ["columns"]),
            operation(
                "rename",
                {"mapping": {"type": "object", "additionalProperties": text, "minProperties": 1}},
                ["mapping"],
            ),
            operation(
                "round",
                {"column": text, "digits": {"type": "integer", "minimum": -6, "maximum": 10}},
                ["column", "digits"],
            ),
            operation(
                "date_part",
                {
                    "column": text,
                    "output": text,
                    "part": {
                        "type": "string",
                        "enum": ["year", "month", "day", "weekday", "date", "year_month"],
                    },
                },
                ["column", "output", "part"],
            ),
            operation(
                "conditional",
                {
                    "output": text,
                    "conditions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {"$ref": "#/$defs/Condition"},
                    },
                    "left_column": {"type": ["string", "null"]},
                    "right_column": {"type": ["string", "null"]},
                    "left_value": {"type": ["number", "string", "null"]},
                    "right_value": {"type": ["number", "string", "null"]},
                },
                [
                    "output",
                    "conditions",
                    "left_column",
                    "right_column",
                    "left_value",
                    "right_value",
                ],
            ),
            operation("values", {"columns": texts}, ["columns"]),
            operation(
                "forward_fill",
                {"by": {"type": "array", "items": text}, "columns": texts},
                ["by", "columns"],
            ),
        ]
    }

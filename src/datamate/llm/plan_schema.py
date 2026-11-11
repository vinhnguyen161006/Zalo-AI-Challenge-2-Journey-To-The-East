"""Constrain PlanIR source references and separate literal predicates from time windows."""

from __future__ import annotations

import re
from typing import Any


def constrain_plan(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    columns = [
        f"{t['table_id']}.{c['name']}" for t in payload["source_schema"] for c in t["columns"]
    ]
    dates = [
        f"{t['table_id']}.{c['name']}"
        for t in payload["source_schema"]
        for c in t["columns"]
        if c.get("samples")
        and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", str(v)) for v in c["samples"])
    ]
    rules = [r["rule_id"] for r in payload["source_rules"]]
    schema["properties"]["group_by"]["items"]["enum"] = columns
    if rules:
        schema["properties"]["rule_ids"]["items"]["enum"] = rules
    else:
        schema["properties"]["rule_ids"]["maxItems"] = 0
    metric = schema["$defs"]["MetricIR"]
    metric["properties"]["field"]["enum"] = [*columns, None]
    if rules:
        metric["properties"]["rule_ids"]["items"]["enum"] = rules
    else:
        metric["properties"]["rule_ids"]["maxItems"] = 0
    metric["required"] = ["name", "function", "field", "rule_ids"]
    sorting = schema["$defs"]["SortIR"]
    sorting["properties"]["field"]["enum"] = columns
    sorting["required"] = ["field", "descending"]
    field = {"type": "string", "enum": columns}
    scalar = {"type": ["string", "number", "boolean"]}

    def predicate(
        op: dict[str, Any], value: dict[str, Any], *, time: bool = False
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {"type": "string", "enum": dates} if time else field,
                "op": op,
                "value": value,
                "value_ref": {"type": "string", "const": "time_window"}
                if time
                else {"type": "null"},
            },
            "required": ["field", "op", "value", "value_ref"],
        }

    choices = [
        predicate({"type": "string", "enum": ["eq", "ne", "gt", "gte", "lt", "lte"]}, scalar),
        predicate(
            {"type": "string", "const": "in"}, {"type": "array", "items": scalar, "minItems": 1}
        ),
        predicate(
            {"type": "string", "const": "range"},
            {"type": "array", "items": scalar, "minItems": 2, "maxItems": 2},
        ),
        predicate({"type": "string", "const": "is_null"}, {"type": "null"}),
    ]
    if dates:
        choices.append(predicate({"type": "string", "const": "range"}, {"type": "null"}, time=True))
    schema["$defs"]["FilterIR"] = {"anyOf": choices}

from typing import Any

from datamate.config import Limits
from datamate.llm.agents import ask
from datamate.llm.generation_schema import pandas_steps_schema, share_repeated_column_schemas
from datamate.routing.budgets import BudgetManager
from datamate.types import SqlCandidate


def test_sql_generation_requires_exact_plan_rule_references() -> None:
    class Capture:
        def generate_structured(
            self, messages: list[dict[str, str]], schema: dict[str, Any], *, timeout: float
        ) -> dict[str, Any]:
            assert schema["properties"]["rule_ids"]["const"] == ["rule:1"]
            assert "rule_ids" in schema["required"]
            return {
                "candidate_id": "sql",
                "sql": "SELECT 1",
                "strategy": "direct",
                "answer_type": "number",
                "rule_ids": ["rule:1"],
            }

    candidate = ask(
        Capture(),
        "sql_coder",
        {"question": {}, "validated_plan": {"answer_type": "number", "rule_ids": ["rule:1"]}},
        SqlCandidate,
        BudgetManager(Limits()),
    )
    assert candidate.rule_ids == ["rule:1"]


def test_pandas_generation_schema_requires_consumed_operands() -> None:
    variants = pandas_steps_schema()["anyOf"]
    sums = [v for v in variants if "sum" in v["properties"].get("function", {}).get("enum", [])]
    assert len(sums) == 1
    assert "column" in sums[0]["required"]
    assert "columns" not in sums[0]["properties"]
    membership = [
        v for v in variants if "in" in v["properties"].get("comparator", {}).get("enum", [])
    ]
    assert len(membership) == 1
    assert membership[0]["properties"]["value"]["type"] == "array"
    assert all(v["additionalProperties"] is False for v in variants)


def test_shared_schema_preserves_distinct_source_samples_and_columns() -> None:
    payload = {
        "source_schema": [
            {
                "table_id": "jan",
                "rows": 10,
                "columns": [{"name": "amount", "dtype": "BIGINT", "samples": [1]}],
            },
            {
                "table_id": "feb",
                "rows": 20,
                "columns": [{"name": "amount", "dtype": "BIGINT", "samples": [2]}],
            },
            {
                "table_id": "other",
                "rows": 1,
                "columns": [{"name": "name", "dtype": "VARCHAR", "samples": ["A"]}],
            },
        ]
    }
    result = share_repeated_column_schemas(payload)
    assert result["source_schema"][1] == {
        "table_id": "feb",
        "rows": 20,
        "column_schema_ref": "jan",
        "column_samples": [[2]],
    }
    assert result["source_schema"][2] == payload["source_schema"][2]
    assert "columns" in payload["source_schema"][1]

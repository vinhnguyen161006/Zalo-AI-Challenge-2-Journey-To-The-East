from datamate.llm.plan_schema import constrain_plan
from datamate.types import PlanIR


def test_generation_cannot_apply_time_window_to_status_or_paid_amount() -> None:
    schema = PlanIR.model_json_schema()
    payload = {
        "source_schema": [
            {
                "table_id": "orders",
                "columns": [
                    {"name": "date", "samples": ["2026-01-01"]},
                    {"name": "status", "samples": ["complete"]},
                    {"name": "paid", "samples": ["100"]},
                ],
            }
        ],
        "source_rules": [],
    }
    constrain_plan(schema, payload)
    filters = schema["$defs"]["FilterIR"]["anyOf"]
    windows = [v for v in filters if v["properties"]["value_ref"].get("const") == "time_window"]
    assert len(windows) == 1
    assert windows[0]["properties"]["field"]["enum"] == ["orders.date"]
    assert windows[0]["properties"]["op"]["const"] == "range"
    assert schema["properties"]["rule_ids"]["maxItems"] == 0
    assert "enum" not in schema["properties"]["rule_ids"]["items"]
    assert schema["$defs"]["SortIR"]["properties"]["field"]["enum"] == [
        "orders.date",
        "orders.status",
        "orders.paid",
    ]

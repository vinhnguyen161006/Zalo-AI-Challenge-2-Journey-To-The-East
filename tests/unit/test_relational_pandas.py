from decimal import Decimal

import pandas as pd
import pytest

from datamate.errors import UnsafeProgram
from datamate.execution.dataops_dsl import DataOpsCandidate, interpret


def run(frame: pd.DataFrame, steps: list[dict], **kwargs: object) -> pd.DataFrame:
    candidate = DataOpsCandidate.model_validate(
        {
            "candidate_id": "synthetic",
            "source_table": "sales",
            "steps": steps,
            "answer_type": "table",
        }
    )
    return interpret(candidate, frame, **kwargs)


def test_join_null_keys_do_not_match_and_duplicate_dimension_is_rejected() -> None:
    sales = pd.DataFrame({"code": ["a", None, "b"], "amount": [10, 20, 30]})
    products = pd.DataFrame({"code": ["a", None], "name": ["Apple", "Unknown"]})
    step = {
        "op": "join",
        "right_table": "products",
        "left_on": ["code"],
        "right_on": ["code"],
        "right_prefix": "product_",
        "how": "left",
    }
    joined = run(sales, [step], sources={"products": products})
    assert len(joined) == 3
    assert joined["product_name"].iloc[0] == "Apple"
    assert joined["product_name"].iloc[1:].isna().all()
    duplicated = pd.DataFrame({"code": ["a", "a"], "name": ["Apple", "Other"]})
    with pytest.raises(UnsafeProgram, match="not unique"):
        run(sales, [step], sources={"products": duplicated})


def test_calculation_grouping_money_rounding_and_zero_division() -> None:
    frame = pd.DataFrame(
        {
            "product": ["a", "a", "b"],
            "qty": [2, 3, 0],
            "price": [Decimal("1.25"), Decimal("1.5"), Decimal("5")],
        }
    )
    result = run(
        frame,
        [
            {
                "op": "calculate",
                "output": "revenue",
                "operator": "multiply",
                "left_column": "qty",
                "right_column": "price",
            },
            {
                "op": "group_aggregate",
                "by": ["product"],
                "aggregations": [
                    {"output": "total", "function": "sum", "column": "revenue"},
                    {"output": "orders", "function": "count"},
                ],
            },
            {"op": "round", "column": "total", "digits": 0},
        ],
    )
    assert result.to_dict("records") == [
        {"product": "a", "total": Decimal(7), "orders": 2},
        {"product": "b", "total": Decimal(0), "orders": 1},
    ]
    result = run(
        frame,
        [
            {
                "op": "calculate",
                "output": "ratio",
                "operator": "divide",
                "left_column": "price",
                "right_column": "qty",
            }
        ],
    )
    assert result["ratio"].iloc[2] is None


def test_union_is_all_and_budget_is_enforced_before_concat() -> None:
    frame = pd.DataFrame({"x": [1, 1]})
    union = [{"op": "union", "right_table": "next"}]
    assert len(run(frame, union, sources={"next": frame})) == 4
    with pytest.raises(UnsafeProgram, match="row limit"):
        run(frame, union, sources={"next": frame}, max_rows=3)
    with pytest.raises(UnsafeProgram, match="matching columns"):
        run(frame, union, sources={"next": pd.DataFrame({"y": [1]})})


def test_null_filter_dates_and_half_up_rounding() -> None:
    frame = pd.DataFrame({"date": ["2024-02-29", None], "money": [Decimal("2.5"), Decimal("-2.5")]})
    rounded = run(frame, [{"op": "round", "column": "money", "digits": 0}])
    assert rounded["money"].tolist() == [Decimal(3), Decimal(-3)]
    result = run(
        frame,
        [
            {"op": "filter", "column": "date", "comparator": "not_null"},
            {"op": "date_part", "column": "date", "output": "month", "part": "year_month"},
        ],
    )
    assert result["month"].tolist() == ["2024-02"]


def test_preaggregate_dimension_before_join() -> None:
    orders = pd.DataFrame({"order_id": ["a", "b"], "amount": [100, 200]})
    payments = pd.DataFrame({"order_id": ["a", "a"], "paid": [20, 30]})
    candidate = DataOpsCandidate.model_validate(
        {
            "candidate_id": "debt",
            "source_table": "orders",
            "answer_type": "table",
            "preparations": [
                {
                    "source_table": "payments",
                    "steps": [
                        {
                            "op": "group_aggregate",
                            "by": ["order_id"],
                            "aggregations": [
                                {"output": "paid", "column": "paid", "function": "sum"}
                            ],
                        }
                    ],
                }
            ],
            "steps": [
                {
                    "op": "join",
                    "right_table": "payments",
                    "left_on": ["order_id"],
                    "right_on": ["order_id"],
                    "how": "left",
                    "right_prefix": "payment_",
                }
            ],
        }
    )
    result = interpret(candidate, orders, {"orders": orders, "payments": payments})
    assert len(result) == 2 and result["payment_paid"].iloc[0] == 50
    assert len(payments) == 2


def test_percentage_and_ordered_multi_value_answer() -> None:
    frame = pd.DataFrame({"status": ["complete", "cancelled", "returned", "delivering"]})
    grouped = [
        {
            "op": "conditional",
            "output": "cancelled",
            "conditions": [{"column": "status", "comparator": "eq", "value": "cancelled"}],
            "left_value": 1,
            "right_value": 0,
        },
        {
            "op": "group_aggregate",
            "by": [],
            "aggregations": [
                {"output": "cancelled", "function": "sum", "column": "cancelled"},
                {"output": "all_orders", "function": "count"},
            ],
        },
    ]
    result = run(
        frame,
        [
            *grouped,
            {
                "op": "calculate",
                "output": "ratio",
                "operator": "divide",
                "left_column": "cancelled",
                "right_column": "all_orders",
            },
            {
                "op": "calculate",
                "output": "percent",
                "operator": "multiply",
                "left_column": "ratio",
                "right_value": 100,
            },
            {"op": "select", "columns": ["percent"]},
        ],
    )
    assert result.iloc[0, 0] == Decimal(25)
    assert run(frame, [*grouped, {"op": "values", "columns": ["all_orders", "cancelled"]}])[
        "value"
    ].tolist() == [4, 1]


def test_boolean_and_fill_within_group_only() -> None:
    frame = pd.DataFrame({"order_id": ["a", "a", "b"], "customer": ["Lan", None, None]})
    filled = run(frame, [{"op": "forward_fill", "by": ["order_id"], "columns": ["customer"]}])
    assert filled["customer"].iloc[1] == "Lan"
    assert pd.isna(filled["customer"].iloc[2])
    result = run(
        frame,
        [
            {"op": "aggregate", "function": "count"},
            {
                "op": "calculate",
                "output": "positive",
                "operator": "gt",
                "left_column": "value",
                "right_value": 0,
            },
            {"op": "select", "columns": ["positive"]},
        ],
    )
    assert bool(result.iloc[0, 0]) is True

import json

from datamate.config import Config
from datamate.llm.agents import validate_plan
from datamate.llm.client import FakeModelClient, VLLMModelClient
from datamate.pipeline import Pipeline
from datamate.types import Catalog, PlanIR, QuestionRecord


def question() -> QuestionRecord:
    return QuestionRecord(
        id="q",
        business_id="biz_demo",
        question="Đếm bản ghi trong bảng orders",
        as_of_date="2026-01-10",
    )


def test_fake_model_cpu_pipeline(config: Config, catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="q",
        intent="count",
        answer_type="number",
        required_tables=[table],
        complexity="low",
    )
    planner = FakeModelClient([plan.model_dump()])
    reasoner = FakeModelClient(
        [
            {
                "candidate_id": "sql",
                "sql": f"SELECT COUNT(*) FROM {table}",
                "strategy": "direct",
                "answer_type": "number",
            }
        ]
    )
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs["biz_demo"] = catalog
    decision = pipeline.solve(question())
    assert decision.status == "abstain"
    assert "uncalibrated_policy" in decision.reason_codes
    assert pipeline.last_trace["route"] == "FAST"
    assert pipeline.last_trace["candidate_summaries"][0]["status"] == "ok"
    assert pipeline.last_trace["model_calls"] == 2
    assert decision.confidence is None


def test_invalid_planner_repair_is_bounded(config: Config) -> None:
    planner = FakeModelClient([{}, {}])
    pipeline = Pipeline(config, planner, FakeModelClient([]))
    decision = pipeline.solve(question())
    assert decision.status == "abstain"
    assert len(planner.requests) == 2


def test_clean_room_branch(config: Config, catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="q",
        intent="count",
        answer_type="number",
        required_tables=[table],
        complexity="high",
    )
    planner = FakeModelClient(
        [
            plan.model_dump(),
            {
                "candidate_id": "dsl",
                "source_table": table,
                "steps": [{"op": "aggregate", "function": "count"}],
                "answer_type": "number",
            },
        ]
    )
    reasoner = FakeModelClient(
        [
            {
                "candidate_id": f"sql_{i}",
                "sql": f"SELECT COUNT(*) FROM {table}",
                "strategy": "direct",
                "answer_type": "number",
            }
            for i in range(3)
        ]
    )
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs["biz_demo"] = catalog
    pipeline.solve(question())
    payload = json.loads(planner.requests[-1][1]["content"])
    assert set(payload) == {
        "question",
        "source_schema",
        "source_readme",
        "source_rules",
        "time_evidence",
    }
    assert "sql_candidate" not in payload and "validated_plan" not in payload
    assert "independent_dsl" in pipeline.last_trace
    assert pipeline.last_trace["cross_paradigm_agreement"] is True


def test_disagreement_does_not_get_voted_away(config: Config, catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="q",
        intent="count",
        answer_type="number",
        required_tables=[table],
        complexity="high",
    )
    planner = FakeModelClient(
        [
            plan.model_dump(),
            {
                "candidate_id": "dsl",
                "source_table": table,
                "steps": [
                    {"op": "filter", "column": "status", "comparator": "eq", "value": "complete"},
                    {"op": "aggregate", "function": "count"},
                ],
                "answer_type": "number",
            },
        ]
    )
    reasoner = FakeModelClient(
        [
            {
                "candidate_id": f"sql_{i}",
                "sql": f"SELECT COUNT(*) FROM {table}",
                "strategy": "direct",
                "answer_type": "number",
            }
            for i in range(3)
        ]
    )
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs["biz_demo"] = catalog
    decision = pipeline.solve(question())
    assert "cross_paradigm_disagreement" in decision.reason_codes
    assert pipeline.last_trace["cross_paradigm_agreement"] is False


def test_fast_failure_escalates_within_budget(config: Config, catalog: Catalog) -> None:
    config.limits.max_repairs = 0
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="q",
        intent="count",
        answer_type="number",
        required_tables=[table],
        complexity="low",
    )
    planner = FakeModelClient(
        [
            plan.model_dump(),
            {
                "candidate_id": "dsl",
                "source_table": table,
                "steps": [{"op": "aggregate", "function": "count"}],
                "answer_type": "number",
            },
        ]
    )
    reasoner = FakeModelClient(
        [
            {
                "candidate_id": "bad",
                "sql": f"SELECT nonexistent FROM {table}",
                "strategy": "direct",
                "answer_type": "number",
            },
            *[
                {
                    "candidate_id": f"sql_{i}",
                    "sql": f"SELECT COUNT(*) FROM {table}",
                    "strategy": "ctes",
                    "answer_type": "number",
                }
                for i in range(2)
            ],
        ]
    )
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs["biz_demo"] = catalog
    pipeline.solve(question())
    assert pipeline.last_trace["route"] == "SLOW"
    assert "prior_execution_or_verification_failure" in pipeline.last_trace["route_reasons"]
    assert pipeline.last_trace["model_calls"] <= config.limits.max_model_calls


def test_nonexistent_column_is_rejected(catalog: Catalog) -> None:
    plan = PlanIR(
        question_id="q",
        intent="count",
        answer_type="number",
        required_tables=[catalog.tables[0].table_id],
        required_columns=["invented.salary"],
    )
    assert "unknown_or_unqualified_column" in validate_plan(plan, question(), catalog, [])


def test_cloud_endpoint_rejected() -> None:
    import pytest

    from datamate.errors import ModelUnavailable

    with pytest.raises(ModelUnavailable):
        VLLMModelClient("https://api.example.com/v1/chat/completions", "model")

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from datamate.adapters.submission_writer import read_predictions, write_predictions
from datamate.config import Config
from datamate.errors import ModelUnavailable
from datamate.llm.client import FakeModelClient, OllamaModelClient
from datamate.pipeline import Pipeline
from datamate.types import Catalog, InternalDecision, MetricIR, PlanIR, QuestionRecord
from datamate.verification.joins import has_unique_right_keys


def test_baseline_executes_answer_and_logs_evidence(config: Config, catalog: Catalog) -> None:
    config.decision_policy = "execution_baseline"
    config.log_programs = True
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    question = QuestionRecord(
        id="count", business_id="biz_demo", question="Đếm bản ghi orders", as_of_date="2026-01-10"
    )
    plan = PlanIR(
        question_id=question.id,
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
                "candidate_id": "sql",
                "sql": f"SELECT COUNT(*) FROM {table}",
                "strategy": "direct",
                "answer_type": "number",
            }
        ]
    )
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs[question.business_id] = catalog
    decision = pipeline.solve(question)
    assert decision.status == "answer"
    assert decision.value == 4
    assert decision.confidence is None
    assert pipeline.last_trace["cross_paradigm_agreement"] is True
    assert pipeline.last_trace["final_answer"] == {"id": "count", "type": "number", "answer": 4}
    assert list((config.output_root / "logs").glob("*.json"))


def test_join_uniqueness_does_not_accept_payment_fanout(catalog: Catalog) -> None:
    orders = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    payments = next(t.table_id for t in catalog.tables if t.source_file == "payments.csv")
    assert has_unique_right_keys(
        f"SELECT p.amount FROM {payments} p JOIN {orders} o ON p.order_id = o.order_id", catalog
    )
    assert not has_unique_right_keys(
        f"SELECT o.amount FROM {orders} o JOIN {payments} p ON p.order_id = o.order_id", catalog
    )
    assert not has_unique_right_keys(
        f"SELECT p.amount FROM {payments} p JOIN {orders} o ON p.order_id = o.order_id OR 1=1",
        catalog,
    )


def test_published_submission_survives_interrupt_and_resumes(tmp_path: Path) -> None:
    questions = [
        QuestionRecord(id=str(i), business_id="b", question="count", as_of_date="2026-01-01")
        for i in range(3)
    ]
    path = tmp_path / "submission.jsonl"

    def crash(q: QuestionRecord) -> InternalDecision:
        if q.id == "1":
            raise RuntimeError("interrupted")
        return InternalDecision(question_id=q.id, status="answer", answer_type="number", value=5)

    with pytest.raises(RuntimeError):
        write_predictions(path, questions, crash, "fp", publish_progress=True)
    assert read_predictions(path) == [{"id": "0", "type": "number", "answer": 5}]
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"id":')

    def remaining(q: QuestionRecord) -> InternalDecision:
        assert q.id != "0"
        return InternalDecision(question_id=q.id, status="abstain")

    assert (
        write_predictions(path, questions, remaining, "fp", resume=True, publish_progress=True) == 3
    )
    assert [r["id"] for r in read_predictions(path)] == ["0", "1", "2"]
    assert (
        write_predictions(path, questions, remaining, "fp", resume=True, publish_progress=True) == 3
    )


def test_ollama_uses_schema_and_seed_without_cloud() -> None:
    client = OllamaModelClient("http://127.0.0.1:11434/api/chat", "local", 123, 8192, 7)
    schema = {"type": "object"}
    payload = client.payload([], schema)
    assert payload["format"] == schema
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0, "seed": 7, "num_predict": 123, "num_ctx": 8192}


def test_ollama_rejects_unpinned_or_missing_weights_before_prediction() -> None:
    client = OllamaModelClient("http://127.0.0.1:11434/api/chat", "local", 128, 8192, 7)
    body = b'{"models":[{"name":"local","digest":"revision"}]}'
    with patch.object(client.opener, "open", return_value=BytesIO(body)):
        client.verify_installed("revision")
    with patch.object(client.opener, "open", return_value=BytesIO(body)):
        with pytest.raises(ModelUnavailable, match="digest"):
            client.verify_installed("different")
    with patch.object(client.opener, "open", return_value=BytesIO(b'{"models":[]}')):
        with pytest.raises(ModelUnavailable, match="not installed"):
            client.verify_installed("revision")


def test_missing_measure_does_not_execute_an_unrelated_metric(
    config: Config, catalog: Catalog
) -> None:
    config.decision_policy = "execution_baseline"
    table = catalog.tables[0].table_id
    question = QuestionRecord(
        id="missing_measure",
        business_id="biz_demo",
        question="Room humidity?",
        as_of_date="2026-01-10",
    )
    plan = PlanIR(
        question_id=question.id,
        intent="other",
        answer_type="number",
        required_tables=[table],
        unknowns=["No humidity measurements in sources"],
    )
    planner = FakeModelClient([plan.model_dump()])
    reasoner = FakeModelClient([])
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs[question.business_id] = catalog
    assert pipeline.solve(question).status == "abstain"
    assert not reasoner.requests


def test_fast_template_keeps_pandas_independent_and_skips_reasoner(
    config: Config, catalog: Catalog
) -> None:
    config.decision_policy = "execution_baseline"
    config.use_verified_templates = True
    config.pandas_first = True
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    q = QuestionRecord(
        id="records",
        business_id="biz_demo",
        question="Count source records",
        as_of_date="2026-01-10",
    )
    plan = PlanIR(
        question_id=q.id,
        intent="count",
        answer_type="number",
        required_tables=[table],
        metrics=[MetricIR(name="records", function="count")],
        complexity="low",
    )
    planner = FakeModelClient(
        [
            plan.model_dump(),
            {
                "candidate_id": "independent",
                "source_table": table,
                "steps": [{"op": "aggregate", "function": "count"}],
                "answer_type": "number",
            },
        ]
    )
    reasoner = FakeModelClient([])
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs[q.business_id] = catalog
    result = pipeline.solve(q)
    assert result.status == "answer" and result.value == 4
    assert not reasoner.requests
    assert pipeline.last_trace["model_calls"] == 2


def test_reasoner_recovers_only_after_two_invalid_planner_contracts(
    config: Config, catalog: Catalog
) -> None:
    config.decision_policy = "execution_baseline"
    config.planner_critic = True
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    q = QuestionRecord(
        id="records", business_id="biz_demo", question="Count records", as_of_date="2026-01-10"
    )
    good = PlanIR(
        question_id=q.id,
        intent="count",
        answer_type="number",
        required_tables=[table],
        complexity="low",
    )
    bad = good.model_copy(update={"required_columns": ["not_a_source_column"]})
    planner = FakeModelClient(
        [
            bad.model_dump(),
            bad.model_dump(),
            {
                "candidate_id": "independent",
                "source_table": table,
                "steps": [{"op": "aggregate", "function": "count"}],
                "answer_type": "number",
            },
        ]
    )
    reasoner = FakeModelClient(
        [
            good.model_dump(),
            {
                "candidate_id": "sql",
                "sql": f"SELECT COUNT(*) FROM {table}",
                "strategy": "direct",
                "answer_type": "number",
            },
        ]
    )
    pipeline = Pipeline(config, planner, reasoner)
    pipeline.catalogs[q.business_id] = catalog
    decision = pipeline.solve(q)
    assert decision.status == "answer" and decision.value == 4
    assert pipeline.last_trace["planner_recovery"] == "reasoner_checkpoint_after_two_invalid_plans"
    assert pipeline.last_trace["model_calls"] == 5

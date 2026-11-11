from datamate.config import Limits
from datamate.execution.sql_executor import execute
from datamate.execution.templates import ranking_template, scalar_template
from datamate.types import Catalog, FilterIR, MetricIR, PlanIR, SortIR, TimeResolution


def test_typed_template_applies_exact_plan_filters_without_generation(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    time = TimeResolution(
        as_of_date="2026-01-10", start_iso="2026-01-01", end_exclusive_iso="2026-01-11"
    )
    plan = PlanIR(
        question_id="test",
        intent="count",
        answer_type="number",
        required_tables=[table],
        metrics=[MetricIR(name="count", function="count")],
        filters=[
            FilterIR(field=f"{table}.status", op="in", value=["complete", "delivering"]),
            FilterIR(field=f"{table}.order_date", op="range", value_ref="time_window"),
        ],
    )
    candidate = scalar_template(plan, catalog, time)
    assert candidate is not None
    assert execute(candidate, catalog, Limits()).rows == [[2]]
    malicious = plan.model_copy(
        update={
            "filters": [
                FilterIR(field=f"{table}.status", op="eq", value="x'; DROP TABLE orders; --")
            ]
        }
    )
    safe = scalar_template(malicious, catalog, time)
    assert safe is not None and execute(safe, catalog, Limits()).rows == [[0]]


def test_templates_do_not_guess_missing_formulas(catalog: Catalog) -> None:
    plan = PlanIR(
        question_id="missing",
        intent="aggregate",
        answer_type="number",
        required_tables=[catalog.tables[0].table_id],
        metrics=[MetricIR(name="profit without source cost")],
    )
    assert scalar_template(plan, catalog, TimeResolution(as_of_date="2026-01-10")) is None


def test_rank_template_selects_only_requested_date(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="rank",
        intent="rank",
        answer_type="date",
        required_tables=[table],
        metrics=[MetricIR(name="sales", function="sum", field=f"{table}.amount")],
        filters=[FilterIR(field=f"{table}.status", op="in", value=["complete", "delivering"])],
        group_by=[f"{table}.order_date"],
        sort=[SortIR(field=f"{table}.amount", descending=True)],
        limit=1,
    )
    candidate = ranking_template(plan, catalog, TimeResolution(as_of_date="2026-01-10"))
    assert candidate is not None and candidate.answer_type == "date"
    assert execute(candidate, catalog, Limits()).rows == [["2026-01-01"]]

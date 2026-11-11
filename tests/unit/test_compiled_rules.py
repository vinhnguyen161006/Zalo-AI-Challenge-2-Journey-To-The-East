from datamate.semantics.compiled_rules import (
    bind_resolved_time_filters,
    compile_period_constraint,
    compile_revenue_measure,
    compile_sales_validity,
)
from datamate.types import (
    Catalog,
    FilterIR,
    MetricIR,
    PlanIR,
    QuestionRecord,
    RuleEvidence,
    TimeResolution,
)


def test_only_exact_resolved_iso_window_is_bound(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    time = TimeResolution(
        as_of_date="2026-01-10", start_iso="2026-01-01", end_exclusive_iso="2026-01-11"
    )
    plan = PlanIR(
        question_id="window",
        intent="aggregate",
        answer_type="number",
        required_tables=[table],
        filters=[
            FilterIR(field=f"{table}.order_date", op="range", value=["2026-01-01", "2026-01-11"])
        ],
    )
    bound, fields = bind_resolved_time_filters(plan, catalog, time)
    assert fields == [f"{table}.order_date"]
    assert bound.filters[0].value_ref == "time_window" and bound.filters[0].value is None
    assert plan.filters[0].value_ref is None
    literal = plan.model_copy(
        update={
            "filters": [
                FilterIR(
                    field=f"{table}.order_date", op="range", value=["2026-01-01", "2026-01-10"]
                )
            ]
        }
    )
    assert not bind_resolved_time_filters(literal, catalog, time)[1]
    assert not bind_resolved_time_filters(
        plan, catalog, time.model_copy(update={"ambiguous": True})
    )[1]


def test_revenue_field_requires_matching_rule_and_source_definition(catalog: Catalog) -> None:
    source = catalog.model_copy(deep=True)
    table = next(t for t in source.tables if t.source_file == "orders.csv")
    amount = next(c for c in table.columns if c.name == "amount")
    table.columns.append(
        amount.model_copy(update={"name": "tien_hang", "display_name": "Tiền hàng"})
    )
    source.readme = (
        "## Cột trong `orders.csv`\n- **Tiền hàng**: cộng thành tiền trước giảm giá đơn\n"
    )
    rule = RuleEvidence(
        rule_id="revenue",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="Doanh thu một đơn = thành tiền; không trừ voucher, không gồm phí giao.",
    )
    q = QuestionRecord(
        id="revenue",
        business_id="synthetic",
        question="Doanh thu hôm nay?",
        as_of_date="2026-01-10",
    )
    plan = PlanIR(
        question_id=q.id,
        intent="aggregate",
        answer_type="number",
        required_tables=[table.table_id],
        metrics=[MetricIR(name="revenue", function="sum", field=f"{table.table_id}.amount")],
    )
    compiled, evidence = compile_revenue_measure(plan, q, source, [rule])
    assert compiled.metrics[0].field == f"{table.table_id}.tien_hang"
    assert evidence[0]["rule_id"] == "revenue"
    assert not compile_revenue_measure(plan, q, source, [])[1]
    override = q.model_copy(update={"question": "Doanh thu sau giảm giá?"})
    assert not compile_revenue_measure(plan, override, source, [rule])[1]
    source.readme = ""
    assert not compile_revenue_measure(plan, q, source, [rule])[1]


def test_status_fragment_requires_cited_rule_and_order_grain(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    q = QuestionRecord(
        id="revenue", business_id="biz_demo", question="Total revenue", as_of_date="2026-01-10"
    )
    rule = RuleEvidence(
        rule_id="rule:1",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="Valid sales have status complete or delivering; exclude cancelled and returned.",
    )
    plan = PlanIR(
        question_id=q.id,
        intent="aggregate",
        answer_type="number",
        required_tables=[table],
        rule_ids=[rule.rule_id],
    )
    compiled, evidence = compile_sales_validity(plan, q, catalog, [rule])
    assert len(compiled.filters) == 1 and set(compiled.filters[0].value) == {
        "complete",
        "delivering",
    }
    assert evidence[0]["rule_ids"] == [rule.rule_id]
    assert not compile_sales_validity(plan.model_copy(update={"rule_ids": []}), q, catalog, [rule])[
        1
    ]
    literal = q.model_copy(update={"question": "Count source records"})
    assert not compile_sales_validity(plan, literal, catalog, [rule])[1]
    changed_rule = rule.model_copy(
        update={"text": "Only completed sales count, delivering is excluded"}
    )
    assert not compile_sales_validity(plan, q, catalog, [changed_rule])[1]


def test_only_known_transaction_date_gets_compiled_window(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    q = QuestionRecord(
        id="period", business_id="biz_demo", question="Doanh thu tháng này", as_of_date="2026-01-10"
    )
    plan = PlanIR(
        question_id=q.id, intent="aggregate", answer_type="number", required_tables=[table]
    )
    time = TimeResolution(
        as_of_date=q.as_of_date, start_iso="2026-01-01", end_exclusive_iso="2026-01-11"
    )
    proposed, evidence = compile_period_constraint(plan, q, catalog, time)
    assert proposed.filters[0].field == f"{table}.order_date"
    assert proposed.filters[0].value_ref == "time_window"
    assert evidence
    delivery = q.model_copy(update={"question": "Số đợt giao tháng này"})
    assert not compile_period_constraint(plan, delivery, catalog, time)[1]

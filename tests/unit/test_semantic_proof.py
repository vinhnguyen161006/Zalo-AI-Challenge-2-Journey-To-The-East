from datamate.types import Catalog, FilterIR, MetricIR, PlanIR, SortIR, TimeResolution
from datamate.verification.semantics import declared_checks


def test_missing_time_bound_wrong_entity_and_or_do_not_pass(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="x",
        intent="aggregate",
        answer_type="number",
        required_tables=[table],
        filters=[
            FilterIR(field=f"{table}.order_date", op="range", value_ref="time_window"),
            FilterIR(field=f"{table}.status", op="eq", value="complete"),
        ],
    )
    time = TimeResolution(
        as_of_date="2026-01-10", start_iso="2026-01-01", end_exclusive_iso="2026-01-11"
    )
    good = f"SELECT SUM(o.amount) FROM {table} o WHERE o.order_date >= '2026-01-01' AND o.order_date < '2026-01-11' AND o.status = 'complete'"
    checks, warnings = declared_checks(good, plan, catalog, time)
    assert all(checks.values()) and not warnings
    for bad in [
        good.replace("2026-01-11", "2026-02-01"),
        good.replace("complete", "cancelled"),
        good.replace(" AND ", " OR ", 1),
    ]:
        checks, _ = declared_checks(bad, plan, catalog, time)
        assert not all(checks.values())


def test_predicate_in_unused_cte_cannot_prove_root_semantics(catalog: Catalog) -> None:
    table = catalog.tables[0].table_id
    plan = PlanIR(
        question_id="x",
        intent="count",
        answer_type="number",
        required_tables=[table],
        filters=[FilterIR(field=f"{table}.status", op="eq", value="complete")],
    )
    checks, warnings = declared_checks(
        f"WITH unused AS (SELECT * FROM {table} WHERE status='complete') SELECT COUNT(*) FROM {table}",
        plan,
        catalog,
        TimeResolution(as_of_date="2026-01-01"),
    )
    assert checks == {}
    assert warnings == ["semantic_proof_requires_direct_select"]


def test_undeclared_filter_cannot_silently_narrow_literal_count(catalog: Catalog) -> None:
    table = catalog.tables[0].table_id
    plan = PlanIR(
        question_id="records", intent="count", answer_type="number", required_tables=[table]
    )
    plain, _ = declared_checks(
        f"SELECT COUNT(*) FROM {table}", plan, catalog, TimeResolution(as_of_date="2026-01-01")
    )
    filtered, _ = declared_checks(
        f"SELECT COUNT(*) FROM {table} WHERE status='complete'",
        plan,
        catalog,
        TimeResolution(as_of_date="2026-01-01"),
    )
    assert plain["filter_coverage"] is True
    assert filtered["filter_coverage"] is False


def test_ranking_can_order_by_declared_aggregate_alias(catalog: Catalog) -> None:
    table = next(t.table_id for t in catalog.tables if t.source_file == "orders.csv")
    plan = PlanIR(
        question_id="rank",
        intent="rank",
        answer_type="date",
        required_tables=[table],
        metrics=[MetricIR(name="revenue", function="sum", field=f"{table}.amount")],
        group_by=[f"{table}.order_date"],
        sort=[
            SortIR(field=f"{table}.amount", descending=True),
            SortIR(field=f"{table}.order_date"),
        ],
        limit=1,
    )
    sql = f"SELECT order_date, SUM(amount) AS revenue FROM {table} GROUP BY order_date ORDER BY revenue DESC, order_date ASC LIMIT 1"
    checks, _ = declared_checks(sql, plan, catalog, TimeResolution(as_of_date="2026-01-10"))
    assert all(checks.values())

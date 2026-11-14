from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from datamate.config import Config, Limits
from datamate.data.ingest import ingest_business
from datamate.decision.calibration import evidence_bucket
from datamate.execution.sql_executor import execute
from datamate.llm.client import FakeModelClient
from datamate.pipeline import Pipeline
from datamate.semantics.sales_filters import listed_months
from datamate.semantics.sales_model import (
    SALES_ROUTE,
    infer_sales_model,
    period_outside_orders,
    sales_family,
    sales_sql,
    status_class,
)
from datamate.types import Catalog, QuestionRecord, SqlCandidate

LINES = """ma_don,ngay,trang_thai,ten_hang,thanh_tien
o1,2026-01-10,Đã hoàn thành,Bia,30000
o1,2026-01-10,Đã hoàn thành,Bia,30000
o1,2026-01-10,Đã hoàn thành,Mực,70000
o2,2026-01-10,Đã hủy,Bia,30000
o3,2026-01-11,Đã hoàn thành,Bia,30000
o3,2026-01-12,Trả hàng,Bia,-30000
o4,2026-01-11,Đang giao,Gỏi,50000
o5,2026-02-01,Đã hoàn thành,Gỏi,50000
"""


@pytest.fixture
def sales_catalog(tmp_path: Path) -> Catalog:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "SoChiTiet.csv").write_text(LINES, encoding="utf-8")
    return ingest_business(business.parent, "shop", tmp_path / "cache")


def _run(catalog: Catalog, family: str, start: str, end: str) -> object:
    model = infer_sales_model(catalog)
    assert model is not None
    sql = sales_sql(model, family, start, end)
    result = execute(
        SqlCandidate(candidate_id="sales", sql=sql, strategy="direct", answer_type="number"),
        catalog,
        Limits(),
    )
    return result.rows[0][0]


def test_revenue_excludes_cancelled_and_any_returned_order_and_counts_duplicates_once(
    sales_catalog: Catalog,
) -> None:
    assert _run(sales_catalog, "revenue", "2026-01-01", "2026-02-01") == Decimal(150000)


def test_order_count_uses_only_valid_orders(sales_catalog: Catalog) -> None:
    assert _run(sales_catalog, "order_count", "2026-01-01", "2026-02-01") == 2


def test_period_without_orders_has_zero_revenue(sales_catalog: Catalog) -> None:
    assert _run(sales_catalog, "revenue", "2026-01-20", "2026-01-21") == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Đã hoàn thành", "valid"),
        ("Đang giao hàng", "valid"),
        ("đã lấy", "valid"),
        ("Đã hủy đơn", "cancelled"),
        ("khách hủy", "cancelled"),
        ("Không giao được", "cancelled"),
        ("Đổi trả", "returned"),
        ("Đã hoàn hàng", "returned"),
        ("hàng trả về", "returned"),
        ("Đang vận chuyển", "valid"),
        ("Khuyến mãi tháng 3", "valid"),
        ("khách trả lại 1 cái", "returned"),
        ("khách hủy đơn", "cancelled"),
        ("kiểm tra lại tuần sau", "valid"),
        ("khách trả lại, kiểm tra lại máy", "returned"),
    ],
)
def test_status_values_map_to_rulebook_classes(value: str, expected: str) -> None:
    assert status_class(value) == expected


@pytest.mark.parametrize(
    ("question", "family"),
    [
        ("Doanh thu tháng 3/2026 là bao nhiêu?", "revenue"),
        ("dthu hom qua", "revenue"),
        ("Tháng 3/2026 có bao nhiêu đơn?", "order_count"),
        ("Doanh thu nhóm đồ uống tháng 3/2026?", None),
        ("Doanh thu tháng này so với tháng trước tăng mấy %?", "revenue_change"),
        ("Tháng 3 có bao nhiêu đơn bị hủy?", None),
        ("Khách nào mua nhiều nhất?", None),
        ("Doanh thu của Coca Cola 330ml trong tháng 5 năm 2026 là bao nhiêu?", None),
        ("tiền bán gas petrolimex 12kg tháng 2/2026 là bn", None),
        ("Cho tôi doanh thu của tháng 8/2025, tháng 9/2025 và tháng 10/2025, ghi lần lượt", None),
        ("Tháng 5/2026 có mấy ngày doanh thu đạt 11.000.000 đồng trở lên?", None),
        ("thang 12/2025 co may don co dthu tu 170.000 dong tro len", None),
        ("Tổng doanh thu 7 ngày gần nhất là bao nhiêu vậy?", "revenue"),
        ("tinh tu ngay 1/1 den het hom nay, doanh thu nam nay la bao nhieu do", "revenue"),
        ("Từ đầu năm đến hết hôm nay cửa hàng có bao nhiêu đơn hàng vậy?", "order_count"),
    ],
)
def test_only_plain_revenue_and_order_count_questions_are_routed(
    question: str, family: str | None
) -> None:
    assert sales_family(question) == family


def test_period_outside_the_order_dates_is_reported(sales_catalog: Catalog) -> None:
    model = infer_sales_model(sales_catalog)
    assert model is not None
    assert period_outside_orders(model, sales_catalog, "2025-01-01", "2025-02-01") is True
    assert period_outside_orders(model, sales_catalog, "2026-01-31", "2026-02-02") is False


def _pipeline(config: Config, catalog: Catalog, policy: str) -> Pipeline:
    config.sales_model = True
    config.decision_policy = policy
    pipeline = Pipeline(config)
    pipeline.catalogs["shop"] = catalog
    return pipeline


def _question(text: str) -> QuestionRecord:
    return QuestionRecord(id="q", business_id="shop", question=text, as_of_date="2026-02-15")


def test_pipeline_answers_plain_revenue_without_a_model(
    config: Config, sales_catalog: Catalog
) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    decision = pipeline.solve(_question("Doanh thu tháng 1/2026 là bao nhiêu?"))
    assert decision.status == "answer"
    assert decision.value == 150000
    assert pipeline.last_trace["route"] == SALES_ROUTE
    assert evidence_bucket(pipeline.last_trace) == "sales_model:compiled"


def test_pipeline_reports_a_period_outside_the_data_as_unanswerable(
    config: Config, sales_catalog: Catalog
) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    decision = pipeline.solve(_question("Doanh thu tháng 3/2025 là bao nhiêu?"))
    assert decision.status == "unanswerable"


def test_abstain_policy_never_publishes_a_sales_answer(
    config: Config, sales_catalog: Catalog
) -> None:
    pipeline = _pipeline(config, sales_catalog, "abstain")
    assert pipeline.solve(_question("Doanh thu tháng 1/2026 là bao nhiêu?")).status == "abstain"


def test_other_questions_keep_the_model_route(config: Config, sales_catalog: Catalog) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    decision = pipeline.solve(_question("Khách nào mua nhiều nhất tháng 1/2026?"))
    assert decision.reason_codes == ["model_backend_disabled"]


NOTE_LINES = """stt,ngay,ten_hang,thanh_tien,ghi_chu
1,2026-01-10,Bia,30000,
2,2026-01-10,Bia,30000,khách hủy
3,2026-01-11,Gỏi,50000,khách trả lại
4,2026-01-11,Gỏi,50000,giao chiều
"""


def test_note_column_carries_status_when_no_status_column(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "SoBanHang.csv").write_text(NOTE_LINES, encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    model = infer_sales_model(catalog)
    assert model is not None and model.status == "ghi_chu"
    assert _run(catalog, "revenue", "2026-01-01", "2026-02-01") == Decimal(80000)


def _package(tmp_path: Path, files: dict[str, str], readme: str) -> Catalog:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    for name, text in files.items():
        (business / name).write_text(text, encoding="utf-8")
    (business / "README.md").write_text(readme, encoding="utf-8")
    return ingest_business(business.parent, "shop", tmp_path / "cache")


SPLIT_README = """# Dữ liệu
## Cột trong `don_hang.csv`
- **Số CT**: mã đơn
- **Date**: ngày bán
- **Xử lý**: trạng thái đơn
## Cột trong `chi_tiet_don.csv`
- **Mã bill**: mã đơn
- **Cộng**: thành tiền sau giảm
- **Mã H.hóa**: mã hàng
"""


def test_readme_roles_join_differently_named_order_keys(tmp_path: Path) -> None:
    catalog = _package(
        tmp_path,
        {
            "don_hang.csv": "So CT,Date,Xử lý\nA,2026-01-05,Hoàn thành\nB,2026-01-06,Hủy\n",
            "chi_tiet_don.csv": "Mã bill,Cộng,Mã H.hóa\nA,100,h1\nA,50,h2\nB,70,h1\n",
        },
        SPLIT_README,
    )
    assert _run(catalog, "revenue", "2026-01-01", "2026-02-01") == Decimal(150)
    assert _run(catalog, "order_count", "2026-01-01", "2026-02-01") == 1


ORDER_README = """# Dữ liệu
## Cột trong `danh_sach_hoa_don.csv`
- **Ngày tháng**: ngày bán
- **Trạng thái đơn**: trạng thái đơn
- **TongTien**: số khách phải trả cho cả đơn (đã tính giảm giá của đơn và phí giao)
- **Mã hóa đơn**: mã đơn
- **Giá trị hàng**: tiền hàng của đơn: cộng thành tiền các mặt hàng, trước giảm giá của đơn
"""


def test_order_level_goods_value_gives_revenue_without_lines(tmp_path: Path) -> None:
    catalog = _package(
        tmp_path,
        {
            "danh_sach_hoa_don.csv": (
                "Ngày tháng,Trạng thái đơn,TongTien,Mã hóa đơn,Giá trị hàng\n"
                "2026-01-05,Hoàn thành,95,A,100\n2026-01-05,Hủy,70,B,70\n"
            )
        },
        ORDER_README,
    )
    assert _run(catalog, "revenue", "2026-01-01", "2026-02-01") == Decimal(100)


NEGATIVE_README = """# Dữ liệu
## Cột trong `so_ban.csv`
- **Mã đơn**: mã đơn
- **Ngày**: ngày bán
- **Tên hàng**: tên hàng
- **SL**: hàng trả ghi số lượng âm, cùng mã đơn với đơn đã bán: đơn có trả hàng
- **Thành tiền**: thành tiền sau giảm
"""


def test_negative_quantity_line_marks_its_order_returned(tmp_path: Path) -> None:
    catalog = _package(
        tmp_path,
        {
            "so_ban.csv": (
                "Mã đơn,Ngày,Tên hàng,SL,Thành tiền\n"
                "A,2026-01-05,Bia,2,60\nB,2026-01-05,Bia,1,30\nB,2026-01-05,Mực,1,40\n"
                "B,2026-01-07,Bia,-1,-30\n"
            )
        },
        NEGATIVE_README,
    )
    assert _run(catalog, "revenue", "2026-01-01", "2026-02-01") == Decimal(60)


def test_numeric_date_column_is_not_used_as_an_order_date(tmp_path: Path) -> None:
    catalog = _package(
        tmp_path,
        {"so_ban.csv": "Mã đơn,Ngày,Tên hàng,Thành tiền\nA,20260105,Bia,60\n"},
        NEGATIVE_README.replace(
            "- **SL**: hàng trả ghi số lượng âm, cùng mã đơn với đơn đã bán: đơn có trả hàng\n", ""
        ),
    )
    assert infer_sales_model(catalog) is None


def test_sales_route_failure_falls_back_instead_of_failing_the_question(
    config: Config, sales_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")

    def broken(*_: object) -> bool:
        raise duckdb.ConversionException("cast failed")

    monkeypatch.setattr("datamate.pipeline.period_outside_orders", broken)
    decision = pipeline.solve(_question("Doanh thu tháng 1/2026 là bao nhiêu?"))
    assert decision.reason_codes == ["model_backend_disabled"]
    assert pipeline.last_trace["sales_model_error"] == "ConversionException"


def test_engine_error_on_the_model_route_abstains_instead_of_failing_the_batch(
    config: Config, sales_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.decision_policy = "execution_baseline"
    config.models.backend = "ollama"
    pipeline = Pipeline(config, FakeModelClient([]), FakeModelClient([]))
    pipeline.catalogs["shop"] = sales_catalog

    def broken(*_: object) -> object:
        raise duckdb.ConversionException("cast failed")

    monkeypatch.setattr("datamate.pipeline.resolve_time", broken)
    decision = pipeline.solve(_question("Khách nào mua nhiều nhất tháng 1/2026?"))
    assert decision.status == "abstain"
    assert decision.reason_codes == ["ConversionException"]


def test_unmerged_monthly_partition_is_never_a_sales_source(sales_catalog: Catalog) -> None:
    monthly = sales_catalog.model_copy(
        update={
            "tables": [
                t.model_copy(update={"source_file": "BanHang_Thang01_2026.csv"})
                for t in sales_catalog.tables
            ]
        }
    )
    assert infer_sales_model(monthly) is None


def test_question_log_has_the_organizer_fields(config: Config, sales_catalog: Catalog) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    pipeline.solve(_question("Doanh thu tháng 1/2026 là bao nhiêu?"))
    trace = pipeline.last_trace
    assert trace["id"] == "q"
    assert trace["answer"] == {"id": "q", "type": "number", "answer": 150000}
    assert isinstance(trace["elapsed_ms"], int) and trace["elapsed_ms"] >= 0
    assert trace["thinking"] is None
    assert trace["code"].startswith("WITH lines AS")
    assert trace["code_source"] == "src/datamate/semantics/sales_model.py::sales_sql"
    assert trace["output"] == 150000


def test_abstained_question_log_has_null_code(config: Config, sales_catalog: Catalog) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    pipeline.solve(_question("Khách nào mua nhiều nhất tháng 1/2026?"))
    trace = pipeline.last_trace
    assert trace["answer"] == {"id": "q", "type": "abstain"}
    assert trace["code"] is None and trace["output"] is None and trace["thinking"] is None


@pytest.mark.parametrize(
    ("question", "family"),
    [
        ("So với tháng liền trước, doanh thu tháng 2/2026 thay đổi bao nhiêu %?", "revenue_change"),
        ("dthu tháng 3/2026 hơn hay kém th trước bn %", "revenue_change"),
        ("Doanh thu tháng 4/2026 so với tháng 4/2025 tăng hay giảm bao nhiêu %?", "revenue_change"),
        ("Tháng 4/2025 nhóm nướng chiếm bao nhiêu % doanh thu?", None),
        ("Doanh thu khách sỉ tháng 3 so với tháng trước tăng mấy %?", None),
        ("Doanh thu tháng 3 so với tháng trước chênh bao nhiêu tiền?", None),
    ],
)
def test_revenue_change_questions_are_routed(question: str, family: str | None) -> None:
    assert sales_family(question) == family


def test_pipeline_answers_revenue_change_percent(config: Config, sales_catalog: Catalog) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    question = "Doanh thu tháng 2/2026 so với tháng trước tăng hay giảm bao nhiêu %?"
    decision = pipeline.solve(_question(question))
    assert decision.status == "answer"
    assert decision.answer_type == "percent"
    assert decision.value == Decimal("-66.67")
    assert evidence_bucket(pipeline.last_trace) == "sales_model:compiled"


@pytest.mark.parametrize(
    ("question", "answer_type", "value"),
    [
        ("Doanh thu trung bình mỗi đơn tháng 1/2026 là bao nhiêu?", "number", Decimal(75000)),
        ("Tháng 1/2026 có bao nhiêu đơn bị hủy?", "number", 1),
        ("Tỉ lệ đơn hủy tháng 1/2026 là bao nhiêu phần trăm?", "percent", Decimal("25.00")),
        ("Tháng 1/2026 có mấy đơn có doanh thu từ 50.000 đồng trở lên?", "number", 2),
        ("Tháng 1/2026 có mấy đơn có doanh thu trên 50.000 đồng?", "number", 1),
        ("Tháng 1/2026 có mấy ngày doanh thu từ 60.000 đồng trở lên?", "number", 1),
        ("Tháng 1/2026 ngày nào cửa hàng bán được nhiều tiền nhất?", "date", "2026-01-10"),
        (
            "Tháng nào doanh thu cao nhất trong hai tháng 1/2026, 2/2026?",
            "date",
            "2026-01",
        ),
        (
            "Cho tôi doanh thu tháng 1/2026 và tháng 2/2026, ghi lần lượt",
            "list",
            [Decimal(150000), Decimal(50000)],
        ),
        ("Doanh thu tháng 1/2026 rồi tháng 2/2026 có tăng liên tục không?", "boolean", False),
        ("Tháng 1/2026 cửa hàng có bao nhiêu % đơn bị huỷ?", "percent", Decimal("25.00")),
        (
            "Từ tháng 1/2026 tới tháng 2/2026, tháng nào bán được nhiều tiền nhất?",
            "date",
            "2026-01",
        ),
    ],
)
def test_pipeline_answers_order_level_families(
    config: Config, sales_catalog: Catalog, question: str, answer_type: str, value: object
) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    decision = pipeline.solve(_question(question))
    assert decision.status == "answer"
    assert decision.answer_type == answer_type
    assert decision.value == value


def test_month_range_includes_the_months_between() -> None:
    text = "tu thang 4/2025 toi thang 6/2025, thang nao ban duoc nhieu tien nhat"
    assert listed_months(text) == (date(2025, 4, 1), date(2025, 5, 1), date(2025, 6, 1))


def test_month_list_keeps_question_order() -> None:
    text = "doanh thu thang 8/2025, thang 9/2025 va thang 10/2025 lan luot"
    assert listed_months(text) == (date(2025, 8, 1), date(2025, 9, 1), date(2025, 10, 1))


def test_chained_higher_than_question_checks_chronological_increase(
    config: Config, sales_catalog: Catalog
) -> None:
    pipeline = _pipeline(config, sales_catalog, "execution_baseline")
    question = "Dthu tháng 2/2026 cao hơn tháng 1/2026, đúng không?"
    decision = pipeline.solve(_question(question))
    assert decision.answer_type == "boolean"
    assert decision.value is False

from decimal import Decimal
from pathlib import Path

import pytest

from datamate.config import Config, Limits
from datamate.data.ingest import ingest_business
from datamate.execution.sql_executor import execute
from datamate.pipeline import Pipeline
from datamate.semantics.sales_filters import sales_intent
from datamate.semantics.sales_model import infer_sales_model, sales_sql
from datamate.types import Catalog, QuestionRecord, SqlCandidate

README = """# Dữ liệu
## Cột trong `don_hang.csv`
- **Số CT**: mã đơn
- **Date**: ngày bán
- **Giờ tạo**: thời điểm đặt đơn (ngày giờ)
- **Xử lý**: trạng thái đơn
- **Mã KH**: mã khách (trống nếu khách lẻ)
- **Kênh**: kênh bán
- **PTTT**: hình thức thanh toán của đơn: cash tiền mặt, transfer chuyển khoản, cod giao hàng thu tiền
## Cột trong `chi_tiet_don.csv`
- **Mã bill**: mã đơn
- **Cộng**: thành tiền sau giảm
- **Mã HH**: mã hàng
## Cột trong `san_pham.csv`
- **SKU**: mã hàng
- **Tên SP**: tên
- **Nhóm hàng**: nhóm hàng
## Cột trong `khach_hang.csv`
- **Mã khách**: mã khách
- **Nhóm**: nhóm khách
"""
ORDERS = """So CT,Date,Giờ tạo,Xử lý,Mã KH,Kênh,PTTT
A,2026-01-05,2026-01-05T08:10:00,Hoàn thành,K1,store,cash
B,2026-01-05,2026-01-05T15:30:00,Hoàn thành,,phone,cod
C,2026-01-06,2026-01-06T09:00:00,Hủy,K1,store,cash
D,2026-01-07,2026-01-07T20:00:00,Hoàn thành,K2,store,transfer
"""
LINES = """Mã bill,Cộng,Mã HH
A,100,P1
A,50,P2
B,70,P1
C,30,P1
D,200,P2
"""
PRODUCTS = "SKU,Tên SP,Nhóm hàng\nP1,Lẩu thái,Lẩu\nP2,Cơm chiên,Cơm mì cháo\n"
CUSTOMERS = "Mã khách,Nhóm\nK1,thân thiết\nK2,sỉ\n"


@pytest.fixture
def shop(tmp_path: Path) -> Catalog:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    for name, text in {
        "don_hang.csv": ORDERS,
        "chi_tiet_don.csv": LINES,
        "san_pham.csv": PRODUCTS,
        "khach_hang.csv": CUSTOMERS,
        "README.md": README,
    }.items():
        (business / name).write_text(text, encoding="utf-8")
    return ingest_business(business.parent, "shop", tmp_path / "cache")


def _value(catalog: Catalog, question: str) -> object:
    model = infer_sales_model(catalog)
    assert model is not None
    intent = sales_intent(question, catalog, model)
    assert intent is not None
    sql = sales_sql(model, "revenue", "2026-01-01", "2026-02-01", intent.sales_filter)
    candidate = SqlCandidate(
        candidate_id="f", sql=str(sql), strategy="direct", answer_type="number"
    )
    return execute(candidate, catalog, Limits()).rows[0][0]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Doanh thu nhóm hàng lẩu tháng 1/2026 là bao nhiêu?", 170),
        ("Nhóm cơm mì cháo tháng 1/2026 bán được bao nhiêu tiền?", 250),
        ("Doanh thu Lẩu thái tháng 1/2026", 170),
        ("dthu các đơn bán qua điện thoại tháng 1/2026", 70),
        ("dthu các đơn bán tại quầy tháng 1/2026 là bao nhiêu?", 350),
        ("Tháng 1/2026 riêng ca chiều bán được bao nhiêu tiền?", 270),
        ("doanh thu các đơn trả bằng tiền mặt tháng 1/2026", 150),
    ],
)
def test_filtered_revenue(shop: Catalog, question: str, expected: int) -> None:
    assert _value(shop, question) == Decimal(expected)


@pytest.mark.parametrize(
    "question",
    [
        "Doanh thu nhóm hàng bánh ngọt tháng 1/2026 là bao nhiêu?",
        "Doanh thu nhóm lẩu và nhóm cơm mì cháo tháng 1/2026",
        "Doanh thu các đơn của khách nhóm thân thiết tháng 1/2026",
    ],
)
def test_unresolved_or_unsupported_filters_are_not_routed(shop: Catalog, question: str) -> None:
    model = infer_sales_model(shop)
    assert model is not None
    assert sales_intent(question, shop, model) is None


def test_pipeline_answers_group_share_percent(config: Config, shop: Catalog) -> None:
    config.sales_model = True
    config.decision_policy = "execution_baseline"
    pipeline = Pipeline(config)
    pipeline.catalogs["shop"] = shop
    question = QuestionRecord(
        id="q",
        business_id="shop",
        question="Tháng 1/2026 nhóm lẩu chiếm bao nhiêu % doanh thu?",
        as_of_date="2026-02-15",
    )
    decision = pipeline.solve(question)
    assert decision.status == "answer"
    assert decision.answer_type == "percent"
    assert decision.value == Decimal("40.48")


NAME_README = """# Dữ liệu
## Cột trong `so_ban.csv`
- **Mã đơn**: mã đơn
- **Ngày**: ngày bán
- **Tên hàng**: tên hàng
- **Thành tiền**: thành tiền sau giảm
## Cột trong `danh_muc.csv`
- **Tên**: tên
- **Nhóm**: nhóm hàng
"""


def test_item_group_matches_spelling_variants_of_item_names(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "README.md").write_text(NAME_README, encoding="utf-8")
    (business / "so_ban.csv").write_text(
        "Mã đơn,Ngày,Tên hàng,Thành tiền\n"
        "A,2026-01-05,Lẩu thái,100\nB,2026-01-05,LẨU  THÁI,50\nC,2026-01-05,lau thai,20\n"
        "D,2026-01-05,Cơm chiên,70\n",
        encoding="utf-8",
    )
    (business / "danh_muc.csv").write_text(
        "Tên,Nhóm\nLẩu thái,Lẩu\nCơm chiên,Cơm\n", encoding="utf-8"
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    assert _value(catalog, "Doanh thu nhóm lẩu tháng 1/2026 là bao nhiêu?") == Decimal(170)


NAMED_CUSTOMERS_README = """# Dữ liệu
## Cột trong `so_ban.csv`
- **Mã đơn**: mã đơn
- **Ngày**: ngày bán
- **Khách**: tên khách (Khách lẻ nếu không lưu)
- **Tên hàng**: tên hàng
- **Thành tiền**: thành tiền sau giảm
- **Trạng thái**: trạng thái đơn
"""
NAMED_CUSTOMER_LINES = """Mã đơn,Ngày,Khách,Tên hàng,Thành tiền,Trạng thái
A,2026-01-05,Lâm Hoa Long - 0912 345 678,Bia,100,Hoàn thành
B,2026-01-06,lâm hoa long,Bia,80,Hoàn thành
C,2026-01-06,Tạ Hoàng Lan (0987654321),Bia,150,Hoàn thành
D,2026-01-07,Khách lẻ,Bia,500,Hoàn thành
G,2026-01-07,kl,Bia,700,Hoàn thành
E,2026-01-07,Đỗ Duy Khánh,Bia,60,Hoàn thành
F,2026-01-08,Đỗ Duy Khánh,Bia,999,Đã hủy
"""


@pytest.fixture
def named_shop(tmp_path: Path) -> Catalog:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "README.md").write_text(NAMED_CUSTOMERS_README, encoding="utf-8")
    (business / "so_ban.csv").write_text(NAMED_CUSTOMER_LINES, encoding="utf-8")
    return ingest_business(business.parent, "shop", tmp_path / "cache")


@pytest.mark.parametrize(
    ("question", "answer_type", "value"),
    [
        ("Tháng 1/2026 khách nào mua nhiều tiền nhất?", "string", "Lâm Hoa Long"),
        (
            "Top 2 khách hàng mua nhiều tiền nhất tháng 1/2026 là ai?",
            "list",
            ["Lâm Hoa Long", "Tạ Hoàng Lan"],
        ),
        ("Tháng 1/2026 có mấy khách mua từ 2 đơn trở lên?", "number", 1),
        (
            "Tháng 1/2026 khách mua từ 2 đơn trở lên chiếm bao nhiêu % số khách đã mua?",
            "percent",
            Decimal("33.33"),
        ),
    ],
)
def test_customer_families_identify_customers_by_name(
    config: Config, named_shop: Catalog, question: str, answer_type: str, value: object
) -> None:
    config.sales_model = True
    config.decision_policy = "execution_baseline"
    pipeline = Pipeline(config)
    pipeline.catalogs["shop"] = named_shop
    decision = pipeline.solve(
        QuestionRecord(id="q", business_id="shop", question=question, as_of_date="2026-02-15")
    )
    assert decision.status == "answer"
    assert decision.answer_type == answer_type
    assert decision.value == value


ITEM_README = """# Dữ liệu
## Cột trong `so_ban.csv`
- **Mã đơn**: mã đơn
- **Ngày**: ngày bán
- **Tên hàng**: tên hàng
- **SL**: số lượng
- **SL lẻ**: số lượng đã đổi ra đơn vị cơ bản
- **Thành tiền**: thành tiền sau giảm
- **Trạng thái**: trạng thái đơn
## Cột trong `danh_muc.csv`
- **Tên**: tên
- **Nhóm**: nhóm hàng
"""
ITEM_LINES = """Mã đơn,Ngày,Tên hàng,SL,SL lẻ,Thành tiền,Trạng thái
A,2026-01-05,Bia Tiger lon,1,24,400,Hoàn thành
A,2026-01-05,Phí phục vụ,1,100,50,Hoàn thành
B,2026-01-06,BIA TIGER LON,2,2,40,Hoàn thành
C,2026-01-06,Cơm chiên,10,10,500,Hoàn thành
D,2026-01-07,Cơm chiên,30,30,900,Đã hủy
E,2026-01-07,Gỏi ngó sen,3,3,90,Hoàn thành
"""


@pytest.fixture
def item_shop(tmp_path: Path) -> Catalog:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "README.md").write_text(ITEM_README, encoding="utf-8")
    (business / "so_ban.csv").write_text(ITEM_LINES, encoding="utf-8")
    (business / "danh_muc.csv").write_text(
        "Tên,Nhóm\nBia Tiger lon,Bia\nCơm chiên,Cơm\nGỏi ngó sen,Khai vị\n", encoding="utf-8"
    )
    return ingest_business(business.parent, "shop", tmp_path / "cache")


@pytest.mark.parametrize(
    ("question", "answer_type", "value"),
    [
        ("Tháng 1/2026 mặt hàng nào bán chạy nhất?", "string", "Bia Tiger lon"),
        (
            "Top 2 sp bán nhiều nhất tháng 1/2026 là những gì?",
            "list",
            ["Bia Tiger lon", "Cơm chiên"],
        ),
    ],
)
def test_best_selling_items_use_base_quantity_and_catalog_names(
    config: Config, item_shop: Catalog, question: str, answer_type: str, value: object
) -> None:
    config.sales_model = True
    config.decision_policy = "execution_baseline"
    pipeline = Pipeline(config)
    pipeline.catalogs["shop"] = item_shop
    decision = pipeline.solve(
        QuestionRecord(id="q", business_id="shop", question=question, as_of_date="2026-02-15")
    )
    assert decision.status == "answer"
    assert decision.answer_type == answer_type
    assert decision.value == value


CONVERSION_README = """# Dữ liệu
## Cột trong `so_ban.csv`
- **Mã đơn**: mã đơn
- **Ngày**: ngày bán
- **Tên hàng**: tên hàng
- **ĐVT**: đơn vị tính
- **SL**: số lượng
- **Thành tiền**: thành tiền sau giảm
- **Trạng thái**: trạng thái đơn
## Cột trong `quy_doi.csv`
- **Hàng**: tên hàng (file chỉ có mặt hàng có đơn vị lớn)
- **ĐV lớn**: đơn vị lớn (thùng, hộp, tấn...)
- **Hệ số QĐ**: hệ số: số đơn vị cơ bản trong 1 đơn vị lớn
"""


def test_best_seller_converts_large_units_to_base_units(config: Config, tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "README.md").write_text(CONVERSION_README, encoding="utf-8")
    (business / "so_ban.csv").write_text(
        "Mã đơn,Ngày,Tên hàng,ĐVT,SL,Thành tiền,Trạng thái\n"
        "A,2026-01-05,Bia Tiger lon,thùng,2,800,Hoàn thành\n"
        "B,2026-01-06,Nước suối,chai,30,150,Hoàn thành\n"
        "C,2026-01-06,bia tiger lon,lon,3,60,Hoàn thành\n",
        encoding="utf-8",
    )
    (business / "quy_doi.csv").write_text(
        "Hàng,ĐV lớn,Hệ số QĐ\nBia Tiger lon,Thùng,24\n", encoding="utf-8"
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    config.sales_model = True
    config.decision_policy = "execution_baseline"
    pipeline = Pipeline(config)
    pipeline.catalogs["shop"] = catalog
    decision = pipeline.solve(
        QuestionRecord(
            id="q",
            business_id="shop",
            question="Top 2 mặt hàng bán chạy nhất tháng 1/2026?",
            as_of_date="2026-02-15",
        )
    )
    assert decision.value == ["Bia Tiger lon", "Nước suối"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Doanh thu của Bia Tiger lon trong tháng 1 năm 2026 là bao nhiêu?", Decimal(440)),
        ("tiền bán cơm chiên tháng 1/2026 là bn", Decimal(500)),
    ],
)
def test_revenue_of_one_named_item(item_shop: Catalog, question: str, expected: Decimal) -> None:
    assert _value(item_shop, question) == expected


def test_pos_tables_without_column_notes_map_by_column_names(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "README.md").write_text(
        "# POS\nMã đơn nối don_hang với chi_tiet_don.\n", encoding="utf-8"
    )
    files = {
        "don_hang.csv": "MaHoaDon,NgayLap,TinhTrang\nO1,2026-01-05,completed\nO2,2026-01-05,cancelled\n"
        "O3,2026-01-06,completed\n",
        "chi_tiet_don.csv": "MaHoaDon,MaHangHoa,SLQuyDoi,TriGia\nO1,SP1,2,100\nO2,SP1,1,50\n"
        "O3,SP2,1,70\n",
        "tra_hang.csv": "MaPhieuTra,MaHoaDon\nTH1,O3\n",
        "san_pham.csv": "MaHangHoa,TenGoi,DanhMuc\nSP1,Panadol,giảm đau\nSP2,Hapacol,giảm đau\n",
    }
    for name, text in files.items():
        (business / name).write_text(text, encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    assert _value(catalog, "Doanh thu tháng 1/2026 là bao nhiêu?") == Decimal(100)

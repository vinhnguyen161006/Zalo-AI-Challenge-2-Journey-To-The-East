from pathlib import Path

import duckdb
from openpyxl import Workbook

from datamate.data.ingest import ingest_business
from datamate.data.partitions import analysis_tables


def test_monthly_union_keeps_raw_tables_and_proves_dates(tmp_path: Path) -> None:
    root = tmp_path / "data"
    business = root / "shop"
    business.mkdir(parents=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, values in [
        ("01.2026", [["a", "2026-01-10", 10]]),
        ("02.2026", [["b", "2026-02-01", 20]]),
    ]:
        sheet = workbook.create_sheet(name)
        sheet.append(["order_id", "date", "amount"])
        for row in values:
            sheet.append(row)
    workbook.save(business / "orders.xlsx")
    catalog = ingest_business(root, "shop", tmp_path / "cache")
    assert len(catalog.tables) == 3
    visible = analysis_tables(catalog.tables)
    assert len(visible) == 1
    assert len(visible[0].derived_from) == 2
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        assert con.execute(f'SELECT SUM(amount) FROM "{visible[0].table_id}"').fetchone()[0] == 30
        for parent in visible[0].derived_from:
            assert con.execute(f'SELECT COUNT(*) FROM "raw__{parent}"').fetchone()[0] == 1


def test_overlapping_dates_do_not_form_a_partition_union(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name in ["01.2026", "02.2026"]:
        sheet = workbook.create_sheet(name)
        sheet.append(["order_id", "date", "amount"])
        sheet.append(["a", "2026-01-10", 10])
    workbook.save(business / "orders.xlsx")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    assert len(catalog.tables) == 2
    assert not any(t.derived_from for t in catalog.tables)


def test_readme_date_convention_and_rulebook_sheet_year_keep_raw_values(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "README.md").write_text("Ngày ghi theo dd/mm/yyyy", encoding="utf-8")
    rulebook = tmp_path / "rulebook.md"
    rulebook.write_text("Ngày không ghi năm: lấy năm của sheet", encoding="utf-8")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "2026"
    sheet.append(["order_id", "ngay_ban", "amount"])
    sheet.append(["a", "02/03", 10])
    workbook.save(business / "orders.xlsx")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache", rulebook=rulebook)
    table = catalog.tables[0].table_id
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        assert con.execute(f'SELECT ngay_ban FROM "{table}"').fetchone()[0] == "2026-03-02"
        assert con.execute(f'SELECT ngay_ban FROM "raw__{table}"').fetchone()[0] == "02/03"


def _monthly_workbook(path: Path, sheets: dict[str, list[list[object]]]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        sheet.append(["order_id", "ngay", "amount"])
        for row in rows:
            sheet.append(row)
    workbook.save(path)


def _visible_union(catalog_tables: list) -> object:
    unions = [t for t in analysis_tables(catalog_tables) if t.derived_from]
    assert len(unions) == 1
    return unions[0]


def test_mixed_date_formats_in_one_column_are_normalized(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    _monthly_workbook(
        business / "orders.xlsx",
        {"T7-2025": [["a", "13/07/2025", 1], ["b", "1 thg 7 2025", 2], ["c", 45839, 3]]},
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    table = catalog.tables[0].table_id
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        dates = con.execute(f'SELECT ngay FROM "{table}" ORDER BY order_id').fetchall()
    assert dates == [("2025-07-13",), ("2025-07-01",), ("2025-07-01",)]


def test_union_resolves_number_style_from_sibling_sheets(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    _monthly_workbook(
        business / "orders.xlsx",
        {
            "05.2026": [["a", "2026-05-02", "1,056,000"], ["b", "2026-05-03", "56,334"]],
            "06.2026": [["c", "2026-06-01", "80,867"]],
        },
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    union = _visible_union(catalog.tables)
    assert len(union.derived_from) == 2
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        total = con.execute(f'SELECT SUM(amount) FROM "{union.table_id}"').fetchone()[0]
    assert total == 1056000 + 56334 + 80867


def test_union_spans_files_split_by_suffix(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    _monthly_workbook(business / "SoBanHang_1.xlsx", {"Thang 01 2026": [["a", "2026-01-05", 10]]})
    _monthly_workbook(
        business / "SoBanHang_2026.xlsx", {"Thang 02 2026": [["b", "2026-02-05", 20]]}
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    union = _visible_union(catalog.tables)
    assert len(union.derived_from) == 2
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        months = con.execute(
            f'SELECT sheet_month FROM "{union.table_id}" ORDER BY sheet_month'
        ).fetchall()
    assert months == [("2026-01",), ("2026-02",)]


def test_union_allows_blank_dates_on_continuation_rows(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    _monthly_workbook(
        business / "orders.xlsx",
        {
            "01.2026": [["a", "2026-01-10", 10], ["a", None, 5]],
            "02.2026": [["b", "2026-02-01", 20], ["b", None, 1]],
        },
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    union = _visible_union(catalog.tables)
    assert union.rows == 4


def test_union_resolves_day_first_for_a_partial_month(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    _monthly_workbook(
        business / "orders.xlsx",
        {
            "T08-2026": [["a", "13/08/2026", 10]],
            "T09-2026": [["b", "01/09/2026", 20], ["c", "02/09/2026", 30]],
        },
    )
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    union = _visible_union(catalog.tables)
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        dates = con.execute(f'SELECT ngay FROM "{union.table_id}" ORDER BY ngay').fetchall()
    assert dates == [("2026-08-13",), ("2026-09-01",), ("2026-09-02",)]


def test_group_cells_fill_down_and_footer_rows_are_excluded(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "orders"
    sheet.append(["ma_don", "ngay", "trang_thai", "hang", "thanh_tien"])
    sheet.append(["o1", "2026-01-10", "Hoàn thành", "A", "10.000đ"])
    sheet.append([None, None, None, "B", "5,000"])
    sheet.append(["o2", "2026-01-11", "Huỷ", "C", "7k"])
    sheet.append(["Tổng cộng", None, None, None, "22.000đ"])
    sheet.append(["Ghi chú: tiền tính bằng đồng", None, None, None, None])
    workbook.save(business / "orders.xlsx")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    table = catalog.tables[0]
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        rows = con.execute(
            f'SELECT ma_don, ngay, trang_thai, thanh_tien FROM "{table.table_id}" ORDER BY hang'
        ).fetchall()
        raw_count = con.execute(f'SELECT COUNT(*) FROM "raw__{table.table_id}"').fetchone()[0]
    assert rows == [
        ("o1", "2026-01-10", "Hoàn thành", 10000),
        ("o1", "2026-01-10", "Hoàn thành", 5000),
        ("o2", "2026-01-11", "Huỷ", 7000),
    ]
    assert raw_count == 5
    assert table.rows == 3


def test_list_split_across_numbered_files_is_unioned(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    for name, rows in [("ChiTiet_1.csv", "o1,10\no2,20\n"), ("ChiTiet_2.csv", "o3,30\n")]:
        (business / name).write_text("ma_don,thanh_tien\n" + rows, encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    union = _visible_union(catalog.tables)
    assert len(union.derived_from) == 2
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        total = con.execute(f'SELECT SUM(thanh_tien) FROM "{union.table_id}"').fetchone()[0]
    assert total == 60


def test_unrelated_files_with_same_columns_are_not_unioned(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    for name in ["Ban.csv", "Nhap.csv"]:
        (business / name).write_text("ma,tien\na,1\n", encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    assert not any(t.derived_from for t in catalog.tables)


def test_monthly_files_named_by_month_are_unioned(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    _monthly_workbook(business / "BanHang_T12_2025.xlsx", {"DonHang": [["a", "2025-12-05", 10]]})
    _monthly_workbook(business / "BanHang_T1_2026.xlsx", {"DonHang": [["b", "2026-01-05", 20]]})
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache")
    union = _visible_union(catalog.tables)
    assert len(union.derived_from) == 2


def test_money_named_column_reads_single_groups_as_thousands(tmp_path: Path) -> None:
    business = tmp_path / "data" / "shop"
    business.mkdir(parents=True)
    (business / "orders.csv").write_text(
        'ma_don,tong_tien_hang\no1,"90,000"\no2,"55,000"\n', encoding="utf-8"
    )
    rulebook = tmp_path / "rulebook.md"
    rulebook.write_text("Tiền là số nguyên VND", encoding="utf-8")
    catalog = ingest_business(business.parent, "shop", tmp_path / "cache", rulebook=rulebook)
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        total = con.execute(
            f'SELECT SUM(tong_tien_hang) FROM "{catalog.tables[0].table_id}"'
        ).fetchone()[0]
    assert total == 145000

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

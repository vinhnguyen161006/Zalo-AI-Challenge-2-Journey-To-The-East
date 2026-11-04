from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook

from datamate.config import Config
from datamate.data.grounder import ground
from datamate.data.ingest import ingest_business, sha256_file
from datamate.errors import DataQualityError, InvalidInput
from datamate.types import Catalog


def test_raw_lineage_and_joins(catalog: Catalog) -> None:
    orders = next(t for t in catalog.tables if t.source_file == "orders.csv")
    payments = next(t for t in catalog.tables if t.source_file == "payments.csv")
    assert orders.rows == 4
    assert orders.source_rows == [2, 3, 4, 5]
    assert "order_id" in orders.candidate_keys
    edge = next(
        e
        for e in catalog.joins
        if e.left_table == orders.table_id and e.right_table == payments.table_id
    )
    assert edge.cardinality == "one-to-many"
    assert not edge.verified
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        assert con.execute(
            f'SELECT amount, _source_row FROM "raw__{orders.table_id}" ORDER BY _source_row'
        ).fetchone() == ("120", 2)


def test_all_formats_readonly(config: Config, tmp_path: Path) -> None:
    business = config.data_root / "biz_demo"
    before = {p.name: sha256_file(p) for p in business.iterdir() if p.is_file()}
    manifest = ingest_business(config.data_root, "biz_demo", tmp_path)
    assert {Path(t.source_file).suffix for t in manifest.tables} == {".csv", ".xlsx", ".sqlite"}
    assert before == {p.name: sha256_file(p) for p in business.iterdir() if p.is_file()}
    products = [t for t in manifest.tables if t.source_file == "products.xlsx"]
    assert len(products) == 3
    assert any(t.header_row == 2 for t in products)


def test_cache_invalidation_and_bom(tmp_path: Path) -> None:
    root = tmp_path / "input"
    business = root / "biz"
    business.mkdir(parents=True)
    csv = business / "sales.csv"
    csv.write_text("id;amount\na;1.234,50\n", encoding="utf-8-sig")
    first = ingest_business(root, "biz", tmp_path / "cache")
    assert first.tables[0].columns[1].dtype == "DECIMAL(38,10)"
    assert ingest_business(root, "biz", tmp_path / "cache").fingerprint == first.fingerprint
    csv.write_text("id;amount\na;1,234\n", encoding="utf-8-sig")
    second = ingest_business(root, "biz", tmp_path / "cache")
    assert second.fingerprint != first.fingerprint
    assert "ambiguous_numeric" in second.tables[0].columns[1].warnings


def test_malformed_csv_is_not_silently_discarded(tmp_path: Path) -> None:
    business = tmp_path / "biz"
    business.mkdir()
    (business / "bad.csv").write_text('id,amount\na,"unterminated\n', encoding="utf-8")
    with pytest.raises(DataQualityError):
        ingest_business(tmp_path, "biz", tmp_path / "cache")


def test_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(InvalidInput):
        ingest_business(tmp_path, "../escape", tmp_path / "cache")


def test_merged_cells_and_horizontal_regions(tmp_path: Path) -> None:
    business = tmp_path / "biz"
    business.mkdir()
    wb = Workbook()
    sh = wb.active
    sh.append(["id", "group", None, "unit", "factor"])
    sh.append(["a", "A", None, "box", 6])
    sh.append(["b", None, None, "unit", 1])
    sh.merge_cells("B2:B3")
    wb.save(business / "tables.xlsx")
    result = ingest_business(tmp_path, "biz", tmp_path / "cache")
    assert len(result.tables) == 2
    first = next(t for t in result.tables if t.columns[0].display_name == "id")
    with duckdb.connect(result.database_path, read_only=True) as con:
        assert con.execute(
            f'SELECT "group" FROM "{first.table_id}" ORDER BY _source_row'
        ).fetchall() == [("A",), ("A",)]


def test_grounder_returns_sources_not_answers(catalog: Catalog) -> None:
    candidates = ground("ca phe sua", catalog)
    assert candidates
    assert candidates[0].value == "Cà phê sữa"
    assert candidates[0].table and candidates[0].column


def test_decimal_width_changes_do_not_break_batch(tmp_path: Path) -> None:
    business = tmp_path / "biz"
    business.mkdir()
    (business / "amounts.csv").write_text("id,amount\na,1\nb,100\nc,1000000.25\n", encoding="utf-8")
    result = ingest_business(tmp_path, "biz", tmp_path / "cache")
    with duckdb.connect(result.database_path, read_only=True) as con:
        assert (
            str(con.execute(f'SELECT SUM(amount) FROM "{result.tables[0].table_id}"').fetchone()[0])
            == "1000101.2500000000"
        )


def write_csv(business: Path, name: str, text: str) -> None:
    business.mkdir()
    (business / name).write_text(text, encoding="utf-8")


def test_one_proving_cell_resolves_thousands_separators_for_the_column(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "biz",
        "sales.csv",
        'item,price,qty\na,"560,000",0.5\nb,"4,000",1.25\nc,"1,056,000",2\n',
    )
    result = ingest_business(tmp_path, "biz", tmp_path / "cache")
    price = next(c for c in result.tables[0].columns if c.name == "price")
    assert price.dtype == "DECIMAL(38,10)"
    assert price.warnings == []
    with duckdb.connect(result.database_path, read_only=True) as con:
        total = con.execute(f'SELECT SUM(price) FROM "{result.tables[0].table_id}"').fetchone()[0]
    assert str(total) == "1620000.0000000000"


def test_unprovable_separator_column_keeps_its_warning(tmp_path: Path) -> None:
    write_csv(tmp_path / "biz", "sales.csv", 'item,price\na,"560,000"\nb,"4,000"\n')
    result = ingest_business(tmp_path, "biz", tmp_path / "cache")
    price = next(c for c in result.tables[0].columns if c.name == "price")
    assert price.dtype == "VARCHAR"
    assert price.warnings == ["ambiguous_numeric"]


def test_one_proving_cell_resolves_day_first_dates_for_the_column(tmp_path: Path) -> None:
    write_csv(tmp_path / "biz", "sales.csv", "item,order_date\na,03/04/2026\nb,25/02/2026\n")
    result = ingest_business(tmp_path, "biz", tmp_path / "cache")
    column = next(c for c in result.tables[0].columns if c.name == "order_date")
    assert column.warnings == []
    assert column.samples == ["2026-04-03", "2026-02-25"]


def test_native_excel_floats_load_as_decimals(tmp_path: Path) -> None:
    business = tmp_path / "biz"
    business.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["item", "rate"])
    sheet.append(["a", 0.125])
    sheet.append(["b", 1.234])
    workbook.save(business / "rates.xlsx")
    result = ingest_business(tmp_path, "biz", tmp_path / "cache")
    rate = next(c for c in result.tables[0].columns if c.name == "rate")
    assert rate.dtype == "DECIMAL(38,10)"
    assert rate.warnings == []

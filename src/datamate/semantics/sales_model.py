"""Rulebook section 1 sales facts compiled from a business catalog, without a model.

A valid order is neither cancelled nor returned (one returned line excludes the whole order),
belongs to the period of its placement date, and its revenue is the sum of its line amounts.
Two identical lines of one order are an export error and count once (section 8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath
from typing import Any, Literal

import duckdb

from datamate.data.ingest import quote
from datamate.data.normalization import normalized_text
from datamate.data.partitions import analysis_tables, month_label
from datamate.semantics.question import language_hints
from datamate.semantics.source_notes import column_notes
from datamate.semantics.time_engine import month_end
from datamate.types import Catalog, TableCard, TimeResolution

SALES_ROUTE = "SALES_MODEL"
SalesFamily = Literal[
    "revenue",
    "order_count",
    "revenue_change",
    "revenue_share",
    "average_order_value",
    "cancelled_count",
    "cancel_rate",
    "order_threshold_count",
    "day_threshold_count",
    "best_day",
    "best_month",
    "revenue_list",
    "revenue_increasing",
    "top_customers",
    "customer_order_count",
    "customer_order_share",
    "top_items",
]
FAMILY_MEASURE: dict[str, SalesMeasure] = {
    "revenue": "revenue",
    "order_count": "order_count",
    "cancelled_count": "cancelled_count",
    "order_threshold_count": "order_threshold",
    "day_threshold_count": "day_threshold",
    "best_day": "best_day",
    "top_customers": "customer_stats",
    "customer_order_count": "customer_stats",
    "customer_order_share": "customer_stats",
    "top_items": "item_stats",
}
MONTH_LIST_FAMILIES = frozenset({"best_month", "revenue_list", "revenue_increasing"})
TWO_PART_FAMILIES = frozenset(
    {"revenue_change", "revenue_share", "average_order_value", "cancel_rate"}
)
MIN_LISTED_MONTHS = 2
ROW_MEASURES = frozenset({"customer_stats", "item_stats"})
ASCII_LIMIT = 127
SalesMeasure = Literal[
    "revenue",
    "order_count",
    "placed_count",
    "cancelled_count",
    "order_threshold",
    "day_threshold",
    "best_day",
    "customer_stats",
    "item_stats",
]
StatusClass = Literal["valid", "cancelled", "returned"]
AnswerKind = Literal["number", "percent", "date", "list", "boolean", "string"]

AMOUNT_COLUMNS = (
    "thanh_tien",
    "tt",
    "tri_gia",
    "trigia",
    "thanhtien",
    "amount",
    "tien",
    "tong_tien",
)
ITEM_COLUMNS = (
    "ten_hang",
    "mahanghoa",
    "ten_san_pham",
    "hang",
    "noi_dung",
    "ten_mon",
    "hang_hoa",
    "ten_hang_hoa",
    "mat_hang",
    "hang_ban",
)
KEY_COLUMNS = (
    "ma_hoa_don",
    "mahoadon",
    "ma_don_hang",
    "ma_don",
    "so_hoa_don",
    "hd",
    "hoa_don",
    "don",
    "ma_hd",
    "so_phieu",
    "stt",
    "ma_giao_dich",
)
DATE_COLUMNS = (
    "ngay_dat_hang",
    "ngaylap",
    "ngay_ban",
    "ngay_giao_dich",
    "thoi_gian",
    "ngay",
    "ngay_thang",
    "thoi_gian_tao",
    "ngay_lap",
    "thoigianlap",
)
STATUS_COLUMNS = (
    "trang_thai",
    "tinhtrang",
    "trang_thai_don_hang",
    "status",
    "tinh_trang",
    "ket_qua",
    "trang_thai_xu_ly",
    "trangthai",
)
RETURNED_QUANTITY_COLUMNS = ("so_luong_tra", "sl_tra", "so_luong_tra_dvt_co_ban")
ROLE_NAMES: dict[str, tuple[str, ...]] = {
    "item_code": ("mahanghoa", "ma_hang"),
    "catalog_name": ("tengoi",),
    "item_group": ("danhmuc",),
    "base_quantity": ("slquydoi",),
    "customer_key": ("makhachhang",),
    "customer_group": ("loaikhachhang",),
    "channel": ("kenhbanhang",),
    "order_time": ("thoigianlap",),
    "return_listing": ("maphieutra",),
    "amount": AMOUNT_COLUMNS,
    "item": ITEM_COLUMNS,
    "key": KEY_COLUMNS,
    "date": DATE_COLUMNS,
    "status": STATUS_COLUMNS,
    "note": ("ghi_chu", "ghi_chu_don", "note", "notes"),
    "returned_quantity": RETURNED_QUANTITY_COLUMNS,
}
ROLE_PHRASES: dict[str, tuple[str, ...]] = {
    "key": ("ma don",),
    "date": ("ngay ban", "thoi diem dat don", "ngay sua"),
    "status": ("trang thai don",),
    "note": ("ghi chu",),
    "amount": ("thanh tien sau giam", "tong tien ca phieu"),
    "item": ("ten hang", "ma hang", "cac viec da lam"),
    "order_amount": ("tien hang cua don", "tong tien hang cua don", "tong tien hang cua ca don"),
    "returned_quantity": ("so luong khach tra",),
    "negative_returns": ("hang tra ghi so luong am",),
    "item_code": ("ma hang",),
    "item_group": ("nhom hang",),
    "customer_key": ("ma khach",),
    "customer_name": ("ten khach",),
    "base_quantity": ("so luong da doi ra don vi co ban",),
    "unit": ("don vi tinh",),
    "conversion_item": ("ten hang (file chi co",),
    "big_unit": ("don vi lon",),
    "unit_factor": ("he so",),
    "customer_group": ("nhom khach",),
    "channel": ("kenh ban",),
    "payment": ("hinh thuc thanh toan cua don",),
    "order_time": ("thoi diem dat don", "gio dat don"),
}
EXACT_ROLE_PHRASES: dict[str, str] = {"catalog_name": "ten", "quantity": "so luong"}
PROVENANCE_COLUMNS = ("_source_row", "sheet_month", "source_part")
RETURNED_STATUS = re.compile(
    r"\b(?:tra hang|hoan hang|hoan tra|doi tra|tra ve|tra lai|return\w*)\b"
)
CANCELLED_STATUS = re.compile(r"\b(?:huy|cancel\w*|khong giao duoc|giao that bai|that bai)\b")
MAX_STATUS_VALUES = 40
INSPECTION_PHRASE = "kiem tra"
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_NOTE_VALUES = 5000
PLAIN_VOCABULARY = frozenset(
    """
    doanh thu dt ban duoc bao nhieu nhiu nhieu bn bnhieu tien hang don so may tong cong
    thang th t nam ngay tuan quy hom nay qua truoc roi gan nhat tu den toi het dau ky
    gio chu nho tet dip mua mung thg ngoai le
    la cua co the nao vay z ha nhi nhe a ah oi do ne di chu
    cho toi minh em anh chi biet xem giup gium coi hoi tinh duoc
    tiem quan shop cua tiem nha ben trong khoang giai doan luc vao thi
    tat ca ca ra
    check total ta nghe hi rua hen nhen dzay xiu ak voi dum mik mk ah ui
    """.split()
)

REVENUE_TERMS = re.compile(r"doanh thu|dthu|ban duoc bao nhieu tien|tien ban|tien hang")
ORDER_COUNT_TERMS = re.compile(r"bao nhieu don|may don|so don|tong (so )?don")
DIMENSION_TERMS = (
    r"nhom|ca (?:sang|chieu)|kenh|khach|thuoc|cao nhat|thap nhat|trung binh|mon|mat hang"
    r"|hang hoa|san pham|chenh|tien mat|chuyen khoan|cod|thanh toan|nhan vien|quay|online"
    r"|shopee|ti trong|ty trong|chiem|moi|ngay nao|thang nao|tuan nao|huy|tra hang|giao|lai"
    r"|loi|no|nhap"
)
COMPARISON_WORDS = r"phan tram|so voi|hon|kem|tang|giam"
QUALIFIER_TERMS = re.compile(rf"%|\b(?:{DIMENSION_TERMS}|{COMPARISON_WORDS})\b")
DIMENSION_QUALIFIERS = re.compile(rf"\b(?:{DIMENSION_TERMS})\b")
PERCENT_TERMS = re.compile(r"%|\bphan tram\b")
CHANGE_VOCABULARY = PLAIN_VOCABULARY | frozenset(
    "so voi tang giam hay hon kem thay doi phan tram cung ky truong len xuong ngoai".split()
)
PERCENT_STEP = Decimal("0.01")


@dataclass(frozen=True)
class ReturnMarker:
    """Orders listed in this column's table are returned when the quantity has this sign."""

    table: str
    key: str
    column: str
    negative: bool
    listed: bool = False


@dataclass(frozen=True)
class SalesFilter:
    """One business dimension value as SQL predicates on line rows or on order rows."""

    kind: str
    value: str
    line_predicate: str | None
    order_predicate: str | None


@dataclass(frozen=True)
class SalesProgram:
    measure: SalesMeasure
    start: str
    end: str
    sales_filter: SalesFilter | None
    threshold: Decimal | None = None
    strict: bool = False


@dataclass(frozen=True)
class SalesIntent:
    family: SalesFamily
    sales_filter: SalesFilter | None
    threshold: Decimal | None = None
    strict: bool = False
    months: tuple[date, ...] = ()
    top: int | None = None
    customer_by_code: bool = False
    item_by_code: bool = False


@dataclass(frozen=True)
class SalesModel:
    orders_table: str
    order_key: str | None
    date: str
    status: str | None
    excluded_statuses: tuple[str, ...]
    lines_table: str
    key: str | None
    amount: str
    order_level: bool
    line_columns: tuple[str, ...]
    returns: tuple[ReturnMarker, ...]
    customer_column: str | None = None
    customer_is_code: bool = False
    customer_table: str | None = None
    customer_table_key: str | None = None
    customer_table_name: str | None = None
    item_column: str | None = None
    item_is_code: bool = False
    item_table: str | None = None
    item_table_key: str | None = None
    item_table_name: str | None = None
    quantity_column: str | None = None
    unit_column: str | None = None
    conversion_table: str | None = None
    conversion_item: str | None = None
    conversion_unit: str | None = None
    conversion_factor: str | None = None


def status_class(value: object) -> StatusClass:
    text = normalized_text(str(value), accents=True).replace(INSPECTION_PHRASE, "")
    if RETURNED_STATUS.search(text):
        return "returned"
    if CANCELLED_STATUS.search(text):
        return "cancelled"
    return "valid"


def _uses_vocabulary(text: str, vocabulary: frozenset[str]) -> bool:
    """Every word is a period, measure or filler word; names and thresholds route elsewhere."""
    return all(t.isdigit() or t in vocabulary for t in re.findall(r"[a-z]+|\d+", text))


def _is_revenue_change(text: str) -> bool:
    return bool(
        REVENUE_TERMS.search(text)
        and PERCENT_TERMS.search(text)
        and not DIMENSION_QUALIFIERS.search(text)
        and _uses_vocabulary(text, CHANGE_VOCABULARY)
    )


def sales_family(question: str) -> SalesFamily | None:
    """Route only unqualified revenue or order-count questions; qualifiers need other rules."""
    text = normalized_text(language_hints(question)["expanded_question"], accents=True)
    if _is_revenue_change(text):
        return "revenue_change"
    if QUALIFIER_TERMS.search(text.replace("don hang", "don")) or not _uses_vocabulary(
        text, PLAIN_VOCABULARY
    ):
        return None
    if REVENUE_TERMS.search(text):
        return "revenue"
    if ORDER_COUNT_TERMS.search(text):
        return "order_count"
    return None


def _described_roles(catalog: Catalog, table: TableCard) -> dict[str, str] | None:
    notes = column_notes(catalog.readme, table.source_file)
    if not notes:
        return None
    roles: dict[str, str] = {}
    for column in table.columns:
        text = normalized_text(notes.get(column.name, {}).get("description", ""), accents=True)
        for role, phrases in ROLE_PHRASES.items():
            if role not in roles and text.startswith(phrases):
                roles[role] = column.name
        for role, phrase in EXACT_ROLE_PHRASES.items():
            if role not in roles and text == phrase:
                roles[role] = column.name
    return roles


def _named_roles(table: TableCard) -> dict[str, str]:
    names = {c.name for c in table.columns}
    roles = {}
    for role, wanted in ROLE_NAMES.items():
        found = next((w for w in wanted if w in names), None)
        if found:
            roles[role] = found
    return roles


def _is_iso_date(table: TableCard, column: str) -> bool:
    card = next((c for c in table.columns if c.name == column), None)
    return (
        card is not None
        and card.dtype == "VARCHAR"
        and not card.warnings
        and bool(card.samples)
        and all(ISO_DATE.match(v) for v in card.samples)
    )


def table_roles(catalog: Catalog, table: TableCard) -> dict[str, str]:
    """README column definitions decide roles; column names are used only without a README."""
    described = _described_roles(catalog, table)
    roles = _named_roles(table) if described is None else described
    if "date" in roles and not _is_iso_date(table, roles["date"]):
        del roles["date"]
    return roles


def _is_decimal(table: TableCard, column: str | None) -> bool:
    card = next((c for c in table.columns if c.name == column), None)
    return card is not None and card.dtype.startswith("DECIMAL")


def _excluded_statuses(
    catalog: Catalog, table: str, column: str, limit: int
) -> tuple[str, ...] | None:
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        values = con.execute(
            f"SELECT DISTINCT {quote(column)} FROM {quote(table)} "
            f"WHERE {quote(column)} IS NOT NULL LIMIT {limit + 1}"
        ).fetchall()
    if len(values) > limit:
        return None
    return tuple(sorted(str(v) for (v,) in values if status_class(v) != "valid"))


RoledTable = tuple[TableCard, dict[str, str]]


def _line_source(tables: list[RoledTable]) -> tuple[TableCard, dict[str, str], str, bool] | None:
    by_size = sorted(tables, key=lambda pair: -pair[0].rows)
    for table, roles in by_size:
        if "item" in roles and _is_decimal(table, roles.get("amount")):
            return table, roles, roles["amount"], False
    for table, roles in by_size:
        if {"key", "date"} <= roles.keys() and _is_decimal(table, roles.get("order_amount")):
            return table, roles, roles["order_amount"], True
    return None


def _order_source(
    tables: list[RoledTable], lines: TableCard, roles: dict[str, str]
) -> RoledTable | None:
    if "date" in roles:
        return lines, roles
    if "key" not in roles:
        return None
    return next(((t, r) for t, r in tables if t is not lines and {"key", "date"} <= r.keys()), None)


def _return_markers(tables: list[RoledTable]) -> tuple[ReturnMarker, ...]:
    markers = []
    for table, roles in tables:
        if "key" not in roles:
            continue
        for role, negative in (("returned_quantity", False), ("negative_returns", True)):
            if role in roles:
                markers.append(ReturnMarker(table.table_id, roles["key"], roles[role], negative))
        if "return_listing" in roles:
            markers.append(
                ReturnMarker(table.table_id, roles["key"], roles["return_listing"], False, True)
            )
    return tuple(markers)


def is_lone_partition(table: TableCard) -> bool:
    """One month of a monthly export that was not merged cannot represent the business."""
    stem = PurePosixPath(table.source_file).stem
    return not table.derived_from and bool(month_label(table.sheet_or_table) or month_label(stem))


def _customer_fields(tables: list[RoledTable], order_roles: dict[str, str]) -> dict[str, Any]:
    """Rulebook section 3: a customer code identifies a customer; otherwise the written name."""
    if "customer_key" in order_roles:
        directory = next(
            (t for t, r in tables if {"customer_key", "catalog_name"} <= r.keys()), None
        )
        roles = table_roles_cache(tables, directory)
        return {
            "customer_column": order_roles["customer_key"],
            "customer_is_code": True,
            "customer_table": directory.table_id if directory else None,
            "customer_table_key": roles.get("customer_key"),
            "customer_table_name": roles.get("catalog_name"),
        }
    if "customer_name" in order_roles:
        return {"customer_column": order_roles["customer_name"], "customer_is_code": False}
    return {}


def _item_fields(
    tables: list[RoledTable], lines: TableCard, line_roles: dict[str, str]
) -> dict[str, Any]:
    """Items by code when lines carry one, else by name; the catalog gives the written name."""
    quantity = line_roles.get("base_quantity") or line_roles.get("quantity")
    if quantity is None or not _is_decimal(lines, quantity):
        return {}
    by_code = "item_code" in line_roles
    wanted = {"item_code", "catalog_name"} if by_code else {"catalog_name"}
    directory = next((t for t, r in tables if wanted <= r.keys() and "customer_key" not in r), None)
    roles = table_roles_cache(tables, directory)
    column = line_roles["item_code"] if by_code else line_roles.get("item")
    if column is None:
        return {}
    conversion = _conversion_fields(tables, line_roles)
    if conversion is None:
        return {}
    return {
        **conversion,
        "item_column": column,
        "item_is_code": by_code,
        "item_table": directory.table_id if directory else None,
        "item_table_key": roles.get("item_code"),
        "item_table_name": roles.get("catalog_name"),
        "quantity_column": quantity,
    }


def _conversion_fields(
    tables: list[RoledTable], line_roles: dict[str, str]
) -> dict[str, Any] | None:
    """Quantities in the line's unit need the large-unit factor table; None when unusable."""
    if "base_quantity" in line_roles:
        return {}
    wanted = {"conversion_item", "big_unit", "unit_factor"}
    table = next((t for t, r in tables if wanted <= r.keys()), None)
    if table is None:
        return {}
    if "unit" not in line_roles:
        return None
    roles = table_roles_cache(tables, table)
    return {
        "unit_column": line_roles["unit"],
        "conversion_table": table.table_id,
        "conversion_item": roles["conversion_item"],
        "conversion_unit": roles["big_unit"],
        "conversion_factor": roles["unit_factor"],
    }


def table_roles_cache(tables: list[RoledTable], table: TableCard | None) -> dict[str, str]:
    return next((r for t, r in tables if t is table), {})


def infer_sales_model(catalog: Catalog) -> SalesModel | None:
    tables = [
        (t, table_roles(catalog, t))
        for t in analysis_tables(catalog.tables)
        if not is_lone_partition(t)
    ]
    source = _line_source(tables)
    if source is None:
        return None
    lines, roles, amount, order_level = source
    orders = _order_source(tables, lines, roles)
    if orders is None:
        return None
    order_table, order_roles = orders
    status, limit = order_roles.get("status"), MAX_STATUS_VALUES
    if status is None:
        status, limit = order_roles.get("note"), MAX_NOTE_VALUES
    excluded = _excluded_statuses(catalog, order_table.table_id, status, limit) if status else ()
    if excluded is None:
        return None
    return SalesModel(
        orders_table=order_table.table_id,
        order_key=order_roles.get("key"),
        date=order_roles["date"],
        status=status,
        excluded_statuses=excluded,
        lines_table=lines.table_id,
        key=roles.get("key"),
        amount=amount,
        order_level=order_level,
        line_columns=tuple(c.name for c in lines.columns if c.name not in PROVENANCE_COLUMNS),
        returns=_return_markers(tables),
        **_customer_fields(tables, order_roles),
        **_item_fields(tables, lines, roles),
    )


def period_outside_orders(model: SalesModel, catalog: Catalog, start: str, end: str) -> bool:
    """Rulebook section 7: a period entirely outside the order dates is unanswerable."""
    date = f"CAST({quote(model.date)} AS DATE)"
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        first, last = con.execute(
            f"SELECT MIN({date}), MAX({date}) FROM {quote(model.orders_table)}"
        ).fetchone() or (None, None)
    if first is None or last is None:
        return True
    return bool(end <= first.isoformat() or start > last.isoformat())


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _day(value: str) -> str:
    return f"CAST({_literal(value)} AS DATE)"


def _keyless_sql(model: SalesModel, family: SalesMeasure, start: str, end: str) -> str | None:
    if family != "revenue":
        return None
    date = f"CAST({quote(model.date)} AS DATE)"
    status = (
        f" AND ({quote(model.status)} IS NULL OR {quote(model.status)} NOT IN "
        f"({', '.join(_literal(v) for v in model.excluded_statuses)}))"
        if model.status and model.excluded_statuses
        else ""
    )
    return (
        f"SELECT COALESCE(SUM({quote(model.amount)}), 0) AS value FROM {quote(model.lines_table)} "
        f"WHERE {date} >= {_day(start)} AND {date} < {_day(end)}{status}"
    )


def _excluded_sql(model: SalesModel) -> str:
    parts = []
    if model.status and model.excluded_statuses and model.order_key:
        values = ", ".join(_literal(v) for v in model.excluded_statuses)
        parts.append(
            f"SELECT {quote(model.order_key)} AS order_key FROM {quote(model.orders_table)} "
            f"WHERE {quote(model.status)} IN ({values})"
        )
    for marker in model.returns:
        comparison = "IS NOT NULL" if marker.listed else "< 0" if marker.negative else "> 0"
        parts.append(
            f"SELECT {quote(marker.key)} AS order_key FROM {quote(marker.table)} "
            f"WHERE {quote(marker.column)} {comparison}"
        )
    return " UNION ".join(parts) or "SELECT NULL AS order_key WHERE FALSE"


def _lines_sql(model: SalesModel, key: str) -> str:
    amount, table = quote(model.amount), quote(model.lines_table)
    if model.order_level:
        return f"SELECT {key}, MAX({amount}) AS {amount} FROM {table} GROUP BY {key}"
    return f"SELECT DISTINCT {', '.join(quote(c) for c in model.line_columns)} FROM {table}"


def _scope_ctes(model: SalesModel, start: str, end: str, sales_filter: SalesFilter | None) -> str:
    key, order_key, date = quote(model.key or ""), quote(model.order_key or ""), quote(model.date)
    order_filter = (
        f" AND order_key IN (SELECT {order_key} FROM {quote(model.orders_table)} "
        f"WHERE {sales_filter.order_predicate})"
        if sales_filter and sales_filter.order_predicate
        else ""
    )
    return (
        f"WITH lines AS ({_lines_sql(model, key)}), "
        f"placed AS (SELECT {order_key} AS order_key, MIN(CAST({date} AS DATE)) AS placed_on "
        f"FROM {quote(model.orders_table)} WHERE {order_key} IS NOT NULL GROUP BY {order_key}), "
        f"excluded AS ({_excluded_sql(model)}), "
        f"scoped AS (SELECT order_key, placed_on FROM placed WHERE placed_on >= {_day(start)} "
        f"AND placed_on < {_day(end)}{order_filter}), "
        f"valid AS (SELECT order_key FROM scoped WHERE order_key NOT IN "
        f"(SELECT order_key FROM excluded WHERE order_key IS NOT NULL)) "
    )


def _cancelled_keys(model: SalesModel) -> str | None:
    cancelled = [v for v in model.excluded_statuses if status_class(v) == "cancelled"]
    if not model.status or not cancelled or not model.order_key:
        return None
    values = ", ".join(_literal(v) for v in cancelled)
    return (
        f"SELECT {quote(model.order_key)} FROM {quote(model.orders_table)} "
        f"WHERE {quote(model.status)} IN ({values})"
    )


def _measure_sql(model: SalesModel, program: SalesProgram) -> str | None:
    key, amount = quote(model.key or ""), quote(model.amount)
    line_filter = (
        f" AND {program.sales_filter.line_predicate}"
        if program.sales_filter and program.sales_filter.line_predicate
        else ""
    )
    in_valid = f"{key} IN (SELECT order_key FROM valid){line_filter}"
    per_order = f"SELECT {key}, SUM({amount}) AS total FROM lines WHERE {in_valid} GROUP BY {key}"
    per_day = (
        f"SELECT p.placed_on AS day, SUM(l.{amount}) AS total FROM lines l JOIN placed p "
        f"ON l.{key} = p.order_key WHERE l.{in_valid} GROUP BY p.placed_on"
    )
    comparison = ">" if program.strict else ">="
    threshold = str(program.threshold) if program.threshold is not None else "NULL"
    if program.measure == "revenue":
        return f"SELECT COALESCE(SUM({amount}), 0) AS value FROM lines WHERE {in_valid}"
    if program.measure == "order_count":
        return "SELECT COUNT(*) AS value FROM valid"
    if program.measure == "placed_count":
        return "SELECT COUNT(*) AS value FROM scoped"
    if program.measure == "cancelled_count":
        cancelled = _cancelled_keys(model)
        if cancelled is None:
            return None
        return f"SELECT COUNT(*) AS value FROM scoped WHERE order_key IN ({cancelled})"
    if program.measure == "order_threshold":
        return f"SELECT COUNT(*) AS value FROM ({per_order}) t WHERE total {comparison} {threshold}"
    if program.measure == "day_threshold":
        return f"SELECT COUNT(*) AS value FROM ({per_day}) t WHERE total {comparison} {threshold}"
    if program.measure == "customer_stats":
        return _customer_stats_sql(model, in_valid)
    if program.measure == "item_stats":
        return _item_stats_sql(model, in_valid)
    return (
        f"SELECT CAST(day AS VARCHAR) AS value FROM ({per_day}) t "
        f"ORDER BY total DESC, day ASC LIMIT 1"
    )


def _item_stats_sql(model: SalesModel, in_valid: str) -> str | None:
    """Rows (kind, item, written name, unit, quantity): sales, catalog names, unit factors."""
    if model.item_column is None or model.quantity_column is None:
        return None
    item, quantity = quote(model.item_column), quote(model.quantity_column)
    unit = f"l.{quote(model.unit_column)}" if model.unit_column else "NULL"
    table, code, name = (
        quote(model.item_table or ""),
        quote(model.item_table_key or ""),
        quote(model.item_table_name or ""),
    )
    if model.item_is_code:
        if not (model.item_table and model.item_table_key and model.item_table_name):
            return None
        sold = (
            f"SELECT 'sale', l.{item}, n.name, {unit}, SUM(l.{quantity}) FROM lines l LEFT JOIN "
            f"(SELECT {code} AS code, MIN({name}) AS name FROM {table} GROUP BY 1) n "
            f"ON n.code = l.{item} WHERE l.{in_valid} GROUP BY 1, 2, 3, 4"
        )
    else:
        sold = (
            f"SELECT 'sale', l.{item}, l.{item}, {unit}, SUM(l.{quantity}) FROM lines l "
            f"WHERE l.{in_valid} GROUP BY 1, 2, 3, 4"
        )
        if model.item_table and model.item_table_name:
            sold += f" UNION ALL SELECT DISTINCT 'catalog', {name}, {name}, NULL, NULL FROM {table}"
    if model.conversion_table:
        conversion_item = quote(model.conversion_item or "")
        sold += (
            f" UNION ALL SELECT 'unit', {conversion_item}, {conversion_item}, "
            f"{quote(model.conversion_unit or '')}, {quote(model.conversion_factor or '')} "
            f"FROM {quote(model.conversion_table)}"
        )
    return sold


def _customer_stats_sql(model: SalesModel, in_valid: str) -> str | None:
    if model.customer_column is None or model.order_key is None:
        return None
    key, amount, customer = (
        quote(model.key or ""),
        quote(model.amount),
        quote(model.customer_column),
    )
    order_key = quote(model.order_key)
    named = (
        model.customer_is_code
        and model.customer_table
        and model.customer_table_key
        and model.customer_table_name
    )
    display = "n.name" if named else "c.customer"
    names = (
        f"LEFT JOIN (SELECT {quote(model.customer_table_key or '')} AS code, "
        f"MIN({quote(model.customer_table_name or '')}) AS name "
        f"FROM {quote(model.customer_table or '')} GROUP BY 1) n ON n.code = c.customer "
        if named
        else ""
    )
    return (
        f"SELECT c.customer, {display} AS display, COUNT(DISTINCT c.order_key) AS orders, "
        f"SUM(l.{amount}) AS total FROM lines l JOIN (SELECT {order_key} AS order_key, "
        f"MIN({customer}) AS customer FROM {quote(model.orders_table)} WHERE {customer} IS NOT NULL "
        f"GROUP BY {order_key}) c ON l.{key} = c.order_key {names}"
        f"WHERE l.{in_valid} GROUP BY c.customer, {display}"
    )


def sales_sql(
    model: SalesModel,
    family: SalesMeasure,
    start: str,
    end: str,
    sales_filter: SalesFilter | None = None,
    *,
    threshold: Decimal | None = None,
    strict: bool = False,
) -> str | None:
    """Read-only SQL for one half-open period [start, end) under rulebook section 1."""
    program = SalesProgram(family, start, end, sales_filter, threshold, strict)
    if model.key is None or model.order_key is None:
        return None if sales_filter else _keyless_sql(model, family, start, end)
    if sales_filter and sales_filter.line_predicate and family != "revenue":
        return None
    measure = _measure_sql(model, program)
    return None if measure is None else _scope_ctes(model, start, end, sales_filter) + measure


def program_sql(model: SalesModel, program: SalesProgram) -> str | None:
    return sales_sql(
        model,
        program.measure,
        program.start,
        program.end,
        program.sales_filter,
        threshold=program.threshold,
        strict=program.strict,
    )


def _months_programs(intent: SalesIntent) -> list[SalesProgram]:
    return [
        SalesProgram("revenue", m.isoformat(), month_end(m).isoformat(), intent.sales_filter)
        for m in intent.months
    ]


def sales_programs(intent: SalesIntent, window: TimeResolution) -> list[SalesProgram] | None:
    """The measurements a family needs: asked period first, then its reference."""
    family, sales_filter = intent.family, intent.sales_filter
    if family in MONTH_LIST_FAMILIES:
        return _months_programs(intent) if len(intent.months) >= MIN_LISTED_MONTHS else None
    if window.ambiguous or not window.start_iso or not window.end_exclusive_iso:
        return None
    start, end = window.start_iso, window.end_exclusive_iso
    reference = (window.comparison_start_iso, window.comparison_end_exclusive_iso)
    if family == "revenue_change":
        if not reference[0] or not reference[1]:
            return None
        return [
            SalesProgram("revenue", start, end, sales_filter),
            SalesProgram("revenue", reference[0], reference[1], sales_filter),
        ]
    if reference[0]:
        return None
    two_part: dict[str, tuple[tuple[SalesMeasure, SalesFilter | None], ...]] = {
        "revenue_share": (("revenue", sales_filter), ("revenue", None)),
        "average_order_value": (("revenue", sales_filter), ("order_count", sales_filter)),
        "cancel_rate": (("cancelled_count", sales_filter), ("placed_count", sales_filter)),
    }
    if family in two_part:
        if family == "revenue_share" and sales_filter is None:
            return None
        return [SalesProgram(m, start, end, f) for m, f in two_part[family]]
    measure = FAMILY_MEASURE[family]
    return [SalesProgram(measure, start, end, sales_filter, intent.threshold, intent.strict)]


def _percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT_STEP, rounding=ROUND_HALF_UP)


def _best_month(intent: SalesIntent, values: list[object]) -> str | None:
    totals = [Decimal(str(v)) for v in values]
    best = max(totals)
    if totals.count(best) != 1:
        return None
    return intent.months[totals.index(best)].strftime("%Y-%m")


def sales_answer(intent: SalesIntent, values: list[object]) -> tuple[AnswerKind, object] | None:
    """Typed answer from measured values; None when the rulebook gives no defined value."""
    family = intent.family
    if family in CUSTOMER_FAMILIES:
        return customer_answer(intent, values[0])
    if family == "top_items":
        return item_answer(intent, values[0])
    if family == "best_day":
        return ("date", values[0]) if values[0] is not None else None
    if family == "best_month":
        month = _best_month(intent, values)
        return ("date", month) if month else None
    numbers = [Decimal(str(v)) for v in values]
    if family == "revenue_list":
        return ("list", numbers)
    if family == "revenue_increasing":
        return ("boolean", all(a < b for a, b in zip(numbers, numbers[1:], strict=False)))
    if family not in TWO_PART_FAMILIES:
        return ("number", numbers[0])
    part, whole = numbers
    if whole == 0:
        return None
    if family == "average_order_value":
        return ("number", (part / whole).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if family == "revenue_change":
        return ("percent", _percent((part - whole) / whole * 100))
    return ("percent", _percent(part / whole * 100))


FAMILY_KIND: dict[str, AnswerKind] = {
    "revenue_change": "percent",
    "revenue_share": "percent",
    "cancel_rate": "percent",
    "best_day": "date",
    "best_month": "date",
    "revenue_list": "list",
    "revenue_increasing": "boolean",
}


def family_kind(family: SalesFamily) -> AnswerKind:
    """Calibration groups compiled answers by answer type, not by question shape."""
    return FAMILY_KIND.get(family, "number")


CUSTOMER_FAMILIES = frozenset({"top_customers", "customer_order_count", "customer_order_share"})
PHONE_SUFFIX = re.compile(r"[\s\-–(]*\(?\+?\d[\d\s.]{7,}\d\)?\s*$")
WALK_IN_NAMES = frozenset({"", "khach le", "le", "kl", "k.l", "khach vang lai", "vang lai"})


def clean_customer_name(raw: str) -> str:
    """The written name without an attached phone number or trailing separators."""
    return " ".join(PHONE_SUFFIX.sub("", raw).strip(" -–()").split())


@dataclass
class CustomerTotal:
    orders: int
    total: Decimal
    spellings: dict[str, int]


def _display(spellings: dict[str, int]) -> str:
    def rank(name: str) -> tuple[int, int, int]:
        capitals = sum(1 for c in name if c.isupper())
        marked = sum(1 for c in name if ord(c) > ASCII_LIMIT)
        return spellings[name], capitals, marked

    return max(spellings, key=rank)


def _customers(rows: list[list[Any]], by_code: bool) -> dict[str, CustomerTotal]:
    customers: dict[str, CustomerTotal] = {}
    for raw, display, orders, total in rows:
        name = clean_customer_name(str(display if display is not None else raw))
        identity = str(raw).strip() if by_code else normalized_text(name, accents=True)
        if not identity or normalized_text(name, accents=True) in WALK_IN_NAMES:
            continue
        entry = customers.setdefault(identity, CustomerTotal(0, Decimal(0), {}))
        entry.orders += int(orders)
        entry.total += Decimal(str(total))
        entry.spellings[name] = entry.spellings.get(name, 0) + int(orders)
    return customers


def customer_answer(intent: SalesIntent, rows: object) -> tuple[AnswerKind, object] | None:
    if not isinstance(rows, list):
        return None
    customers = _customers(rows, intent.customer_by_code)
    if intent.family == "top_customers":
        top = intent.top or 1
        ranked = sorted(customers.values(), key=lambda c: c.total, reverse=True)
        if len(ranked) < top or (len(ranked) > top and ranked[top - 1].total == ranked[top].total):
            return None
        names = [_display(c.spellings) for c in ranked[:top]]
        return ("string", names[0]) if intent.top is None else ("list", names)
    if intent.threshold is None:
        return None
    limit = intent.threshold
    qualified = sum(
        1 for c in customers.values() if (c.orders > limit if intent.strict else c.orders >= limit)
    )
    if intent.family == "customer_order_count":
        return ("number", Decimal(qualified))
    if not customers:
        return None
    return ("percent", _percent(Decimal(qualified) / Decimal(len(customers)) * 100))


NON_ITEMS = frozenset({"phi phuc vu"})


@dataclass
class ItemTotal:
    quantity: Decimal
    spellings: dict[str, int]
    catalog_name: str | None


def _factors(rows: list[Any]) -> dict[tuple[str, str], Decimal]:
    return {
        (normalized_text(str(item), accents=True), normalized_text(str(unit), accents=True)): (
            Decimal(str(factor))
        )
        for kind, item, _, unit, factor in rows
        if kind == "unit" and item is not None and unit is not None and factor is not None
    }


def _items(rows: list[Any], by_code: bool) -> dict[str, ItemTotal]:
    factors = _factors(rows)
    items: dict[str, ItemTotal] = {}
    for kind, raw, display, unit, quantity in rows:
        if raw is None or kind == "unit":
            continue
        written = str(display if display is not None else raw)
        identity = str(raw).strip() if by_code else normalized_text(str(raw), accents=True)
        if normalized_text(written, accents=True) in NON_ITEMS:
            continue
        entry = items.setdefault(identity, ItemTotal(Decimal(0), {}, None))
        if kind == "catalog":
            entry.catalog_name = str(raw)
            continue
        key = (
            normalized_text(written, accents=True),
            normalized_text(str(unit or ""), accents=True),
        )
        entry.quantity += Decimal(str(quantity)) * factors.get(key, Decimal(1))
        entry.spellings[written] = entry.spellings.get(written, 0) + 1
        if by_code and display is not None:
            entry.catalog_name = str(display)
    return {k: v for k, v in items.items() if v.spellings}


def item_answer(intent: SalesIntent, rows: object) -> tuple[AnswerKind, object] | None:
    """Rulebook section 3: best sellers by quantity in base units; service fees are not items."""
    if not isinstance(rows, list):
        return None
    ranked = sorted(
        _items(rows, intent.item_by_code).values(), key=lambda i: i.quantity, reverse=True
    )
    top = intent.top or 1
    if len(ranked) < top or (
        len(ranked) > top and ranked[top - 1].quantity == ranked[top].quantity
    ):
        return None
    names = [i.catalog_name or _display(i.spellings) for i in ranked[:top]]
    return ("string", names[0]) if intent.top is None else ("list", names)

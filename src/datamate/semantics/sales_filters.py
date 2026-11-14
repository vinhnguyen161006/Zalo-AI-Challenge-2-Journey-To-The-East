"""Resolve one business dimension named in a question to exact source values."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from datamate.data.ingest import quote
from datamate.data.normalization import normalized_text, parse_number
from datamate.data.partitions import analysis_tables
from datamate.semantics.question import language_hints
from datamate.semantics.sales_model import (
    CHANGE_VOCABULARY,
    MIN_LISTED_MONTHS,
    ORDER_COUNT_TERMS,
    PERCENT_TERMS,
    REVENUE_TERMS,
    SalesFamily,
    SalesFilter,
    SalesIntent,
    SalesModel,
    is_lone_partition,
    sales_family,
    table_roles,
)
from datamate.semantics.time_engine import month_end
from datamate.types import Catalog, TableCard

FILTER_VOCABULARY = CHANGE_VOCABULARY | frozenset(
    """
    nhom hang khach thuoc cac don mat ca sang chieu rieng qua tai tra bang kenh mua do
    ve dc mon san pham chiem ti ty trong phan tram tong ban
    """.split()
)
SHARE_TERMS = re.compile(r"\b(?:chiem|ti trong|ty trong|phan cua)\b|tong doanh thu")
CUSTOMER_GROUP_TERMS = re.compile(r"\b(?:nhom khach|khach (?:thuoc )?nhom)\b")
ITEM_GROUP_TERMS = re.compile(r"\bnhom\b")
CHANNEL_TERMS = re.compile(r"\b(?:ban qua|ban tai|qua kenh|kenh)\b")
SHIFT_TERMS = re.compile(r"\bca (sang|chieu)\b")
SHIFT_HOURS = {"sang": (6, 13), "chieu": (14, 21)}
TIME_SUFFIX_LENGTH = 8
PAYMENT_WORDS = {
    "tien mat": "cash",
    "tm": "cash",
    "chuyen khoan": "transfer",
    "ck": "transfer",
    "vi dien tu": "ewallet",
    "cod": "cod",
    "ghi no": "debt",
}
MAX_DIMENSION_VALUES = 500
MAX_MONTH = 12
MAX_TOP = 20
MAX_ITEM_VALUES = 20000
MIN_ITEM_NAME = 4
TIME_SAMPLE_SIZE = 50
CHANNEL_WORDS = {
    "tai quay": "store",
    "tai cua hang": "store",
    "tai tiem": "store",
    "dien thoai": "phone",
    "zalo oa": "zalo_oa",
    "zalo": "zalo_oa",
}


RoledTable = tuple[TableCard, dict[str, str]]
Resolution = tuple[SalesFilter, str]


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _distinct(catalog: Catalog, table: str, column: str) -> list[str]:
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        rows = con.execute(
            f"SELECT DISTINCT {quote(column)} FROM {quote(table)} WHERE {quote(column)} IS NOT NULL "
            f"LIMIT {MAX_DIMENSION_VALUES + 1}"
        ).fetchall()
    return [str(v) for (v,) in rows] if len(rows) <= MAX_DIMENSION_VALUES else []


def _samples(catalog: Catalog, table: str, column: str) -> list[str]:
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        rows = con.execute(
            f"SELECT DISTINCT {quote(column)} FROM {quote(table)} WHERE {quote(column)} IS NOT NULL "
            f"LIMIT {TIME_SAMPLE_SIZE}"
        ).fetchall()
    return [str(v) for (v,) in rows]


def _named_value(values: list[str], text: str) -> tuple[str, str] | None:
    """The single longest source value written as whole words in the question."""
    found = []
    for value in values:
        plain = normalized_text(value, accents=True)
        if plain and re.search(rf"\b{re.escape(plain)}\b", text):
            found.append((len(plain), plain, value))
    if not found:
        return None
    found.sort(reverse=True)
    if len(found) > 1 and found[0][0] == found[1][0] and found[0][2] != found[1][2]:
        return None
    _, plain, value = found[0]
    return value, plain


def _residual(text: str, plain: str) -> str:
    return re.sub(rf"\b{re.escape(plain)}\b", " ", text, count=1)


def _tables(catalog: Catalog) -> list[RoledTable]:
    return [
        (t, table_roles(catalog, t))
        for t in analysis_tables(catalog.tables)
        if not is_lone_partition(t)
    ]


def _roles_of(tables: list[RoledTable], table_id: str) -> dict[str, str]:
    return next((roles for table, roles in tables if table.table_id == table_id), {})


def _item_group(catalog: Catalog, model: SalesModel, text: str) -> Resolution | None:
    if not ITEM_GROUP_TERMS.search(text) or CUSTOMER_GROUP_TERMS.search(text):
        return None
    tables = _tables(catalog)
    lines = _roles_of(tables, model.lines_table)
    if "item_group" in lines:
        named = _named_value(_distinct(catalog, model.lines_table, lines["item_group"]), text)
        if named is None:
            return None
        predicate = f"{quote(lines['item_group'])} = {_literal(named[0])}"
        return SalesFilter("item_group", named[0], predicate, None), _residual(text, named[1])
    for table, roles in tables:
        if table.table_id == model.lines_table or "item_group" not in roles:
            continue
        link = _item_link(lines, roles)
        if link is None:
            continue
        named = _named_value(_distinct(catalog, table.table_id, roles["item_group"]), text)
        if named is None:
            return None
        line_column, catalog_column = link
        if line_column == lines.get("item_code") and catalog_column == roles.get("item_code"):
            predicate = (
                f"{quote(line_column)} IN (SELECT {quote(catalog_column)} "
                f"FROM {quote(table.table_id)} WHERE {quote(roles['item_group'])} = "
                f"{_literal(named[0])})"
            )
        else:
            members = _same_named_items(
                catalog, model.lines_table, line_column, table.table_id, roles, named[0]
            )
            if not members:
                return None
            predicate = f"{quote(line_column)} IN ({', '.join(_literal(m) for m in members)})"
        return SalesFilter("item_group", named[0], predicate, None), _residual(text, named[1])
    return None


def _same_named_items(
    catalog: Catalog,
    lines_table: str,
    line_column: str,
    catalog_table: str,
    roles: dict[str, str],
    group: str,
) -> list[str]:
    """Line item names that equal a catalog name of the group up to case, spacing and accents.

    Rulebook section 8: names that differ only in how they are written are the same thing.
    """
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        entries = con.execute(
            f"SELECT DISTINCT {quote(roles['catalog_name'])}, {quote(roles['item_group'])} "
            f"FROM {quote(catalog_table)}"
        ).fetchall()
        names = _distinct(catalog, lines_table, line_column)
    groups: dict[str, set[str]] = {}
    for name, item_group in entries:
        if name is not None and item_group is not None:
            groups.setdefault(normalized_text(str(name), accents=True), set()).add(str(item_group))
    return [name for name in names if groups.get(normalized_text(name, accents=True)) == {group}]


def _item_link(lines: dict[str, str], catalog_roles: dict[str, str]) -> tuple[str, str] | None:
    if "item_code" in lines and "item_code" in catalog_roles:
        return lines["item_code"], catalog_roles["item_code"]
    if "item" in lines and "catalog_name" in catalog_roles:
        return lines["item"], catalog_roles["catalog_name"]
    return None


def _customer_group(catalog: Catalog, model: SalesModel, text: str) -> Resolution | None:
    if not CUSTOMER_GROUP_TERMS.search(text):
        return None
    tables = _tables(catalog)
    orders = _roles_of(tables, model.orders_table)
    if "customer_key" not in orders:
        return None
    for table, roles in tables:
        if (
            table.table_id == model.orders_table
            or not {"customer_key", "customer_group"} <= roles.keys()
        ):
            continue
        named = _named_value(_distinct(catalog, table.table_id, roles["customer_group"]), text)
        if named is None:
            return None
        predicate = (
            f"{quote(orders['customer_key'])} IN (SELECT {quote(roles['customer_key'])} "
            f"FROM {quote(table.table_id)} WHERE {quote(roles['customer_group'])} = "
            f"{_literal(named[0])})"
        )
        return SalesFilter("customer_group", named[0], None, predicate), _residual(text, named[1])
    return None


def _coded(
    catalog: Catalog, model: SalesModel, text: str, role: str, words: dict[str, str]
) -> Resolution | None:
    """A dimension stored as codes (cash, store, ...) named by Vietnamese words in the question."""
    orders = _roles_of(_tables(catalog), model.orders_table)
    if role not in orders:
        return None
    mentioned = [(w, code) for w, code in words.items() if re.search(rf"\b{w}\b", text)]
    if len({code for _, code in mentioned}) != 1:
        return None
    code = mentioned[0][1]
    stored = [
        v
        for v in _distinct(catalog, model.orders_table, orders[role])
        if v.strip().casefold() == code
    ]
    if len(stored) != 1:
        return None
    predicate = f"{quote(orders[role])} = {_literal(stored[0])}"
    residual = text
    for word, _ in mentioned:
        residual = _residual(residual, word)
    return SalesFilter(role, code, None, predicate), residual


def _channel(catalog: Catalog, model: SalesModel, text: str) -> Resolution | None:
    if not CHANNEL_TERMS.search(text):
        return None
    coded = _coded(catalog, model, text, "channel", CHANNEL_WORDS)
    if coded is not None:
        return coded
    orders = _roles_of(_tables(catalog), model.orders_table)
    if "channel" not in orders:
        return None
    named = _named_value(_distinct(catalog, model.orders_table, orders["channel"]), text)
    if named is None:
        return None
    predicate = f"{quote(orders['channel'])} = {_literal(named[0])}"
    return SalesFilter("channel", named[0], None, predicate), _residual(text, named[1])


def _payment(catalog: Catalog, model: SalesModel, text: str) -> Resolution | None:
    return _coded(catalog, model, text, "payment", PAYMENT_WORDS)


def _shift(catalog: Catalog, model: SalesModel, text: str) -> Resolution | None:
    match = SHIFT_TERMS.search(text)
    orders = _roles_of(_tables(catalog), model.orders_table)
    if match is None or "order_time" not in orders:
        return None
    column = quote(orders["order_time"])
    samples = _samples(catalog, model.orders_table, orders["order_time"])
    if not samples or not all(re.search(r"\d{2}:\d{2}:\d{2}$", v) for v in samples):
        return None
    first, last = SHIFT_HOURS[match[1]]
    hour = f"CAST(SUBSTRING({column}, LENGTH({column}) - {TIME_SUFFIX_LENGTH - 1}, 2) AS INTEGER)"
    predicate = f"{hour} BETWEEN {first} AND {last}"
    return SalesFilter("shift", match[1], None, predicate), text.replace(match[0], " ", 1)


def _all_distinct(catalog: Catalog, table: str, columns: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        rows = con.execute(
            f"SELECT DISTINCT {columns} FROM {quote(table)} LIMIT {MAX_ITEM_VALUES + 1}"
        ).fetchall()
    return rows if len(rows) <= MAX_ITEM_VALUES else []


def _longest_name(names: dict[str, list[str]], text: str) -> str | None:
    """The single longest normalized item name written as whole words in the question."""
    found = sorted(
        (len(n), n)
        for n in names
        if len(n) >= MIN_ITEM_NAME and re.search(rf"\b{re.escape(n)}\b", text)
    )
    if not found or (len(found) > 1 and found[-1][0] == found[-2][0]):
        return None
    return found[-1][1]


def _item_name(catalog: Catalog, model: SalesModel, text: str) -> Resolution | None:
    """One item named in the question; spelling variants of its name are the same item."""
    tables = _tables(catalog)
    lines = _roles_of(tables, model.lines_table)
    if "item_code" in lines:
        directory = next((t for t, r in tables if {"item_code", "catalog_name"} <= r.keys()), None)
        if directory is None:
            return None
        roles = _roles_of(tables, directory.table_id)
        pairs = _all_distinct(
            catalog,
            directory.table_id,
            f"{quote(roles['catalog_name'])}, {quote(roles['item_code'])}",
        )
        column = lines["item_code"]
    elif "item" in lines:
        pairs = [(v, v) for (v,) in _all_distinct(catalog, model.lines_table, quote(lines["item"]))]
        column = lines["item"]
    else:
        return None
    names: dict[str, list[str]] = {}
    for name, value in pairs:
        if name is not None and value is not None:
            names.setdefault(normalized_text(str(name), accents=True), []).append(str(value))
    chosen = _longest_name(names, text)
    if chosen is None:
        return None
    predicate = f"{quote(column)} IN ({', '.join(_literal(v) for v in names[chosen])})"
    return SalesFilter("item", chosen, predicate, None), _residual(text, chosen)


RESOLVERS: tuple[Callable[[Catalog, SalesModel, str], Resolution | None], ...] = (
    _shift,
    _payment,
    _channel,
    _item_group,
    _item_name,
)


def _uses(text: str, vocabulary: frozenset[str]) -> bool:
    return all(t.isdigit() or t in vocabulary for t in re.findall(r"[a-z]+|\d+", text))


def _filtered_family(residual: str, sales_filter: SalesFilter) -> SalesFamily | None:
    if not _uses(residual, FILTER_VOCABULARY):
        return None
    if REVENUE_TERMS.search(residual) and PERCENT_TERMS.search(residual):
        return "revenue_share" if SHARE_TERMS.search(residual) else None
    if PERCENT_TERMS.search(residual) or SHARE_TERMS.search(residual):
        return None
    if REVENUE_TERMS.search(residual) or re.search(r"\bban duoc\b.*\btien\b", residual):
        return "revenue"
    if ORDER_COUNT_TERMS.search(residual) and sales_filter.line_predicate is None:
        return "order_count"
    return None


TEMPLATE_VOCABULARY = FILTER_VOCABULARY | frozenset(
    """
    trung binh binh quan moi mot gia tri mang ve bill hoa don huy bi da ti le ty dem so ngay
    nao nhat nhieu cao dat lan luot tung ghi theo thu tu lien tuc khong dung phai lai tro len
    dong vnd d it toi thieu vuot va cac trong ba bon hai sau nam kia nhung tren duoi bang lon
    dat duoc rui roi xet
    """.split()
)
TEMPLATE_BLOCKERS = re.compile(
    r"(?:nhom|ca (?:sang|chieu)|kenh|khach|thuoc|mon|mat hang|hang hoa|san pham|chenh"
    r"|tien mat|chuyen khoan|cod|thanh toan|nhan vien|quay|online|shopee|ti trong|ty trong"
    r"|chiem|giao|lai|loi|no|nhap|tra hang)"
)
COUNT_WORDS = re.compile(r"\b(?:bao nhieu|may|so|dem)\b")
ORDER_WORDS = re.compile(r"\b(?:don|bill|hoa don)\b")
AMOUNT_TOKEN = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(k|tr|trieu)?"
INCLUSIVE_THRESHOLD = re.compile(
    rf"(?:\btu\b|>=|≥|\bit nhat\b|\btoi thieu\b|\bdat\b)\s*{AMOUNT_TOKEN}"
)
STRICT_THRESHOLD = re.compile(rf"(?:\btren\b|\bhon\b|\bvuot\b|\bqua\b|>)\s*{AMOUNT_TOKEN}")
MONTH_RANGE = re.compile(r"\btu\b.*\b(?:toi|den)\b")
RANGE_ENDPOINTS = 2
LISTED_MONTH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{4})\b")
BEST_TERMS = re.compile(r"\b(?:nhieu tien nhat|cao nhat|dat nhat|nhieu nhat)\b")


def _threshold(text: str) -> tuple[Decimal, bool] | None:
    found = [(m, False) for m in INCLUSIVE_THRESHOLD.finditer(text)]
    found += [(m, True) for m in STRICT_THRESHOLD.finditer(text)]
    found = [(m, strict) for m, strict in found if not LISTED_MONTH.fullmatch(m[1])]
    if len(found) != 1:
        return None
    match, strict = found[0]
    amount = parse_number(match[1] + (match[2] or ""), style="vnd").value
    return (amount, strict) if amount is not None and amount > 0 else None


def listed_months(text: str) -> tuple[date, ...]:
    """Months in question order; "tu A toi/den B" means every month from A to B."""
    months: list[date] = []
    for match in LISTED_MONTH.finditer(text):
        month = int(match[1])
        if 1 <= month <= MAX_MONTH:
            value = date(int(match[2]), month, 1)
            if value not in months:
                months.append(value)
    if len(months) == RANGE_ENDPOINTS and MONTH_RANGE.search(text) and months[0] < months[1]:
        span = [months[0]]
        while span[-1] < months[1]:
            span.append(month_end(span[-1]))
        return tuple(span)
    return tuple(months)


CUSTOMER_VOCABULARY = TEMPLATE_VOCABULARY | frozenset(
    "khach hang nao la ai top tien chi tieu tong mua so da cua nhat liet ke nguoi chiem".split()
)
CUSTOMER_BLOCKERS = re.compile(
    r"\b(?:nhom|ca (?:sang|chieu)|kenh|mon|mat hang|san pham|tien mat|chuyen khoan|cod"
    r"|thanh toan|nhan vien|giao|lai|loi|no|nhap|tra hang|huy)\b"
)
TOP_CUSTOMER_TERMS = re.compile(
    r"\b(?:nhieu tien nhat|chi tieu nhieu nhat|chi nhieu nhat|tong tien mua cao nhat)\b"
)
TOP_COUNT = re.compile(r"\btop (\d+)\b|(?<![\d/])\b(\d+) khach\b")
CUSTOMER_ORDER_THRESHOLD = re.compile(
    r"\bkhach\b.*\bmua\b.*\b(tu|tren|it nhat|hon)\s+(\d+)\s+(?:don|lan)\b"
)


def _customer_family(text: str, by_code: bool) -> SalesIntent | None:
    """Customers per rulebook section 3: top spenders and customers with at least N orders."""
    if not re.search(r"\bkhach\b", text) or CUSTOMER_BLOCKERS.search(text):
        return None
    if not _uses(text, CUSTOMER_VOCABULARY):
        return None
    threshold = CUSTOMER_ORDER_THRESHOLD.search(text)
    if threshold:
        strict = threshold[1] in ("tren", "hon")
        count = Decimal(threshold[2])
        if PERCENT_TERMS.search(text):
            if not re.search(r"\bso khach\b", text):
                return None
            return SalesIntent(
                "customer_order_share", None, count, strict, customer_by_code=by_code
            )
        if not COUNT_WORDS.search(text):
            return None
        return SalesIntent("customer_order_count", None, count, strict, customer_by_code=by_code)
    if not TOP_CUSTOMER_TERMS.search(text):
        return None
    top = TOP_COUNT.search(text)
    size = int(top[1] or top[2]) if top else None
    if size is not None and not 1 <= size <= MAX_TOP:
        return None
    return SalesIntent("top_customers", None, top=size, customer_by_code=by_code)


ITEM_VOCABULARY = TEMPLATE_VOCABULARY | frozenset(
    "mat hang sp san pham mon nao la gi top chay hot nhat nhung ban nhieu duoc".split()
)
ITEM_BLOCKERS = re.compile(
    r"\b(?:nhom|ca (?:sang|chieu)|kenh|khach|tien|doanh thu|thanh toan|nhan vien|giao|lai"
    r"|loi|no|nhap|tra hang|huy|ton)\b"
)
BEST_SELLER_TERMS = re.compile(r"\b(?:ban chay|ban nhieu|hot|ban duoc nhieu) nhat\b")
TOP_ITEM_COUNT = re.compile(r"\btop (\d+)\b|(?<![\d/])\b(\d+) (?:mat hang|sp|san pham|mon|hang)\b")


def _item_family(text: str, by_code: bool) -> SalesIntent | None:
    if not BEST_SELLER_TERMS.search(text) or ITEM_BLOCKERS.search(text):
        return None
    if not _uses(text, ITEM_VOCABULARY):
        return None
    top = TOP_ITEM_COUNT.search(text)
    size = int(top[1] or top[2]) if top else None
    if size is not None and not 1 <= size <= MAX_TOP:
        return None
    return SalesIntent("top_items", None, top=size, item_by_code=by_code)


def _template_family(text: str) -> SalesIntent | None:
    """Fixed question shapes over valid orders; any unexplained word routes elsewhere."""
    if TEMPLATE_BLOCKERS.search(text) or not _uses(text, TEMPLATE_VOCABULARY):
        return None
    months = listed_months(text) if "thang" in text else ()
    revenue = bool(REVENUE_TERMS.search(text) or re.search(r"\bban duoc\b.*\btien\b", text))
    if re.search(r"\b(?:ti le|ty le)\b.*\bhuy\b", text) or (
        PERCENT_TERMS.search(text) and re.search(r"\bhuy\b", text)
    ):
        return SalesIntent("cancel_rate", None)
    if re.search(r"\bhuy\b", text) and ORDER_WORDS.search(text) and COUNT_WORDS.search(text):
        return SalesIntent("cancelled_count", None)
    if re.search(r"\b(?:trung binh|binh quan)\b", text) and ORDER_WORDS.search(text) and revenue:
        return SalesIntent("average_order_value", None)
    if re.search(r"\bngay nao\b", text) and BEST_TERMS.search(text) and revenue:
        return SalesIntent("best_day", None)
    if len(months) >= MIN_LISTED_MONTHS and revenue:
        if re.search(r"\bthang nao\b", text) and BEST_TERMS.search(text):
            return SalesIntent("best_month", None, months=months)
        if re.search(r"\blien tuc\b", text):
            return SalesIntent("revenue_increasing", None, months=months)
        if re.search(r"\b(?:lan luot|tung thang|moi thang|thu tu)\b", text):
            return SalesIntent("revenue_list", None, months=months)
        return None
    threshold = _threshold(text)
    if threshold is None or not COUNT_WORDS.search(text) or not revenue:
        return None
    if re.search(r"\b(?:may|bao nhieu|so|dem so) ngay\b", text):
        return SalesIntent("day_threshold_count", None, threshold[0], threshold[1])
    if ORDER_WORDS.search(text):
        return SalesIntent("order_threshold_count", None, threshold[0], threshold[1])
    return None


def sales_intent(question: str, catalog: Catalog, model: SalesModel) -> SalesIntent | None:
    """A plain sales question, or one filtered by exactly one resolved dimension value."""
    plain = sales_family(question)
    if plain is not None:
        return SalesIntent(plain, None)
    text = normalized_text(language_hints(question)["expanded_question"], accents=True)
    if model.customer_column is not None:
        customer = _customer_family(text, model.customer_is_code)
        if customer is not None:
            return customer
    if model.item_column is not None:
        item = _item_family(text, model.item_is_code)
        if item is not None:
            return item
    templated = _template_family(text)
    if templated is not None:
        return templated
    for resolver in RESOLVERS:
        resolved = resolver(catalog, model, text)
        if resolved is None:
            continue
        sales_filter, residual = resolved
        family = _filtered_family(residual, sales_filter)
        return SalesIntent(family, sales_filter) if family else None
    return None

"""Read source packages without mutation and write fingerprinted DuckDB snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import duckdb
import pandas as pd
from charset_normalizer import from_bytes
from openpyxl import load_workbook

from datamate.config import Limits
from datamate.data.excel_tables import table_regions
from datamate.data.normalization import (
    date_day_first,
    identifier,
    normalized_text,
    number_style,
    parse_date,
    parse_number,
)
from datamate.data.partitions import analysis_tables, file_family, month_label
from datamate.errors import DataQualityError, InvalidInput, ResourceExceeded
from datamate.types import Catalog, ColumnCard, JoinCandidate, TableCard

PARSER_VERSION = "0.3.6"
SHEET_MONTH_COLUMN = "sheet_month"
SOURCE_PART_COLUMN = "source_part"
ISO_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}"
ALL_PARTS_SHEET = "all_parts (numbered split files)"
ALL_MONTHS_SHEET = "all_months (proven date partitions)"
KEY_NAME_TOKENS = ("id", "ma", "code", "phone", "sdt")
DATE_NAME_TOKENS = ("date", "ngay", "thoi_gian")
WHOLE_DONG_RULE = "tien la so nguyen vnd"
MONEY_NAME_TOKENS = frozenset(
    {"tien", "gia", "giam", "tong", "no", "tra", "phi", "von", "thu", "chi", "amount", "price"}
)
FOOTER_PREFIXES = ("ghi chu:", "tong cong", "grand total", "total:")
SUBTOTAL_LABEL = re.compile(
    r"(tong|cong|total|subtotal)(\s+(cong|ngay|thang|tuan|quy|nam|tien))?(\s+[\d/.\-]+)?"
)
MIN_HEADER_MATCHES = 2
MAX_ROW_NOTES = 20
DECIMAL_SCALE = 10
DECIMAL_INTEGER_DIGITS = 28
DECIMAL_TYPE = "DECIMAL(38,10)"


@dataclass(frozen=True)
class SourceHints:
    """Package-wide reading conventions proven by the README or the cited rulebook."""

    day_first: bool | None = None
    whole_dong: bool = False


DEFAULT_HINTS = SourceHints()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_child(root: Path, child: str) -> Path:
    result = (root / child).resolve()
    if not result.is_relative_to(root.resolve()):
        raise InvalidInput("Path escapes business root")
    return result


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _csv(path: Path) -> tuple[list[list[Any]], list[int], list[str]]:
    raw = path.read_bytes()
    warnings = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        detected = from_bytes(raw).best()
        if detected is None:
            raise DataQualityError(f"Unknown encoding: {path.name}") from None
        text = str(detected)
        warnings.append(f"encoding_fallback:{detected.encoding}")
    try:
        dialect = csv.Sniffer().sniff(text[:16384], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
    rows, source_rows = [], []
    try:
        for row in reader:
            rows.append(row)
            source_rows.append(reader.line_num)
    except csv.Error as exc:
        raise DataQualityError(f"Malformed CSV {path.name}, line {reader.line_num}") from exc
    warnings.append(f"delimiter:{repr(delimiter)}")
    return rows, source_rows, warnings


def _is_identifier_column(name: str, nonempty: list[Any]) -> bool:
    key_like = any(token in name.split("_") for token in KEY_NAME_TOKENS)
    leading_zero = any(
        isinstance(v, str) and len(v) > 1 and v.startswith("0") and v.isdigit() for v in nonempty
    )
    return key_like or leading_zero


def is_date_name(name: str) -> bool:
    return any(token in name for token in DATE_NAME_TOKENS)


def is_money_name(name: str) -> bool:
    """Money columns hold whole-dong amounts (rulebook section 9)."""
    return any(token in MONEY_NAME_TOKENS for token in name.split("_"))


def with_sheet_year(value: Any, name: str, year_hint: int | None) -> Any:
    """Append the sheet year to a day/month cell when the rulebook allows it."""
    if (
        year_hint
        and is_date_name(name)
        and isinstance(value, str)
        and re.fullmatch(r"\d{1,2}/\d{1,2}", value.strip())
    ):
        return value.strip() + f"/{year_hint}"
    return value


def _infer(
    values: list[Any],
    name: str,
    *,
    hints: SourceHints = DEFAULT_HINTS,
    year_hint: int | None = None,
) -> tuple[str, list[Any], list[str]]:
    nonempty = [v for v in values if v is not None and str(v).strip()]
    if not nonempty:
        return "VARCHAR", [None] * len(values), []
    if _is_identifier_column(name, nonempty):
        return "VARCHAR", [None if v is None or str(v) == "" else str(v) for v in values], []
    unique = {(type(v).__name__, str(v)): v for v in nonempty}
    day_first = hints.day_first if hints.day_first is not None else date_day_first(unique.values())
    date_cache = {
        key: parse_date(with_sheet_year(v, name, year_hint), day_first=day_first)
        for key, v in unique.items()
    }
    if is_date_name(name) and any(p.value is not None for p in date_cache.values()):
        date_cache = {
            key: parse_date(
                with_sheet_year(v, name, year_hint), day_first=day_first, excel_serial=True
            )
            for key, v in unique.items()
        }
    dates = list(date_cache.values())
    if all(p.value is not None for p in dates):
        return (
            "VARCHAR",
            [
                date_cache[(type(v).__name__, str(v))].value
                if v is not None and str(v).strip()
                else None
                for v in values
            ],
            [],
        )
    money = hints.whole_dong and is_money_name(name)
    style = number_style(unique.values()) or ("vnd" if money else None)
    number_cache = {key: parse_number(v, style=style) for key, v in unique.items()}
    numbers = list(number_cache.values())
    warnings = sorted(
        {
            p.warning
            for p in dates + numbers
            if p.warning and p.warning not in {"not_date", "not_numeric"}
        }
    )
    if all(p.value is not None for p in numbers):
        parsed = [
            number_cache[(type(v).__name__, str(v))].value
            if v is not None and str(v).strip()
            else None
            for v in values
        ]
        if any(
            p is not None
            and (p.as_tuple().exponent < -DECIMAL_SCALE or p.adjusted() >= DECIMAL_INTEGER_DIGITS)
            for p in parsed
        ):
            return (
                "VARCHAR",
                [str(v) if v is not None else None for v in values],
                ["decimal_precision_exceeded"],
            )
        return DECIMAL_TYPE, parsed, warnings
    return (
        "VARCHAR",
        [None if v is None or not str(v).strip() else str(v) for v in values],
        warnings,
    )


def _is_blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def _is_footer(row: list[Any]) -> bool:
    first = next((v for v in row if not _is_blank(v)), None)
    if not isinstance(first, str):
        return False
    return normalized_text(first, accents=True).startswith(FOOTER_PREFIXES)


def _is_subtotal(row: list[Any]) -> bool:
    return any(
        isinstance(v, str) and SUBTOTAL_LABEL.fullmatch(normalized_text(v, accents=True))
        for v in row
    )


def _is_repeated_header(row: list[Any], names: list[str]) -> bool:
    filled = [v for v in row if not _is_blank(v)]
    matches = sum(
        1 for v, n in zip(row, names, strict=True) if isinstance(v, str) and identifier(v) == n
    )
    return matches >= MIN_HEADER_MATCHES and 2 * matches >= len(filled)


def _excluded_reason(row: list[Any], names: list[str]) -> str | None:
    if _is_repeated_header(row, names):
        return "excluded_repeated_header"
    if _is_footer(row):
        return "excluded_footer_row"
    if _is_subtotal(row):
        return "excluded_subtotal_row"
    return None


def record_rows(
    body: list[list[Any]], source_rows: list[int], names: list[str]
) -> tuple[list[list[Any]], list[int], list[str]]:
    """Drop headers, notes and totals; fill a continuation row's blank leading group cells.

    Rulebook section 8: a blank cell in a later row of a row group repeats the group's first row.
    """
    records: list[list[Any]] = []
    sources: list[int] = []
    notes: list[str] = []
    for row, source in zip(body, source_rows, strict=True):
        reason = _excluded_reason(row, names)
        if reason:
            notes.append(f"{reason}:row={source}")
            continue
        lead = next((i for i, v in enumerate(row) if not _is_blank(v)), len(row))
        if records and 0 < lead < len(row):
            row = records[-1][:lead] + row[lead:]
            notes.append(f"group_cells_filled_down:row={source}")
        records.append(list(row))
        sources.append(source)
    return records, sources, _compact_notes(notes)


def _compact_notes(notes: list[str]) -> list[str]:
    """Keep row-level notes readable; a reason seen too often becomes one count."""
    reasons = Counter(n.split(":", 1)[0] for n in notes)
    compact = [n for n in notes if reasons[n.split(":", 1)[0]] <= MAX_ROW_NOTES]
    compact += [f"{r}:rows={c}" for r, c in reasons.items() if c > MAX_ROW_NOTES]
    return compact


def _write_table(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    relative: str,
    source_sha: str,
    sheet: str | None,
    header_row: int,
    column_offset: int,
    rows: list[list[Any]],
    source_rows: list[int],
    warnings: list[str],
    region: int,
    hints: SourceHints = DEFAULT_HINTS,
    year_hint: int | None = None,
) -> TableCard:
    if not rows or not rows[0]:
        raise DataQualityError(f"Empty table: {relative}")
    header, body = rows[0], rows[1:]
    if any(len(row) != len(header) for row in body):
        raise DataQualityError(f"Ragged rows: {relative}, {sheet}")
    table_hash = hashlib.sha256(f"{relative}:{sheet}:{region}".encode()).hexdigest()[:8]
    table_id = f"{identifier(path.stem)}_{table_hash}"
    names: list[str] = []
    counts: Counter[str] = Counter()
    for i, value in enumerate(header):
        name = identifier(str(value or f"column_{i + 1}"))
        counts[name] += 1
        names.append(name if counts[name] == 1 else f"{name}_{counts[name]}")
    if len(names) != len(set(names)) or any(n.startswith("_source_") for n in names):
        raise DataQualityError("Canonical column collision")
    records, record_sources, record_notes = record_rows(body, source_rows, names)
    warnings = [*warnings, *record_notes]
    typed_columns, cards, candidate_keys = [], [], []
    for i, name in enumerate(names):
        values = [r[i] for r in records]
        dtype, parsed, column_warnings = _infer(values, name, hints=hints, year_hint=year_hint)
        typed_columns.append(parsed)
        nonnull = [v for v in parsed if v is not None]
        distinct = len({str(v) for v in nonnull})
        if records and len(nonnull) == len(records) and distinct == len(records):
            candidate_keys.append(name)
        cards.append(
            ColumnCard(
                name=name,
                display_name=str(header[i] or ""),
                source_column=column_offset + i,
                dtype=dtype,
                null_ratio=1 - len(nonnull) / len(records) if records else 0,
                distinct=distinct,
                samples=list(dict.fromkeys(str(v) for v in nonnull))[:5],
                warnings=column_warnings,
            )
        )
    for offset, row in enumerate(records):
        if any(
            normalized_text(str(v), accents=True) in {"tong", "tong cong", "total", "grand total"}
            for v in row
            if v
        ):
            warnings.append(f"possible_total_retained:row={record_sources[offset]}")
    definitions = ", ".join(f"{quote(c.name)} {c.dtype}" for c in cards)
    con.execute(f"CREATE TABLE {quote(table_id)} ({definitions}, _source_row BIGINT)")
    raw_id = f"raw__{table_id}"
    con.execute(
        f"CREATE TABLE {quote(raw_id)} ({', '.join(quote(n) + ' VARCHAR' for n in names)}, _source_row BIGINT)"
    )
    typed_rows = [
        [str(v) if isinstance(v, Decimal) else v for v in r] + [sr]
        for r, sr in zip(zip(*typed_columns, strict=True), record_sources, strict=True)
    ]
    raw_rows = [
        [None if v is None else str(v) for v in r] + [sr]
        for r, sr in zip(body, source_rows, strict=True)
    ]
    for destination, batch in ((table_id, typed_rows), (raw_id, raw_rows)):
        if not batch:
            continue
        con.register("_ingestion_batch", pd.DataFrame(batch, columns=[*names, "_source_row"]))
        try:
            con.execute(f"INSERT INTO {quote(destination)} SELECT * FROM _ingestion_batch")
        finally:
            con.unregister("_ingestion_batch")
    return TableCard(
        table_id=table_id,
        source_file=relative,
        source_sha256=source_sha,
        sheet_or_table=sheet,
        header_row=header_row,
        source_rows=record_sources,
        columns=cards,
        rows=len(records),
        candidate_keys=candidate_keys,
        warnings=warnings,
    )


@dataclass(frozen=True)
class Partition:
    """One sheet or file of a list the source exported in several parts."""

    card: TableCard
    rows: list[list[Any]]
    source_rows: list[int]
    year_hint: int | None
    label: str
    is_month: bool


def _proven_by_sheet_month(con: duckdb.DuckDBPyConnection, card: TableCard) -> bool:
    """True when some date column has only dates inside each row's sheet month."""
    month = quote(SHEET_MONTH_COLUMN)
    for column in card.columns:
        if column.dtype != "VARCHAR" or column.name == SHEET_MONTH_COLUMN or column.warnings:
            continue
        name = quote(column.name)
        present, outside = con.execute(
            f"SELECT COUNT({name}), COUNT(*) FILTER (WHERE {name} IS NOT NULL AND "
            f"(NOT regexp_matches({name}, '{ISO_DATE_PATTERN}') "
            f"OR SUBSTR({name}, 1, 7) <> {month})) FROM {quote(card.table_id)}"
        ).fetchone() or (0, 1)
        if present and not outside:
            return True
    return False


def _is_valid_partition_group(siblings: list[Partition], is_month: bool) -> bool:
    labels = [p.label for p in siblings]
    files = {p.card.source_file for p in siblings}
    if len(siblings) < 2 or len(set(labels)) != len(labels):
        return False
    return is_month or len(files) == len(siblings)


def _write_partition_unions(
    con: duckdb.DuckDBPyConnection, parts: list[Partition], hints: SourceHints
) -> list[TableCard]:
    groups: dict[tuple[str, tuple[str, ...], bool], list[Partition]] = {}
    for part in parts:
        names = tuple(c.name for c in part.card.columns)
        key = (file_family(part.card.source_file), names, part.is_month)
        groups.setdefault(key, []).append(part)
    unions = []
    for (family, names, is_month), siblings in groups.items():
        label_column = SHEET_MONTH_COLUMN if is_month else SOURCE_PART_COLUMN
        if label_column in names or not _is_valid_partition_group(siblings, is_month):
            continue
        siblings.sort(key=lambda p: p.label)
        body = [
            [with_sheet_year(v, n, p.year_hint) for v, n in zip(row, names, strict=True)]
            + [p.label]
            for p in siblings
            for row in p.rows[1:]
        ]
        first = siblings[0].card
        prefix = "all_months" if is_month else "all_parts"
        card = _write_table(
            con,
            Path(f"{prefix}_{identifier(PurePosixPath(family).stem)}"),
            first.source_file,
            first.source_sha256,
            ALL_MONTHS_SHEET if is_month else ALL_PARTS_SHEET,
            0,
            1,
            [[*siblings[0].rows[0], label_column], *body],
            [r for p in siblings for r in p.source_rows],
            sorted({w for p in siblings for w in p.card.warnings}),
            0,
            hints,
        )
        if is_month and not _proven_by_sheet_month(con, card):
            con.execute(f"DROP TABLE {quote(card.table_id)}")
            con.execute(f"DROP TABLE {quote('raw__' + card.table_id)}")
            continue
        parents = [p.card.table_id for p in siblings]
        unions.append(card.model_copy(update={"derived_from": parents, "source_rows": []}))
    return unions


def propose_joins(con: duckdb.DuckDBPyConnection, tables: list[TableCard]) -> list[JoinCandidate]:
    edges = []
    for i, left in enumerate(tables):
        for right in tables[i + 1 :]:
            for lc in left.columns:
                for rc in right.columns:
                    if (
                        lc.name != rc.name
                        or lc.dtype != rc.dtype
                        or not any(x in lc.name for x in ("id", "ma_", "code"))
                    ):
                        continue
                    lvals = {
                        r[0]
                        for r in con.execute(
                            f"SELECT DISTINCT {quote(lc.name)} FROM {quote(left.table_id)} WHERE {quote(lc.name)} IS NOT NULL"
                        ).fetchall()
                    }
                    rvals = {
                        r[0]
                        for r in con.execute(
                            f"SELECT DISTINCT {quote(rc.name)} FROM {quote(right.table_id)} WHERE {quote(rc.name)} IS NOT NULL"
                        ).fetchall()
                    }
                    overlap = (
                        len(lvals & rvals) / min(len(lvals), len(rvals)) if lvals and rvals else 0
                    )
                    if overlap == 0:
                        continue
                    lu, ru = lc.name in left.candidate_keys, rc.name in right.candidate_keys
                    cardinality: Literal[
                        "one-to-one", "one-to-many", "many-to-one", "many-to-many"
                    ] = (
                        "one-to-one"
                        if lu and ru
                        else "one-to-many"
                        if lu
                        else "many-to-one"
                        if ru
                        else "many-to-many"
                    )
                    edges.append(
                        JoinCandidate(
                            left_table=left.table_id,
                            right_table=right.table_id,
                            left_column=lc.name,
                            right_column=rc.name,
                            cardinality=cardinality,
                            overlap=overlap,
                        )
                    )
    return edges


def ingest_business(
    root: Path,
    business_id: str,
    sink: Path,
    limits: Limits | None = None,
    *,
    rulebook: Path | None = None,
) -> Catalog:
    limits = limits or Limits()
    business = safe_child(root, business_id)
    if not business.is_dir():
        raise InvalidInput(f"Business not found: {business_id}")
    files = sorted(p for p in business.rglob("*") if p.is_file())
    if (
        len(files) > limits.max_source_files
        or sum(p.stat().st_size for p in files) > limits.max_source_bytes
    ):
        raise ResourceExceeded("Business package exceeds input limits")
    for path in files:
        if not path.resolve().is_relative_to(business):
            raise InvalidInput("Source symlink escapes business root")
    source_hashes = {p.relative_to(business).as_posix(): sha256_file(p) for p in files}
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                business_id,
                PARSER_VERSION,
                source_hashes,
                sha256_file(rulebook) if rulebook else None,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    target = sink / business_id / fingerprint
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / "catalog.json"
    if manifest.exists() and (target / "normalized.duckdb").exists():
        return Catalog.model_validate_json(manifest.read_text(encoding="utf-8"))
    database = target / "normalized.duckdb"
    # An interrupted build is replaced only in this fingerprinted cache, never in source.
    building = target / "building.duckdb"
    if building.exists():
        building.unlink()
    con = duckdb.connect(str(building))
    tables, warnings, readme = [], [], []
    partitions: list[Partition] = []
    notes = "\n".join(
        p.read_text(encoding="utf-8-sig") for p in files if p.suffix.casefold() in {".md", ".txt"}
    )
    day_first_hint = (
        True if re.search(r"\bdd\s*/\s*mm\s*/\s*yyyy\b", notes, re.IGNORECASE) else None
    )
    rule_text = (
        normalized_text(rulebook.read_text(encoding="utf-8-sig"), accents=True) if rulebook else ""
    )
    sheet_year_rule = "ngay khong ghi nam" in rule_text and "sheet" in rule_text
    hints = SourceHints(day_first_hint, WHOLE_DONG_RULE in rule_text)
    try:
        for path in files:
            relative = path.relative_to(business).as_posix()
            suffix = path.suffix.casefold()
            if suffix in {".md", ".txt"}:
                readme.append(path.read_text(encoding="utf-8-sig"))
            elif suffix == ".csv":
                rows, sr, ws = _csv(path)
                card = _write_table(
                    con,
                    path,
                    relative,
                    source_hashes[relative],
                    None,
                    sr[0] if sr else 1,
                    1,
                    rows,
                    sr[1:],
                    ws,
                    0,
                    hints,
                )
                tables.append(card)
                partitions.append(Partition(card, rows, sr[1:], None, relative, False))
            elif suffix == ".xlsx":
                workbook = load_workbook(path, data_only=True, read_only=False)
                try:
                    for sheet in workbook.worksheets:
                        years = set(re.findall(r"\b((?:19|20)\d{2})\b", sheet.title))
                        year_hint = (
                            int(next(iter(years))) if sheet_year_rule and len(years) == 1 else None
                        )
                        regions = table_regions(sheet)
                        if not regions:
                            warnings.append(f"no_table_regions:{relative}:{sheet.title}")
                        for index, (hr, co, rows) in enumerate(regions):
                            sr = list(range(hr + 1, hr + len(rows)))
                            card = _write_table(
                                con,
                                path,
                                relative,
                                source_hashes[relative],
                                sheet.title,
                                hr,
                                co,
                                rows,
                                sr,
                                [
                                    "xlsx_formula_cached_values_only",
                                    "non_table_report_cells_not_loaded",
                                ],
                                index,
                                hints,
                                year_hint,
                            )
                            tables.append(card)
                            month = month_label(sheet.title) or month_label(path.stem)
                            whole_file = len(workbook.worksheets) == 1 and len(regions) == 1
                            if len(regions) == 1 and (month or whole_file):
                                partitions.append(
                                    Partition(
                                        card, rows, sr, year_hint, month or relative, bool(month)
                                    )
                                )
                finally:
                    workbook.close()
            elif suffix in {".sqlite", ".sqlite3", ".db"}:
                with closing(
                    sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                ) as source:
                    source.execute("PRAGMA query_only = ON")
                    objects = source.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                    for (name,) in objects:
                        if name.casefold().startswith(("pragma_", "sqlite_")):
                            continue
                        cursor = source.execute(f"SELECT * FROM {quote(name)}")
                        rows = [[c[0] for c in cursor.description]] + [
                            list(r) for r in cursor.fetchall()
                        ]
                        sr = list(range(1, len(rows)))
                        tables.append(
                            _write_table(
                                con,
                                path,
                                relative,
                                source_hashes[relative],
                                name,
                                0,
                                1,
                                rows,
                                sr,
                                ["sqlite_source_rows_are_scan_ordinals"],
                                0,
                                hints,
                            )
                        )
                    if source.execute(
                        "SELECT count(*) FROM sqlite_master WHERE type='view'"
                    ).fetchone()[0]:
                        warnings.append(f"sqlite_views_not_executed:{relative}")
            else:
                warnings.append(f"unsupported_file:{relative}")
        if not tables:
            raise DataQualityError("No supported tables found")
        tables.extend(_write_partition_unions(con, partitions, hints))
        joins = propose_joins(con, analysis_tables(tables))
    except duckdb.Error as exc:
        raise DataQualityError(f"DuckDB normalization failed: {type(exc).__name__}") from exc
    finally:
        con.close()
    # Detect any source mutation during read before trusting this fingerprint.
    if any(sha256_file(business / rel) != sha for rel, sha in source_hashes.items()):
        raise DataQualityError("Source changed during ingestion")
    building.replace(database)
    catalog = Catalog(
        business_id=business_id,
        fingerprint=fingerprint,
        parser_version=PARSER_VERSION,
        database_path=str(database.resolve()),
        tables=tables,
        joins=joins,
        warnings=warnings,
        source_fingerprints=source_hashes,
        readme="\n\n".join(readme),
    )
    manifest.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    return catalog

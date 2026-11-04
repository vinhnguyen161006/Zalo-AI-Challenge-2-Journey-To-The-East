"""Read source packages without mutation and write fingerprinted DuckDB snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd
from charset_normalizer import from_bytes
from openpyxl import load_workbook

from datamate.config import Limits
from datamate.data.excel_tables import table_regions
from datamate.data.normalization import identifier, normalized_text, parse_date, parse_number
from datamate.errors import DataQualityError, InvalidInput, ResourceExceeded
from datamate.types import Catalog, ColumnCard, JoinCandidate, TableCard

PARSER_VERSION = "0.1.1"


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


def _infer(values: list[Any], name: str) -> tuple[str, list[Any], list[str]]:
    nonempty = [v for v in values if v is not None and str(v).strip()]
    if not nonempty:
        return "VARCHAR", [None] * len(values), []
    # Preserve identifiers/leading zeros even when every value is numeric.
    key_like = any(token in name.split("_") for token in ("id", "ma", "code", "phone", "sdt"))
    if key_like or any(
        isinstance(v, str) and len(v) > 1 and v.startswith("0") and v.isdigit() for v in nonempty
    ):
        return "VARCHAR", [None if v is None or str(v) == "" else str(v) for v in values], []
    # Parse repeated cell values once per column; preserve original row order/raw data.
    unique = {(type(v).__name__, str(v)): v for v in nonempty}
    date_cache = {key: parse_date(v) for key, v in unique.items()}
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
    number_cache = {key: parse_number(v) for key, v in unique.items()}
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
        # DuckDB DECIMAL(38,10) must not silently round source precision.
        if any(
            p is not None and (p.as_tuple().exponent < -10 or p.adjusted() >= 28) for p in parsed
        ):
            return (
                "VARCHAR",
                [str(v) if v is not None else None for v in values],
                ["decimal_precision_exceeded"],
            )
        return "DECIMAL(38,10)", parsed, warnings
    return (
        "VARCHAR",
        [None if v is None or not str(v).strip() else str(v) for v in values],
        warnings,
    )


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
    typed_columns, cards, candidate_keys = [], [], []
    for i, name in enumerate(names):
        values = [r[i] for r in body]
        dtype, parsed, column_warnings = _infer(values, name)
        typed_columns.append(parsed)
        nonnull = [v for v in parsed if v is not None]
        distinct = len({str(v) for v in nonnull})
        if body and len(nonnull) == len(body) and distinct == len(body):
            candidate_keys.append(name)
        cards.append(
            ColumnCard(
                name=name,
                display_name=str(header[i] or ""),
                source_column=column_offset + i,
                dtype=dtype,
                null_ratio=1 - len(nonnull) / len(body) if body else 0,
                distinct=distinct,
                samples=list(dict.fromkeys(str(v) for v in nonnull))[:5],
                warnings=column_warnings,
            )
        )
    for offset, row in enumerate(body):
        if any(
            normalized_text(str(v), accents=True) in {"tong", "tong cong", "total", "grand total"}
            for v in row
            if v
        ):
            warnings.append(f"possible_total_retained:row={source_rows[offset]}")
    definitions = ", ".join(f"{quote(c.name)} {c.dtype}" for c in cards)
    con.execute(f"CREATE TABLE {quote(table_id)} ({definitions}, _source_row BIGINT)")
    raw_id = f"raw__{table_id}"
    con.execute(
        f"CREATE TABLE {quote(raw_id)} ({', '.join(quote(n) + ' VARCHAR' for n in names)}, _source_row BIGINT)"
    )
    if body:
        typed_rows = [
            [str(v) if isinstance(v, Decimal) else v for v in r] + [sr]
            for r, sr in zip(zip(*typed_columns, strict=True), source_rows, strict=True)
        ]
        raw_rows = [
            [None if v is None else str(v) for v in r] + [sr]
            for r, sr in zip(body, source_rows, strict=True)
        ]
        for destination, records in ((table_id, typed_rows), (raw_id, raw_rows)):
            con.register("_ingestion_batch", pd.DataFrame(records, columns=[*names, "_source_row"]))
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
        source_rows=source_rows,
        columns=cards,
        rows=len(body),
        candidate_keys=candidate_keys,
        warnings=warnings,
    )


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
    root: Path, business_id: str, sink: Path, limits: Limits | None = None
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
        json.dumps([business_id, PARSER_VERSION, source_hashes], sort_keys=True).encode()
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
    try:
        for path in files:
            relative = path.relative_to(business).as_posix()
            suffix = path.suffix.casefold()
            if suffix in {".md", ".txt"}:
                readme.append(path.read_text(encoding="utf-8-sig"))
            elif suffix == ".csv":
                rows, sr, ws = _csv(path)
                tables.append(
                    _write_table(
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
                    )
                )
            elif suffix == ".xlsx":
                workbook = load_workbook(path, data_only=True, read_only=False)
                try:
                    for sheet in workbook.worksheets:
                        regions = table_regions(sheet)
                        if not regions:
                            warnings.append(f"no_table_regions:{relative}:{sheet.title}")
                        for index, (hr, co, rows) in enumerate(regions):
                            sr = list(range(hr + 1, hr + len(rows)))
                            tables.append(
                                _write_table(
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
        joins = propose_joins(con, tables)
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

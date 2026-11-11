"""Expose proven monthly workbook partitions as a UNION ALL view, retaining sources."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

import duckdb

from datamate.types import TableCard


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def monthly_unions(con: duckdb.DuckDBPyConnection, tables: list[TableCard]) -> list[TableCard]:
    groups: dict[tuple[Any, ...], list[tuple[TableCard, str]]] = defaultdict(list)
    for table in tables:
        title = table.sheet_or_table or ""
        match = re.fullmatch(r"(?:T|Thang\s*)?(\d{1,2})[.\-/ _](\d{4})", title, flags=re.IGNORECASE)
        reverse = re.fullmatch(r"(\d{4})[.\-/ _](\d{1,2})", title)
        if table.source_file.lower().endswith(".xlsx") and (match or reverse):
            month, year = (
                (int(match[1]), int(match[2]))
                if match
                else (int(reverse[2]), int(reverse[1]))
                if reverse
                else (0, 0)
            )
            if not 1 <= month <= 12:
                continue
            shape = tuple((c.name, c.dtype) for c in table.columns)
            groups[(table.source_file, shape)].append((table, f"{year:04d}-{month:02d}"))
    views = []
    for siblings in groups.values():
        if len(siblings) < 2 or len({m for _, m in siblings}) != len(siblings):
            continue
        first = siblings[0][0]
        date_columns = []
        for card in first.columns:
            if card.dtype != "VARCHAR" or card.warnings:
                continue
            valid = True
            for table, month_label in siblings:
                bad = con.execute(
                    f"SELECT COUNT(*) FROM {_quote(table.table_id)} WHERE {_quote(card.name)} IS NULL OR SUBSTR({_quote(card.name)}, 1, 7) <> ?",
                    [month_label],
                ).fetchone()
                if not table.rows or bad is None or bad[0]:
                    valid = False
                    break
            if valid:
                date_columns.append(card.name)
        if not date_columns:
            continue
        parents = [t.table_id for t, _ in sorted(siblings, key=lambda pair: pair[1])]
        name = "all_months_" + hashlib.sha256("|".join(parents).encode()).hexdigest()[:12]
        con.execute(
            f"CREATE VIEW {_quote(name)} AS "
            + " UNION ALL ".join(f"SELECT * FROM {_quote(p)}" for p in parents)
        )
        count = sum(t.rows for t, _ in siblings)
        cards, keys = [], []
        for card in first.columns:
            nulls, distinct = con.execute(
                f"SELECT COUNT(*) - COUNT({_quote(card.name)}), COUNT(DISTINCT {_quote(card.name)}) FROM {_quote(name)}"
            ).fetchone() or (count, 0)
            samples = list(
                dict.fromkeys(
                    v
                    for t, _ in siblings
                    for c in t.columns
                    if c.name == card.name
                    for v in c.samples
                )
            )[:5]
            warnings = sorted(
                {
                    w
                    for t, _ in siblings
                    for c in t.columns
                    if c.name == card.name
                    for w in c.warnings
                }
            )
            cards.append(
                card.model_copy(
                    update={
                        "null_ratio": nulls / count,
                        "distinct": distinct,
                        "samples": samples,
                        "warnings": warnings,
                    }
                )
            )
            if not nulls and distinct == count:
                keys.append(card.name)
        views.append(
            TableCard(
                table_id=name,
                source_file=first.source_file,
                source_sha256=first.source_sha256,
                sheet_or_table="all_months (proven date partitions)",
                header_row=0,
                source_rows=[],
                columns=cards,
                rows=count,
                candidate_keys=keys,
                warnings=sorted({w for t, _ in siblings for w in t.warnings}),
                derived_from=parents,
            )
        )
    return views


def analysis_tables(tables: list[TableCard]) -> list[TableCard]:
    covered = {parent for t in tables for parent in t.derived_from}
    return [t for t in tables if t.table_id not in covered]

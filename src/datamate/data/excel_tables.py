"""Detect rectangular blocks separated by empty rows/columns, with source coordinates."""

from __future__ import annotations

from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

MAX_TITLE_LINES = 3
MAX_TITLE_COLUMNS = 2


def _filled(row: list[Any]) -> list[Any]:
    return [v for v in row if v is not None and str(v).strip()]


def is_group_header(first: list[Any], second: list[Any]) -> bool:
    """A sparse all-text row that only groups the full column-name row below it."""
    above, below = _filled(first), _filled(second)
    return 2 * len(above) <= len(below) and all(isinstance(v, str) for v in [*above, *below])


def is_title_block(part: list[list[Any]]) -> bool:
    """A short run of text lines, such as a report title, filter caption and period."""
    is_ragged = len(part[0]) == 1 or any(v is None for row in part for v in row)
    return (
        is_ragged
        and len(part[0]) <= MAX_TITLE_COLUMNS
        and len(part) <= MAX_TITLE_LINES
        and all(isinstance(v, str) for row in part for v in row if v is not None)
    )


def table_regions(sheet: Worksheet) -> list[tuple[int, int, list[list[Any]]]]:
    # Fill only actual merged cells; never infer group membership from arbitrary blanks.
    rows = [list(r) for r in sheet.iter_rows(values_only=True)]
    for merged in sheet.merged_cells.ranges:
        value = rows[merged.min_row - 1][merged.min_col - 1]
        for r in range(merged.min_row - 1, merged.max_row):
            for c in range(merged.min_col - 1, merged.max_col):
                rows[r][c] = value
    regions = []
    start = 0
    while start < len(rows):
        if not any(v is not None and str(v).strip() for v in rows[start]):
            start += 1
            continue
        end = start + 1
        while end < len(rows) and any(v is not None and str(v).strip() for v in rows[end]):
            end += 1
        block = rows[start:end]
        # A report title followed immediately by a header belongs to the same block.
        header_offset = next(
            (i for i, row in enumerate(block) if sum(v is not None for v in row) >= 2), 0
        )
        if len(block) > header_offset + 2 and is_group_header(
            block[header_offset], block[header_offset + 1]
        ):
            header_offset += 1
        block = block[header_offset:]
        occupied = [c for c in range(len(rows[start])) if any(row[c] is not None for row in block)]
        groups: list[list[int]] = []
        for c in occupied:
            if not groups or c > groups[-1][-1] + 1:
                groups.append([])
            groups[-1].append(c)
        for group in groups:
            part = [[row[c] for c in group] for row in block]
            if len(part) > 1 and not is_title_block(part):
                regions.append((start + header_offset + 1, group[0] + 1, part))
        start = end
    return regions

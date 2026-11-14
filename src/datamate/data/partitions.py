"""Identify monthly workbook partitions and hide them behind their verified union."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from datamate.data.normalization import normalized_text
from datamate.types import TableCard

MONTH_FIRST_TITLE = re.compile(r"(?:T|Thang\s*)?(\d{1,2})[.\-/ _](\d{4})", re.IGNORECASE)
YEAR_FIRST_TITLE = re.compile(r"(\d{4})[.\-/ _](\d{1,2})")
SPLIT_FILE_SUFFIX = re.compile(r"[_\- ]?\d+$")
FILE_MONTH = re.compile(r"[_\- ]?(?:T|Thang)\s*_?(\d{1,2})[_\-. ](\d{4})$", re.IGNORECASE)
MAX_MONTH = 12


def month_label(title: str | None) -> str | None:
    """Return YYYY-MM when a sheet title names exactly one month, else None."""
    text = normalized_text(title or "", accents=True)
    in_file_name = FILE_MONTH.search(text)
    if in_file_name:
        text = f"{in_file_name[1]}-{in_file_name[2]}"
    month_first = MONTH_FIRST_TITLE.fullmatch(text)
    year_first = YEAR_FIRST_TITLE.fullmatch(text)
    if month_first:
        month, year = int(month_first[1]), int(month_first[2])
    elif year_first:
        month, year = int(year_first[2]), int(year_first[1])
    else:
        return None
    return f"{year:04d}-{month:02d}" if 1 <= month <= MAX_MONTH else None


def file_family(relative: str) -> str:
    """Group files exported as one list but split by a numeric or year suffix."""
    path = PurePosixPath(relative)
    stem = SPLIT_FILE_SUFFIX.sub("", FILE_MONTH.sub("", path.stem)) or path.stem
    return f"{path.parent.as_posix()}/{normalized_text(stem, accents=True)}{path.suffix.lower()}"


def analysis_tables(tables: list[TableCard]) -> list[TableCard]:
    covered = {parent for t in tables for parent in t.derived_from}
    return [t for t in tables if t.table_id not in covered]

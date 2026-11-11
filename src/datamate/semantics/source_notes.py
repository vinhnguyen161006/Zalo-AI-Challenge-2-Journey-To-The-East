"""Attach explicit README column definitions without inventing a data dictionary."""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any

from datamate.data.normalization import identifier, normalized_text


def column_notes(readme: str, source_file: str) -> dict[str, dict[str, Any]]:
    selected: str | None = None
    notes = {}
    for line_number, line in enumerate(readme.splitlines(), 1):
        if line.startswith("#"):
            header = normalized_text(line, accents=True)
            files = re.findall(r"`([^`]+)`", line)
            selected = files[0] if files and ("cot" in header or "column" in header) else None
        match = re.match(r"\s*[-*]\s+\*\*([^*]+)\*\*\s*:\s*(.+)", line)
        if (
            match
            and selected
            and PurePath(selected).name.casefold() == PurePath(source_file).name.casefold()
        ):
            name, description = match[1], match[2]
            notes[identifier(name)] = {
                "description": description,
                "source": f"README:L{line_number}",
            }
    return notes


def order_key_names(readme: str, source_file: str) -> set[str]:
    return {
        name
        for name, note in column_notes(readme, source_file).items()
        if re.search(
            r"\b(ma don|ma hoa don|order id|invoice id)\b",
            normalized_text(note["description"], accents=True),
        )
    }

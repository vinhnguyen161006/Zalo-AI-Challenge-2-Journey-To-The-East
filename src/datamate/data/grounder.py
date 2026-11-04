from __future__ import annotations

import duckdb
from rapidfuzz.fuzz import WRatio

from datamate.data.ingest import quote
from datamate.data.normalization import normalized_text
from datamate.types import Catalog, GroundingCandidate


def ground(mention: str, catalog: Catalog, limit: int = 8) -> list[GroundingCandidate]:
    candidates = []
    target = normalized_text(mention, accents=True)
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        for table in catalog.tables:
            for column in table.columns:
                if column.dtype != "VARCHAR":
                    continue
                # This is an entity shortlist, not a source of final answers.
                values = con.execute(
                    f"SELECT DISTINCT {quote(column.name)} FROM {quote(table.table_id)} WHERE {quote(column.name)} IS NOT NULL LIMIT 5000"
                ).fetchall()
                for (value,) in values:
                    score = float(WRatio(target, normalized_text(str(value), accents=True)))
                    if score >= 70:
                        candidates.append(
                            GroundingCandidate(
                                table=table.table_id,
                                column=column.name,
                                value=str(value),
                                score=score,
                                ambiguous=False,
                            )
                        )
    candidates.sort(key=lambda c: (-c.score, c.table, c.column, c.value))
    if (
        len(candidates) >= 2
        and candidates[0].score - candidates[1].score < 5
        and candidates[0].value != candidates[1].value
    ):
        for candidate in candidates[:2]:
            candidate.ambiguous = True
    return candidates[:limit]

from __future__ import annotations

import duckdb
from rapidfuzz.fuzz import WRatio

from datamate.config import Limits
from datamate.data.ingest import quote
from datamate.data.normalization import normalized_text
from datamate.types import Catalog, GroundingCandidate


def ground(mention: str, catalog: Catalog, limits: Limits | None = None) -> list[GroundingCandidate]:
    """Shortlist source values that resemble the mention; never an answer by itself."""
    limits = limits or Limits()
    candidates = []
    target = normalized_text(mention, accents=True)
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        for table in catalog.tables:
            for column in table.columns:
                if column.dtype != "VARCHAR":
                    continue
                values = con.execute(
                    f"SELECT DISTINCT {quote(column.name)} FROM {quote(table.table_id)} "
                    f"WHERE {quote(column.name)} IS NOT NULL LIMIT {limits.grounding_scan_limit}"
                ).fetchall()
                for (value,) in values:
                    if len(str(value).strip()) < limits.grounding_min_length:
                        continue
                    score = float(WRatio(target, normalized_text(str(value), accents=True)))
                    if score >= limits.grounding_score_threshold:
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
        and candidates[0].score - candidates[1].score < limits.grounding_ambiguity_margin
        and candidates[0].value != candidates[1].value
    ):
        for candidate in candidates[:2]:
            candidate.ambiguous = True
    return candidates[: limits.grounding_limit]

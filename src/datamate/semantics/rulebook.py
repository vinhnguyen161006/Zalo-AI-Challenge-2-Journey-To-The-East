from __future__ import annotations

import hashlib
from pathlib import Path

from datamate.data.normalization import normalized_text
from datamate.types import RuleEvidence


def parse_rulebook(path: Path) -> list[RuleEvidence]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    rules = []
    for number, line in enumerate(lines, 1):
        if line.strip() and not line.startswith("#") and not set(line.strip()) <= set("|-: "):
            rules.append(
                RuleEvidence(
                    rule_id=f"{fingerprint}:L{number}",
                    source_path=str(path),
                    line_start=number,
                    line_end=number,
                    text=line,
                )
            )
    return rules


def retrieve_rules(question: str, rules: list[RuleEvidence], limit: int = 20) -> list[RuleEvidence]:
    tokens = set(normalized_text(question, accents=True).split())
    ranked = sorted(
        rules, key=lambda r: -len(tokens & set(normalized_text(r.text, accents=True).split()))
    )
    return ranked[:limit]

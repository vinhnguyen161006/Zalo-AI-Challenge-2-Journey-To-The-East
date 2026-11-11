from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
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
    def words(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", normalized_text(value, accents=True)))

    tokens = words(question)
    documents = [words(r.text) for r in rules]
    frequencies = Counter(word for document in documents for word in document)
    weights = {word: math.log(1 + len(rules) / (1 + count)) for word, count in frequencies.items()}
    ranked = sorted(
        range(len(rules)), key=lambda i: (-sum(weights.get(t, 0) for t in tokens & documents[i]), i)
    )
    mandatory = [
        i
        for i, r in enumerate(rules)
        if any(
            fragment in normalized_text(r.text, accents=True)
            for fragment in (
                "hoan toan",
                "o de trong",
                "hai dong",
                "cach ghi",
                "hom nay**",
                "don tinh vao doanh thu",
                "doanh thu mot don",
            )
        )
    ]
    selected = list(dict.fromkeys([*mandatory, *ranked]))[: max(limit, len(mandatory))]
    return [rules[i] for i in selected]

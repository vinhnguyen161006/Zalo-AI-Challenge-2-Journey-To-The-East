"""Conservative Vietnamese language hints, independent of business/question identifiers."""

from __future__ import annotations

import re
from typing import Any

from datamate.data.normalization import normalized_text


def language_hints(question: str) -> dict[str, Any]:
    expanded = question
    expansions = {
        "dthu": "doanh thu",
        "bnhiu": "bao nhiêu",
        "baonhiu": "bao nhiêu",
        "bnhiêu": "bao nhiêu",
        "trc": "trước",
    }
    for short, full in expansions.items():
        expanded = re.sub(r"\b" + short + r"\b", full, expanded, flags=re.IGNORECASE)
    text = normalized_text(expanded, accents=True)
    hints: dict[str, Any] = {"expanded_question": expanded}
    plural = any(token in text for token in ("liet ke", "nhung ", "cac ", "top ", "kem ", " va "))
    if (
        not plural
        and re.search(r"\b(ngay|hom|thang) nao\b", text)
        and any(token in text for token in ("nhat", "dau tien", "cuoi cung"))
        and not any(token in text for token in ("trong tuan", "thu may", "thu nao"))
    ):
        hints["expected_answer_type"] = "date"
        if re.match(r"^(?:e |cho hoi |cho toi biet |cho biet )?(ngay|hom) nao\b", text):
            hints["group_entity"] = "calendar_day"
    if not plural and (
        any(token in text for token in ("ti le", "ty le", "ti trong", "ty trong"))
        or re.search(r"\b(bao nhieu|bao nhiu|bn|bnhiu)\s*(%|phan tram)", text)
    ):
        hints["expected_answer_type"] = "percent"
    if not plural and any(token in text for token in ("co phai", "co dung la", "hay khong")):
        hints["expected_answer_type"] = "boolean"
    return hints

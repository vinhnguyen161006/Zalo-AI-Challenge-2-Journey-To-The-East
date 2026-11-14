"""Conservative Vietnamese language hints, independent of business/question identifiers."""

from __future__ import annotations

import re
from typing import Any

from datamate.data.normalization import normalized_text

MONTH_SHORTHAND = r"\b(?:th|thg|t)\s*(\d{1,2})\s*/\s*(\d{4}|\d{2})\b"
PHRASE_EXPANSIONS = (
    (r"\bso vs\b", "so với"),
    (r"\bth\b(?=\s+(?:trước|truoc|này|nay|rồi|roi|sau))", "tháng"),
    (r"\b(tháng|thang)\s+(?:liền trước|lien truoc|trước đó|truoc do|rồi|roi)", r"\1 trước"),
    (r"\b(?:bữa nay|bua nay|hnay)\b", "hôm nay"),
    (r"\b(?:bữa qua|bua qua|hqua)\b", "hôm qua"),
)


def _month_phrase(match: re.Match[str]) -> str:
    year = match[2] if len(match[2]) == 4 else "20" + match[2]
    return f"tháng {int(match[1])}/{year}"


VIETNAMESE_ONSETS = frozenset(
    "ngh ch gh gi kh ng nh ph qu th tr b c d g h k l m n p r s t v x".split() + [""]
)
TELEX_MIN_LENGTH = 4
BARE_MONTH = re.compile(r"(?<![\d/.\-])\b(\d{1,2})[/-](\d{4}|\d{2})\b(?![/\-]\d)")
MONTH_WORDS = ("tháng ", "thang ", "th ", "t ")
QUARTER_WORDS = ("quý ", "quy ", "q")


def _untelex(match: re.Match[str]) -> str:
    """A lowercase word typed with leftover Telex keys, e.g. thangs, tieenf, dduowcj."""
    word = match[0]
    if not word.isascii() or not word.islower() or len(word) < TELEX_MIN_LENGTH:
        return word
    fixed = re.sub(r"[sfrxj]$", "", word)
    for typed, letter in (("dd", "d"), ("aa", "a"), ("ee", "e"), ("oo", "o")):
        fixed = fixed.replace(typed, letter)
    fixed = re.sub(r"([uoa])w", r"\1", fixed)
    onset = re.match(r"[^aeiouy]*", fixed)
    if fixed == word or onset is None or onset[0] not in VIETNAMESE_ONSETS:
        return word
    return fixed


def _bare_month(match: re.Match[str]) -> str:
    year = match[2] if len(match[2]) == 4 else "20" + match[2]
    before = match.string[: match.start()].casefold()
    if before.endswith(QUARTER_WORDS):
        return f"{match[1]}/{year}"
    prefix = "" if before.endswith(MONTH_WORDS) else "tháng "
    return f"{prefix}{int(match[1])}/{year}"


def language_hints(question: str) -> dict[str, Any]:
    expanded = re.sub(r"[A-Za-z]+", _untelex, question)
    expansions = {
        "dthu": "doanh thu",
        "bnhiu": "bao nhiêu",
        "baonhiu": "bao nhiêu",
        "bnhiêu": "bao nhiêu",
        "trc": "trước",
        "revenue": "doanh thu",
    }
    for short, full in expansions.items():
        expanded = re.sub(r"\b" + short + r"\b", full, expanded, flags=re.IGNORECASE)
    expanded = re.sub(MONTH_SHORTHAND, _month_phrase, expanded, flags=re.IGNORECASE)
    expanded = BARE_MONTH.sub(_bare_month, expanded)
    for pattern, full in PHRASE_EXPANSIONS:
        expanded = re.sub(pattern, full, expanded, flags=re.IGNORECASE)
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

from __future__ import annotations

import calendar as month_calendar
import csv
import re
from datetime import date, timedelta
from pathlib import Path

from datamate.data.normalization import normalized_text
from datamate.semantics.question import language_hints
from datamate.types import RuleEvidence, TimeResolution

COMPARISON_TERMS = (
    "so voi",
    "cung ky",
    "tang truong",
    "hon hay kem",
    "hon kem",
    "tang hay giam",
    "thay doi",
    "len hay xuong",
    "len xuong",
)
NAMED_MONTH = re.compile(r"\bthang\s+(\d{1,2})(?:\s*/\s*|\s+nam\s+)(\d{4})\b")
REFERENCE_DISTANCE = 12


def month_end(start: date) -> date:
    return start + timedelta(days=month_calendar.monthrange(start.year, start.month)[1])


def _is_whole_month(start: date, end: date) -> bool:
    return start.day == 1 and end == month_end(start)


def _named_months(text: str) -> list[tuple[int, date]]:
    """Explicit months with their positions, distinct by month."""
    found: dict[date, int] = {}
    for match in NAMED_MONTH.finditer(text):
        month = int(match[1])
        if 1 <= month <= 12:
            found.setdefault(date(int(match[2]), month, 1), match.start())
    return sorted((position, month) for month, position in found.items())


def _primary_and_reference(
    text: str, months: list[tuple[int, date]]
) -> tuple[date, date, date, date]:
    """The month right after "so voi" is the reference; the other is the asked period."""
    anchor = text.find("so voi")
    first, second = months[0][1], months[1][1]
    reference_first = anchor >= 0 and 0 <= months[0][0] - anchor <= REFERENCE_DISTANCE
    primary, reference = (second, first) if reference_first else (first, second)
    return primary, month_end(primary), reference, month_end(reference)


def _explicit_period(text: str, today: date, rule_text: str) -> tuple[date, date] | None:
    dates = re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    iso_dates = re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    parsed = [date(int(y), int(m), int(d)) for d, m, y in dates]
    parsed += [date(int(y), int(m), int(d)) for y, m, d in iso_dates]
    if len(parsed) == 2 and re.search(r"\btu\b.*\bden\b", text):
        if parsed[0] > parsed[1]:
            raise ValueError("Reversed date range")
        return parsed[0], parsed[1] + timedelta(days=1)
    if len(parsed) == 1:
        return parsed[0], parsed[0] + timedelta(days=1)
    if len(parsed) > 1:
        raise ValueError("Multiple dates require comparison semantics")
    quarter = re.search(r"\bquy\s+([1-4])(?:\s*[/\-]\s*|\s+nam\s+|\s+)?(\d{4})?\b", text)
    if quarter:
        number = int(quarter[1])
        year = int(quarter[2]) if quarter[2] else today.year
        start = date(year, 3 * number - 2, 1)
        if not quarter[2]:
            if "khong ghi nam" not in rule_text:
                raise ValueError("Missing year default rule")
            if start > today:
                start = start.replace(year=year - 1)
        end = month_end(month_end(month_end(start)))
        return start, end
    month = re.search(r"\bthang\s+(\d{1,2})(?:\s*[/\-]\s*|\s+nam\s+|\s+)(\d{4})\b", text)
    if month:
        start = date(int(month[2]), int(month[1]), 1)
        return start, month_end(start)
    month_only = re.search(r"\bthang\s+(\d{1,2})\b", text)
    if month_only and not re.search(r"\b(tet|chap|gieng|am lich)\b", text):
        if "khong ghi nam" not in rule_text:
            raise ValueError("Missing year default rule")
        start = date(today.year, int(month_only[1]), 1)
        if start > today:
            start = start.replace(year=today.year - 1)
        return start, month_end(start)
    year_match = re.search(r"\bnam\s+(\d{4})\b", text)
    if year_match and "tet" not in text:
        return date(int(year_match[1]), 1, 1), date(int(year_match[1]) + 1, 1, 1)
    return None


def resolve_time(
    question: str, as_of_date: str, rules: list[RuleEvidence], calendar_path: Path
) -> TimeResolution:
    today = date.fromisoformat(as_of_date)
    text = normalized_text(language_hints(question)["expanded_question"], accents=True)
    result = TimeResolution(as_of_date=as_of_date)
    rule_text = normalized_text("\n".join(r.text for r in rules), accents=True)
    tomorrow = today + timedelta(days=1)
    start: date | None = None
    end: date | None = None
    try:
        explicit = _explicit_period(text, today, rule_text)
    except ValueError:
        result.ambiguous = True
        result.evidence = ["invalid_or_unsupported_explicit_period"]
        return result
    if explicit:
        start, end = explicit
        result.evidence = ["explicit_question_period"]
    elif "tet" in text:
        match = re.search(r"tet(?: nam)? (\d{4})", text)
        if not match:
            result.ambiguous = True
            result.evidence = ["lunar_year_unspecified"]
            return result
        year = int(match[1])
        tet_rules = [
            (r.rule_id, re.sub(r"[*_`|]", " ", normalized_text(r.text, accents=True)))
            for r in rules
            if "tet" in normalized_text(r.text, accents=True)
        ]
        boundaries = []
        for rule_id, tet_text in tet_rules:
            first_day_match = re.search(r"\b(\d{1,2})\s+thang\s+chap\b", tet_text)
            last_day_match = re.search(r"\b(?:mung\s+)?(\d{1,2})\s+thang\s+gieng\b", tet_text)
            if (
                first_day_match
                and last_day_match
                and all(1 <= int(m[1]) <= 30 for m in (first_day_match, last_day_match))
            ):
                boundaries.append((int(first_day_match[1]), int(last_day_match[1]), rule_id))
        if len({(a, b) for a, b, _ in boundaries}) != 1:
            result.ambiguous = True
            result.evidence = ["tet_window_rule_missing_or_conflicting"]
            return result
        first_day, last_day, rule_id = boundaries[0]
        with calendar_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        starts = [
            r["date"]
            for r in rows
            if (r.get("lunar_year"), r.get("lunar_month"), r.get("lunar_day"))
            == (str(year - 1), "12", str(first_day))
            and r.get("lunar_leap", "false").casefold() == "false"
        ]
        ends = [
            r["date"]
            for r in rows
            if (r.get("lunar_year"), r.get("lunar_month"), r.get("lunar_day"))
            == (str(year), "1", str(last_day))
            and r.get("lunar_leap", "false").casefold() == "false"
        ]
        if len(starts) != 1 or len(ends) != 1:
            result.ambiguous = True
            result.evidence = ["calendar_or_tet_rule_missing"]
            return result
        start, end = date.fromisoformat(starts[0]), date.fromisoformat(ends[0]) + timedelta(days=1)
        result.calendar = "lunar"
        result.evidence = [f"calendar:{calendar_path.name}", rule_id]
    elif "tuan" in text:
        weekdays = {
            "thu hai": 0,
            "thu ba": 1,
            "thu tu": 2,
            "thu nam": 3,
            "thu sau": 4,
            "thu bay": 5,
            "chu nhat": 6,
        }
        week_rules = []
        for rule in rules:
            plain = re.sub(r"[*_`|]", " ", normalized_text(rule.text, accents=True))
            found = re.search(
                r"\btuan\b.{0,30}?bat dau\s+(?:tu\s+)?(thu hai|thu ba|thu tu|thu nam|thu sau|thu bay|chu nhat)\b",
                plain,
            )
            if found:
                week_rules.append((weekdays[found[1]], rule.rule_id))
        if len({day for day, _ in week_rules}) != 1:
            result.ambiguous = True
            result.evidence = ["week_start_not_cited"]
            return result
        week_start = today - timedelta(days=(today.weekday() - week_rules[0][0]) % 7)
        result.evidence = [rule_id for _, rule_id in week_rules]
        if "tuan nay" not in text and any(t in text for t in ("tuan truoc", "tuan roi")):
            start, end = week_start - timedelta(days=7), week_start
        elif "tuan nay" in text:
            start, end = week_start, tomorrow
        else:
            result.ambiguous = True
    elif "hom qua" in text:
        start, end = today - timedelta(days=1), today
    elif "thang nay" in text:
        start, end = today.replace(day=1), tomorrow
    elif "thang truoc" in text:
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
    elif "quy nay" in text:
        start, end = today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1), tomorrow
    elif "quy truoc" in text:
        end = today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1)
        last = end - timedelta(days=1)
        start = last.replace(month=3 * ((last.month - 1) // 3) + 1, day=1)
    elif "nam nay" in text:
        start, end = today.replace(month=1, day=1), tomorrow
    elif "nam truoc" in text or "nam ngoai" in text:
        start, end = date(today.year - 1, 1, 1), date(today.year, 1, 1)
    elif "dau nam" in text:
        start, end = today.replace(month=1, day=1), tomorrow
    elif match := re.search(r"(\d+) ngay gan nhat", text):
        n = int(match[1])
        if n < 1 or n > 36600:
            result.ambiguous = True
        else:
            start, end = today - timedelta(days=n - 1), tomorrow
    elif "hom nay" in text:
        if any(t in text for t in ("tinh den", "con no", "con ton", "con hang")):
            result.ambiguous = True
            result.evidence = ["stock_cutoff_requires_full_history"]
        else:
            start, end = today, tomorrow
    elif match := re.search(r"thang (\d{1,2})/(\d{4})", text):
        month, year = int(match[1]), int(match[2])
        try:
            start = date(year, month, 1)
            end = start + timedelta(days=month_calendar.monthrange(year, month)[1])
        except ValueError:
            result.ambiguous = True
    elif re.search(r"\b(ngay|thang|quy|nam|ram|mung)\b", text):
        result.ambiguous = True
        result.evidence = ["unsupported_temporal_expression"]
    if start and end:
        result.start_iso, result.end_exclusive_iso = start.isoformat(), end.isoformat()
        result.evidence += [
            r.rule_id
            for r in rules
            if any(
                t in normalized_text(r.text, accents=True)
                for t in ("thu hai", "hom nay", "as_of_date")
            )
        ][:3]
        if any(t in text for t in COMPARISON_TERMS):
            comparison_start: date | None = None
            comparison_end: date | None = None
            months = _named_months(text)
            if len(months) == 2 and explicit:
                start, end, comparison_start, comparison_end = _primary_and_reference(text, months)
                result.start_iso, result.end_exclusive_iso = start.isoformat(), end.isoformat()
            elif explicit and _is_whole_month(start, end) and "thang truoc" in text:
                comparison_end = start
                comparison_start = (start - timedelta(days=1)).replace(day=1)
            elif "cung ky nam" in text or re.search(r"cung thang nam", text):

                def previous_year(day: date) -> date:
                    return day.replace(
                        year=day.year - 1,
                        day=min(day.day, month_calendar.monthrange(day.year - 1, day.month)[1]),
                    )

                comparison_start = previous_year(start)
                comparison_end = previous_year(end - timedelta(days=1)) + timedelta(days=1)
            elif "thang nay" in text and "thang truoc" in text:
                comparison_rules = [
                    r
                    for r in rules
                    if "thang" in normalized_text(r.text, accents=True)
                    and "thang truoc" in normalized_text(r.text, accents=True)
                ]
                comparison_text = normalized_text(
                    " ".join(r.text for r in comparison_rules), accents=True
                )
                if "cung so ngay" in comparison_text:
                    comparison_start = (start - timedelta(days=1)).replace(day=1)
                    comparison_end = min(
                        month_end(comparison_start), comparison_start + timedelta(days=today.day)
                    )
                    result.evidence += [r.rule_id for r in comparison_rules]
            elif "tuan nay" in text and any(t in text for t in ("tuan truoc", "tuan roi")):
                comparison_start, comparison_end = (
                    start - timedelta(days=7),
                    end - timedelta(days=7),
                )
            elif "truoc tet" in text and result.calendar == "lunar":
                comparison_start, comparison_end = start - (end - start), start
            if comparison_start and comparison_end:
                result.comparison_start_iso = comparison_start.isoformat()
                result.comparison_end_exclusive_iso = comparison_end.isoformat()
            else:
                result.ambiguous = True
                result.evidence.append("comparison_window_not_resolved")
    stock = any(term in text for term in ("con no", "con ton", "con hang", "cong no", "ton kho"))
    cutoff = any(term in text for term in ("den het", "tinh den", "hom nay"))
    snapshot_rule = "tu dau du lieu" in rule_text and "den het" in rule_text
    if stock and cutoff and snapshot_rule and not result.comparison_start_iso:
        if "hom nay" in text and not re.search(r"\b(thang|quy|nam)\b", text):
            result.start_iso, result.end_exclusive_iso = None, tomorrow.isoformat()
            result.ambiguous = False
            result.evidence = ["snapshot_cutoff_full_history"]
        elif explicit and explicit[1] - explicit[0] == timedelta(days=1):
            result.start_iso, result.end_exclusive_iso = None, explicit[1].isoformat()
            result.ambiguous = False
            result.evidence = ["snapshot_cutoff_full_history", "explicit_question_date"]
    return result

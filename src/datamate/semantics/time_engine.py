from __future__ import annotations

import calendar as month_calendar
import csv
import re
from datetime import date, timedelta
from pathlib import Path

from datamate.data.normalization import normalized_text
from datamate.types import RuleEvidence, TimeResolution


def resolve_time(
    question: str, as_of_date: str, rules: list[RuleEvidence], calendar_path: Path
) -> TimeResolution:
    today = date.fromisoformat(as_of_date)
    text = normalized_text(question, accents=True)
    result = TimeResolution(as_of_date=as_of_date)
    rule_text = normalized_text("\n".join(r.text for r in rules), accents=True)
    tomorrow = today + timedelta(days=1)
    start: date | None = None
    end: date | None = None
    if "tet" in text:
        match = re.search(r"tet(?: nam)? (\d{4})", text)
        if not match:
            result.ambiguous = True
            result.evidence = ["lunar_year_unspecified"]
            return result
        year = int(match[1])
        with calendar_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        starts = [
            r["date"]
            for r in rows
            if (r.get("lunar_year"), r.get("lunar_month"), r.get("lunar_day"))
            == (str(year - 1), "12", "23")
            and r.get("lunar_leap", "false").casefold() == "false"
        ]
        ends = [
            r["date"]
            for r in rows
            if (r.get("lunar_year"), r.get("lunar_month"), r.get("lunar_day"))
            == (str(year), "1", "10")
            and r.get("lunar_leap", "false").casefold() == "false"
        ]
        if len(starts) != 1 or len(ends) != 1 or "23" not in rule_text or "10" not in rule_text:
            result.ambiguous = True
            result.evidence = ["calendar_or_tet_rule_missing"]
            return result
        start, end = date.fromisoformat(starts[0]), date.fromisoformat(ends[0]) + timedelta(days=1)
        result.calendar = "lunar"
        result.evidence = [f"calendar:{calendar_path.name}"]
    elif "tuan" in text:
        if "thu hai" not in rule_text:
            result.ambiguous = True
            result.evidence = ["week_start_not_cited"]
            return result
        monday = today - timedelta(days=today.weekday())
        if any(t in text for t in ("tuan truoc", "tuan roi")):
            start, end = monday - timedelta(days=7), monday
        elif "tuan nay" in text:
            start, end = monday, tomorrow
        else:
            result.ambiguous = True
    elif "hom qua" in text:
        start, end = today - timedelta(days=1), today
    elif "thang nay" in text:
        start, end = today.replace(day=1), tomorrow
    elif "thang truoc" in text:
        # Without an explicit comparison context, resolve the complete preceding month.
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        if any(t in text for t in ("so voi", "tang", "giam")):
            result.ambiguous = True
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
    elif re.search(r"ngay|thang|quy|nam|ram|mung|den|tu", text):
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
    return result

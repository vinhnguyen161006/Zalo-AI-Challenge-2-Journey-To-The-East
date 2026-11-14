"""Diagnostic: score compiled sales intents per family on labelled businesses (no inference use)."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import duckdb

from datamate.eval.matching import matches
from datamate.semantics.rulebook import parse_rulebook
from datamate.semantics.sales_filters import sales_intent
from datamate.semantics.sales_model import (
    ROW_MEASURES,
    SalesModel,
    infer_sales_model,
    period_outside_orders,
    program_sql,
    sales_answer,
    sales_programs,
)
from datamate.semantics.time_engine import resolve_time
from datamate.types import Catalog


def predict(question: dict[str, str], model: SalesModel, catalog: Catalog, split: Path) -> dict:
    intent = sales_intent(question["question"], catalog, model)
    if intent is None:
        return {"type": "skip"}
    rules = parse_rulebook(split / "rulebook.md")
    window = resolve_time(
        question["question"], question["as_of_date"], rules, split / "calendar.csv"
    )
    programs = sales_programs(intent, window)
    if programs is None:
        return {"type": "skip"}
    family = intent.family + (f"/{intent.sales_filter.kind}" if intent.sales_filter else "")
    if any(period_outside_orders(model, catalog, p.start, p.end) for p in programs):
        return {"type": "unanswerable", "family": family}
    statements = [program_sql(model, p) for p in programs]
    if any(sql is None for sql in statements):
        return {"type": "skip"}
    with duckdb.connect(catalog.database_path, read_only=True) as con:
        values = [
            con.execute(str(sql)).fetchall()
            if p.measure in ROW_MEASURES
            else con.execute(str(sql)).fetchone()[0]
            for sql, p in zip(statements, programs, strict=True)
        ]
    answer = sales_answer(intent, values)
    if answer is None:
        return {"type": "skip"}
    value = answer[1]
    if isinstance(value, Decimal):
        value = value.normalize()
    if isinstance(value, list):
        value = [v.normalize() if isinstance(v, Decimal) else v for v in value]
    return {"type": answer[0], "answer": value, "family": family}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--show", type=int, default=20)
    args = parser.parse_args()
    labels = {
        json.loads(line)["id"]: json.loads(line)
        for line in (args.split / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    }
    catalogs = {
        business: Catalog.model_validate_json(Path(path).read_text(encoding="utf-8"))
        for business, path in json.loads(args.index.read_text()).items()
    }
    models = {business: infer_sales_model(catalog) for business, catalog in catalogs.items()}
    stats: Counter[str] = Counter()
    shown = 0
    for line in (args.split / "questions.jsonl").read_text(encoding="utf-8").splitlines():
        question = json.loads(line)
        model = models.get(question["business_id"])
        if model is None:
            continue
        prediction = predict(question, model, catalogs[question["business_id"]], args.split)
        if prediction["type"] == "skip":
            continue
        family = prediction.pop("family")
        correct = matches({"id": question["id"], **prediction}, labels[question["id"]])
        stats[f"{family}:{'correct' if correct else 'wrong'}"] += 1
        if not correct and shown < args.show:
            shown += 1
            gold = labels[question["id"]]
            print(
                "MISS",
                family,
                question["business_id"],
                question["question"][:80],
                prediction.get("answer", prediction["type"]),
                "gold",
                gold.get("answer", gold["type"]),
            )
    print(json.dumps(dict(sorted(stats.items())), indent=1))


if __name__ == "__main__":
    main()

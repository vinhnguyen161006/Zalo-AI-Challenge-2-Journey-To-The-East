"""Summarize local ingestion and recheck source hashes, without reading gold answers."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from datamate.data.ingest import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("outputs/ingestion_report.json"))
    parser.add_argument("--data-root", type=Path, default=Path("train/data"))
    parser.add_argument("--output", type=Path, default=Path("outputs/ingestion_summary.json"))
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    formats: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    tables = []
    source_count = 0
    mismatches = []
    for business in report["loaded"]:
        catalog = json.loads(Path(business["catalog"]).read_text(encoding="utf-8"))
        for relative, digest in catalog["source_fingerprints"].items():
            path = args.data_root / catalog["business_id"] / relative
            source_count += 1
            if sha256_file(path) != digest:
                mismatches.append(f"{catalog['business_id']}/{relative}")
        for table in catalog["tables"]:
            tables.append(table)
            formats[Path(table["source_file"]).suffix] += 1
            notes.update(w.split(":")[0] for w in table["warnings"])
            warnings.update(w for column in table["columns"] for w in column["warnings"])
    summary = {
        "businesses": len(report["loaded"]),
        "failed": len(report["failed"]),
        "tables": len(tables),
        "rows": sum(t["rows"] for t in tables),
        "source_files": source_count,
        "table_formats": dict(formats),
        "table_notes": dict(notes),
        "column_warnings": dict(warnings),
        "source_hash_mismatches": mismatches,
        "scope": "Ingestion checks, not business-semantic correctness or model accuracy",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    if summary["failed"] or mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

# Supplied train release: ingestion smoke report

Measured locally on 2026-11-04. No `answers.jsonl` was read for this check.

Commands:

```sh
python -m datamate inspect --config configs/dev.yaml --all-businesses
python scripts/export_ingestion_report.py
```

| Check | Observed result |
|---|---:|
| Business packages loaded | 30 / 30 |
| Failed packages | 0 |
| Tables / detected Excel regions | 340 |
| Imported rows across these tables | 3,997,077 |
| Package source files re-hashed | 158 |
| Source hash mismatches | 0 |
| CSV tables | 20 |
| Excel regions | 303 |
| SQLite tables | 17 |

Local reports: `outputs/ingestion_report.json`, `outputs/ingestion_summary.json`
and fingerprinted `outputs/cache/<business>/<fingerprint>/catalog.json`.
These contain source metadata/sample values and stay gitignored.

Warnings remain visible: 588 columns have `ambiguous_numeric` signals and 245
have `ambiguous_date` signals. Counts are per column, not error rows. Ambiguous
values remain strings; no numeric/date guess is silently made. The 303 workbook
regions also carry notes that cached formula values are used and cells outside
detected table regions are not imported. SQLite row provenance uses scan ordinals.

The row count includes retained totals and source duplicate rows where present;
it is not a certified count of sales/orders or a deduplicated semantic dataset.
Successful loading is not evidence that every business metric, temporal meaning,
group-fill rule or dirty-data layout is understood correctly. Those checks remain
implementation work before enabling model answers.

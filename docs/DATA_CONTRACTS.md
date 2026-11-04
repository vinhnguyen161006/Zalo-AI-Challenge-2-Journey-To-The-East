# Data contracts

Internal Pydantic schemas have version 1 and forbid unknown fields. Question
records validate nonblank text, ISO dates, bounded IDs and path-safe business IDs;
extra question metadata stays in `extra`.

Sources are never written. Package cache keys include business ID, relative
filenames, SHA-256 and parser version. Prediction resume additionally binds
config, question, rulebook, calendar, all dataset bytes and pipeline version.
Inputs must stay immutable within a run; new runs re-fingerprint edited packages.

Each imported table has a typed table and `raw__<table_id>` string mirror, plus
`_source_row`. Catalogs record source hashes, sheet/table, header, raw display
name and source column. SQLite row numbers are scan ordinals, not physical rowids.
Raw binary types/styles remain available in the untouched original files.

CSV supports UTF-8/BOM, logged encoding fallback, comma/semicolon/tab and strict
quoting. Ragged/malformed data fails explicitly. Ambiguous numbers remain strings
with warnings. DECIMAL(38,10) is used only without source precision loss;
leading-zero identifiers stay strings. Percent points are not converted to ratios.
Ambiguous dates are flagged, not guessed.

Excel handles rectangular regions separated by blank rows/columns, report
headings and actual merged-cell ranges. Formula values use workbook caches.
Arbitrary nested headers, formula recalculation and source-rule group-fill are
not complete. Potential totals are retained and flagged. Business deduplication
is not applied at ingestion; semantic operations must prove the correct grain.

SQLite uses mode=ro/query_only. Ordinary tables are imported; views are reported
but not executed to avoid untrusted definitions. Unsupported files are fingerprinted
and reported. Join proposals remain heuristic; they are not foreign-key proof.
SQL provenance is table/column-level and is never marked complete row-level.

Local output shape follows the supplied rulebook, including seven types and
special markers. Exact Decimal JSON avoids float roundtrips. Missing scalars
are not zero. Official alias/rounding/tolerance semantics are not reimplemented
as authority; the local scorer is only an exact-match diagnostic.

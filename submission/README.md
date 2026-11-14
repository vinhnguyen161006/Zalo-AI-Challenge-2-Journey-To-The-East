# DataMate solution: team journey to the east

Zalo AI Challenge 2026, DataMate track. Image tag: `zaic2026_datamate_journey_to_the_east:final`.

## Run

```bash
bash /code/predict.sh
```

`predict.sh` takes no arguments. It reads `/data` (read-only), writes
`/result/submission.jsonl` one line per question as soon as the question is answered,
and one log file per question under `/result/logs/`. A snapshot cache is written under
`/result/cache/`. No network, GPU or model server is used. All settings (seed 12,
calibrated decision policy, limits) are fixed in
`/code/calibration/deployment-config.yaml` and `/code/configs/`.

## Approach

1. **Ingestion** (`src/datamate/data/`): every CSV, XLSX and SQLite file of a business is
   loaded read-only into a DuckDB snapshot. Dirty values are normalized conservatively:
   mixed date formats (`dd/mm/yyyy`, `1 thg 7 2025`, Excel serials, sheet-year dates),
   Vietnamese money (`1.600đ`, `9,200`, `19tr990`, `20,1tr`, `69k`, `129 000`), quantities
   with units (`2 cái`). Rulebook section 8 is applied: report titles, filter captions,
   repeated headers, `Tổng cộng` / `Tổng ngày` / `Cộng` subtotal rows and note footers are
   not records; blank leading cells of a continuation row repeat the group's first row.
   Monthly sheets and files split as `_1`, `_2`, `_2026` or by month in the file name are
   merged into one table when every dated row lies in its own month.
2. **Compiled sales model** (`src/datamate/semantics/sales_model.py`): column roles come
   from the package README controlled vocabulary ("mã đơn", "ngày bán", "trạng thái đơn",
   "thành tiền sau giảm", "tiền hàng của đơn", "số lượng khách trả lại", "hàng trả ghi
   số lượng âm"). Rulebook section 1 is compiled into SQL: an order is valid when it is
   neither cancelled nor returned (one returned line excludes the whole order), it belongs
   to the period of its placement date, revenue is the sum of its line amounts, and two
   identical lines of one order count once. A period fully outside the order dates is
   `unanswerable` (section 7). Time windows come from the rule-based time engine
   (`src/datamate/semantics/time_engine.py`), half-open, weeks from Monday.
3. **Routing**: only questions made entirely of measure, period and filler words
   (plain revenue or order count for one period) reach the compiled program. Every other
   question abstains.
4. **Execution**: SQL passes an AST allowlist (`src/datamate/execution/sql_policy.py`)
   and runs in an isolated read-only worker process.
5. **Decision**: an answer is published only when its evidence bucket has a calibrated
   Wilson 95% lower bound above 0.5, the break-even accuracy for +1 / -1 / 0 scoring.
   Calibration and holdout used disjoint train businesses (see `training_code/`).
   Revenue bucket: 38 of 38 correct over 5 businesses (lower bound 0.908). The order-count
   bucket had 9 samples, below the 20 required, so order counts abstain.
6. **Output**: code-only serialization; numbers are exact integers or decimals.

No answer, question id or business id is hard-coded.

### Per-question log fields

Each `/result/logs/<sha256(id)>.json` holds `id`, `answer` (the published record),
`elapsed_ms`, `thinking` (always `null`: no model is called), `code` (the executed SQL),
`code_source` (`src/datamate/semantics/sales_model.py::sales_sql` for compiled programs),
`output` (the computed value) and the full trace (`sales_model` inputs, time window,
execution result, decision reasons).

## Models

No language, embedding, reranking or classification model is used at inference.
Total inference parameters: **0**. No model weights are included or required.
(The repository also contains an experimental Qwen3.5 4B + 9B route; it scored 0 of 130
on train questions, failed calibration, and is disabled in this configuration.)

## Data

Only the organizer-provided train split (`/code/training_code/data/train`, 30 businesses,
3,000 questions with answers) was used, to develop and calibrate the rules and decision
thresholds. Business split with seed 12: 18 development, 6 calibration (600 questions),
6 holdout (480 questions). Public and private test data were never used for training,
tuning or answer collection. No data was generated.

| Stage | Questions | Correct | Wrong | Abstain | Local PA |
|---|---|---|---|---|---|
| Calibration | 600 | 48 | 0 | 552 | 0.0800 |
| Holdout | 480 | 41 | 0 | 439 | 0.0854 |

Local PA uses the rulebook scoring text; it is not the organizer evaluator.

## Reproduce calibration

```bash
bash /code/training_code/train.sh
```

See `/code/training_code/README.md`. Runtime is a few minutes on CPU and deterministic.

## Layout

| Path | Content |
|---|---|
| `/code/predict.sh` | entry point |
| `/code/src/datamate/` | inference code (run with `PYTHONPATH=/code/src`) |
| `/code/configs/` | runtime configuration (`sales.yaml`) |
| `/code/calibration/` | fitted calibration artifact and deployment config |
| `/code/training_code/` | calibration code, data, logs and `data_manifest.json` |
| `/code/docs/` | design notes, ADRs, threat model |
| `/code/requirements.txt` | pinned Python dependencies |

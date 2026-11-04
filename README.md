# AEGDA — DataMate Agent

Code base cho agent phân tích dữ liệu doanh nghiệp chạy offline, xây theo
[CODEX_MASTER_SPEC.md](CODEX_MASTER_SPEC.md) và [architecture.mmd](architecture.mmd).
Môi trường đã kiểm thử: **Python 3.12 trên Windows**.

Đã có CSV/XLSX/SQLite ingestion, DuckDB snapshots, schema catalog và provenance,
rule/time resolver, entity grounding, typed PlanIR, model clients cục bộ, SQL AST
allowlist, executor chạy process riêng, DataOpsDSL, pipeline có budget, JSONL
writer có resume, CLI và kiểm thử CPU.

Đã kiểm tra **98 tests**, lint/typecheck và smoke ingest **30/30 doanh nghiệp**.
Chi tiết dữ liệu/cảnh báo: [DATA_INGESTION_REPORT.md](docs/DATA_INGESTION_REPORT.md).

**Đây là code base phát triển, chưa submission-ready.** Mặc định model bị tắt;
`predict` ghi `abstain` cho từng câu. Kể cả khi nối model, policy hiện vẫn abstain
cho đến khi hoàn thiện semantic verification và calibration. `test-fixtures`
thực sự chạy SQL/DataOps trên dữ liệu tổng hợp, không phải benchmark câu hỏi train.

## Cài đặt

Mở PowerShell tại thư mục repo. `.venv` đã được tạo trên máy hiện tại; với máy mới:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

Không cần activate môi trường, GPU hoặc torch. Lock ghi lại dependencies CPU/dev
đã resolve; môi trường GPU/vLLM chưa được lock hay kiểm thử. Package khai báo
Python 3.11–3.12; Python 3.11 chưa được chạy kiểm thử trên máy này.

## Chạy nhanh

```powershell
# Kiểm tra input train, không đọc answers.jsonl
.\.venv\Scripts\python.exe -m datamate preflight --config configs/dev.yaml

# Chạy SQL và DataOps thật trên fixture tổng hợp
.\.venv\Scripts\python.exe -m datamate test-fixtures

# Xuất catalog/provenance vào outputs/cache
.\.venv\Scripts\python.exe -m datamate inspect --config configs/dev.yaml --business-id biz_101
.\.venv\Scripts\python.exe -m datamate inspect --config configs/dev.yaml --all-businesses

# Test luồng JSONL với model tắt: kết quả abstain có chủ ý
.\.venv\Scripts\python.exe -m datamate predict --config configs/default.yaml --output outputs/demo.jsonl
.\.venv\Scripts\python.exe -m datamate validate --config configs/default.yaml --predictions outputs/demo.jsonl
```

Thêm `--resume` vào cùng lệnh predict để tiếp tục hoặc nhận kết quả đã hoàn tất.
`configs/default.yaml` dùng fixture; `configs/dev.yaml` dùng `train/`. Đường dẫn
tính theo config, không theo thư mục chạy lệnh. Biến môi trường trong đường dẫn
được expand; biến chưa tồn tại làm config báo lỗi. Official mode bị chặn cho đến
khi tích hợp kit/validator; bật một cờ YAML không thể bỏ qua gate này.

## Cấu trúc

```text
configs/                 cấu hình dev/official, model manifest
src/datamate/
  data/                  ingestion, raw/typed snapshots, catalogs, join proposals
  semantics/             rule spans, thời gian và lịch split
  llm/ + prompts/        local/fake clients, role prompts, plan validation
  routing/               FAST/SLOW và giới hạn tài nguyên
  execution/             SQL AST policy, process worker, DataOpsDSL
  verification/          kiểm tra type/source/rule; semantics chưa hoàn chỉnh
  decision/              abstain với reason codes, utility threshold
  adapters/              train inputs, typed JSONL, checkpoint/resume
  eval/                  local diagnostics, split theo business
tests/                   unit, integration, security, e2e, fixtures tự tạo
scripts/                 tạo fixture, audit safetensors
docs/                    audit, hợp đồng dữ liệu, runbook, status, threat model
```

## Model cục bộ

Sau khi xác minh weights, điền model identities/revisions và endpoint trong config
riêng rồi đặt `models.backend: local`. Chỉ hỗ trợ HTTP loopback, không gọi cloud,
không tự tải weights. Client dùng structured JSON của giao thức chat-completions;
tương thích Qwen/vLLM cụ thể chưa được thử. Small-model client dùng chung cho
planner và DataOps; DataOps nhận nguồn gốc độc lập, không nhận SQL/PlanIR/result.

```powershell
.\.venv\Scripts\python.exe -m datamate audit-models --checkpoint models/planner
# Hoặc kiểm tra tất cả checkpoint trong manifest:
.\.venv\Scripts\python.exe -m datamate audit-models --config configs/models.yaml
```

Audit đếm stored tensor elements từ safetensors và hash shard; không tự kết luận
số tham số gốc của quantization hoặc chứng nhận tổng <=15B.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src/datamate
```

Nếu sandbox chặn Temp của pytest, dùng `--basetemp outputs/pytest-base`. Pytest
quản lý/xóa thư mục tạm này; không dùng thư mục chứa dữ liệu cần giữ.

## Docker CPU

```sh
docker build -t datamate-dev .
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /app/outputs:uid=10001,gid=10001 datamate-dev
```

Recipe CPU/dev; chưa phải entrypoint hay image chính thức. Build cần tải
dependencies, runtime demo không cần mạng. Xem [RUNBOOK.md](docs/RUNBOOK.md),
[THREAT_MODEL.md](docs/THREAT_MODEL.md) và [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

# DataMate — AEGDA

Agent trả lời câu hỏi tiếng Việt trên CSV, Excel và SQLite, chạy inference offline.
Kiến trúc trong [architecture.mmd](architecture.mmd): Planner **Qwen3.5 4B** →
SQLCoder **9B** và PandasCoder **4B** độc lập → thực thi → kiểm chứng → trả lời hoặc abstain.
PandasCoder sinh thao tác JSON có kiểu; SQL có AST allowlist và worker chỉ đọc.
Không chạy Python tùy ý do model sinh ra.

**Trạng thái:** pipeline chạy được, đã qua 202 test Linux; calibration/holdout
chưa hoàn tất, chưa có file public cuối. Fixture CPU trong image hai model:
1 đúng, 0 sai, 2 abstain; đây không phải điểm thi. Xem
[trạng thái chi tiết](docs/IMPLEMENTATION_STATUS.md).

## 1. Clone và cài môi trường

Yêu cầu **Python 3.12**, Git. Chạy các lệnh tại thư mục gốc repository.
Package và test không cần torch/CUDA.

Windows PowerShell:

```powershell
git clone https://github.com/vinhnguyen161006/Zalo-AI-Challenge-2-Journey-To-The-East.git
cd Zalo-AI-Challenge-2-Journey-To-The-East
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m datamate test-fixtures
```

Linux/macOS, cho package và CLI:

```sh
git clone https://github.com/vinhnguyen161006/Zalo-AI-Challenge-2-Journey-To-The-East.git
cd Zalo-AI-Challenge-2-Journey-To-The-East
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install -e . --no-deps
python -m datamate test-fixtures
```

Các script `.ps1` điều phối/quan sát bên dưới dùng Windows PowerShell.
Nếu execution policy chặn script, gọi bằng `powershell -NoProfile -ExecutionPolicy
Bypass -File scripts/watch_progress.ps1`. Linux dùng CLI hoặc entrypoint Docker
`train.sh`/`predict.sh`; các lệnh Docker hoạt động sau khi thay đường dẫn mount
bằng đường dẫn tuyệt đối trên host.

## 2. Chuẩn bị dữ liệu riêng trên máy bạn

GitHub **không chứa dữ liệu cuộc thi, weights, cache hay kết quả chạy**.
Tải bộ train/public từ BTC hoặc nhận riêng từ chủ dự án rồi giải nén thành:

```text
data/
  train/
    data/<business_id>/...     # CSV/XLSX/SQLite và README.md
    rulebook.md
    calendar.csv
    questions.jsonl
    answers.jsonl
  public_test/
    data/<business_id>/...
    rulebook.md
    calendar.csv
    questions.jsonl
```

Tránh lồng thêm `train/train/`. Chỉ train có nhãn. Không dùng public/private
để fine-tune hoặc fit calibration. Pipeline đọc rulebook/calendar của chính split.

```powershell
.\.venv\Scripts\python.exe -m datamate preflight --config configs/dev.yaml
```

## 3. Cài hai model và thử inference

Cài Ollama riêng. Môi trường đã kiểm tra dùng **Ollama 0.40.16** và hai checkpoint
**Qwen3.5 4B + 9B Q4_K_M**, giấy phép Apache-2.0. Planner/PandasCoder dùng chung
4B, SQLCoder/critic dùng 9B; không có embedding/reranker bổ sung. Xem
[khai báo và giới hạn audit model](docs/MODEL_PARAMETER_REPORT.md).
GGUF audit đếm 13.949.079.280 stored tensor elements gồm projector; chưa phải
chứng nhận tham số/eligibility chính thức của BTC.

Tải weights lúc chuẩn bị máy, trước inference offline:

```powershell
ollama pull qwen3.5:4b
ollama pull qwen3.5:9b
ollama list
.\.venv\Scripts\python.exe -m datamate check-models --config configs/qwen35.yaml
```

Ollama phải đang chạy; nếu chưa, chạy `ollama serve` ở cửa sổ khác. Config pin
revision: nếu mismatch, kiểm tra checkpoint/phiên bản Ollama; không xóa pin để
bỏ qua. Digest Windows và Linux khác nhau, dùng đúng config tương ứng. Đổi
checkpoint/config cần calibration lại.

Khuyến nghị local **32 GB RAM**. Weights khoảng 10 GB; cần thêm dung lượng cho
Docker image/build cache và kết quả. GPU không bắt buộc; GPU 4 GB không chứa
trọn hai model. T1000 local đã timeout khi thử Docker GPU, nên lượt hiện tại dùng
CPU và có thể chạy rất lâu. Chưa đo giới hạn 6 giờ trên 2 × RTX 5090.

Thử ba câu tổng hợp có sẵn, không cần dữ liệu thật:

```powershell
.\.venv\Scripts\python.exe -m datamate evaluate --config configs/qwen35-fixture.yaml --labels tests/fixtures/synthetic_business/labels.jsonl --output outputs/my-fixture/run.jsonl
```

Evaluate nhỏ trên train, baseline chưa hiệu chỉnh:

```powershell
.\.venv\Scripts\python.exe -m datamate evaluate --config configs/qwen35-evaluation.yaml --labels data/train/answers.jsonl --limit 10 --output outputs/my-eval/train10.jsonl --resume
```

Nhãn chỉ dùng chấm sau inference, không đưa vào prompt. PA local =
`(correct - wrong) / total`, không thay evaluator BTC. Config `default.yaml`/
`dev.yaml` tắt model, abstain có chủ ý; dùng `qwen35*` để chạy model thật.
Config `ollama*` 3B là baseline lịch sử.

## 4. Docker: calibration → holdout → public

Cài Docker Desktop, chọn Linux containers. Chuẩn bị weights ở bước 3 trước.
Build cần mạng để cài dependencies; container inference chạy **network none**.
Máy mới phải build image: GitHub không chứa image/volume trên máy tác giả.

Xuất đúng hai checkpoint vào thư mục mới, kiểm hash rồi build:

```powershell
.\.venv\Scripts\python.exe scripts/export_ollama.py --destination models/qwen35-export --model qwen3.5:4b --model qwen3.5:9b
docker build -f Dockerfile.offline --build-context model_store=./models/qwen35-export --build-arg DATAMATE_CONFIG=configs/qwen35-docker.yaml -t datamate-local:qwen35 .
```

Exporter mặc định đọc `~/.ollama/models`; thêm `--source` nếu store ở chỗ khác.
Đích phải mới/rỗng. Có thể thêm `--hardlink` khi cùng ổ, nhưng không sửa blob
đã hardlink. `Dockerfile.workflow` chỉ refresh runtime image có sẵn, không dùng
để build từ đầu. `Dockerfile` không hậu tố là demo CPU không chứa model.

Chuẩn bị rồi chạy toàn bộ workflow, ví dụ máy có ít nhất 12 luồng CPU:

```powershell
.\scripts\finish_submission.ps1 -Work outputs/my-run -Cpus 12 -PrepareOnly
.\scripts\finish_submission.ps1 -Work outputs/my-run -Cpus 12
```

`-PrepareOnly` chưa chạy model hay đọc nội dung nhãn. Workflow dùng 120 câu
calibration và 120 câu holdout thuộc các doanh nghiệp riêng. Chỉ khi đủ bằng
chứng calibration mới tạo release, image `datamate-local:calibrated` và chạy public.
Đây là hiệu chỉnh quyết định, không fine-tune weights. Thiếu bằng chứng sẽ báo
`failed`, giữ log, không tạo bản cuối giả thành công.

Mặc định giới hạn RAM container `12g`, CPU 6. Chọn `-Cpus` theo máy, giữ nguyên
tham số khi resume. `-UseGpu` chỉ dùng sau khi kiểm tra GPU Docker hoạt động.
Không chạy nhiều workflow model đồng thời trên máy nhỏ. Sau khi tắt máy, bật
Docker rồi chạy lại **đúng lệnh, đúng Work và tham số** để resume; các câu đã lưu
được giữ, câu chưa xong có thể phải chạy lại. Đổi code/model/dataset/config cần
Work mới và build lại image. Không sửa dữ liệu nguồn.

Xem tiến độ ở cửa sổ khác:

```powershell
.\scripts\watch_progress.ps1 -Work outputs/my-run -Watch
# Thêm -Logs để xem chi tiết. Ctrl+C chỉ đóng viewer, không dừng inference.
```

Viewer hiển thị stage, số câu lưu, container và câu/model đang xử lý. Số câu
bao gồm abstain, không phải accuracy. File status cũ có thể vẫn là running khi
máy/Docker tắt; viewer có kiểm tra container trực tiếp.

## 5. Nộp bài

Chỉ khi `outputs/my-run/status.json` có `state: complete`, file cuối nằm tại:

```text
outputs/my-run/public/submission.jsonl
outputs/my-run/public/logs/
```

Kiểm tra format lại nếu cần:

```powershell
.\.venv\Scripts\python.exe -m datamate validate --config configs/public.yaml --predictions outputs/my-run/public/submission.jsonl
```

Upload **submission.jsonl** lên mục DataMate trên cổng Zalo AI Challenge. Vòng
cuối nộp image `datamate-local:calibrated` theo Docker guideline riêng của BTC;
chưa có guideline đó trong repository để xác nhận registry/archive và image nền.
Private dùng đúng solution đã đóng băng. Format hợp lệ không bảo đảm điểm cao.
Xem [hướng dẫn nộp](docs/SUBMIT_VI.md).

## 6. Kiểm thử và cấu trúc

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src/datamate
```

Nếu Temp bị chặn trong sandbox, chọn thư mục tạm mới bằng
`--basetemp outputs/pytest-check` (tạo thư mục cha trước bằng
`New-Item -ItemType Directory -Force outputs` trên PowerShell hoặc `mkdir -p outputs`
trên Linux). Pytest có thể xóa thư mục tạm này; không dùng nơi
chứa kết quả cần giữ. Test dùng fixtures tự tạo, không cần dữ liệu thật/model.

| Thư mục | Nội dung |
| --- | --- |
| `src/datamate/` | Ingest, semantics, agents, execution, verification, calibration, CLI |
| `configs/` | Config synthetic, train/public, model revisions, Docker |
| `scripts/` | Export/audit, evaluation plan, finalization, progress |
| `tests/` | Unit/integration/security, fixtures tổng hợp |
| `docs/` | Contracts, runbook, audit và trạng thái |
| `data/`, `models/`, `outputs/` | Chỉ local, được Git ignore |

Các tên `DataOpsCandidate`, `dataops_dsl.py`, `dsl_coder.md` là định danh code cũ
của PandasCoder, không phải nhánh riêng. Lịch sử ở [WORKLOG.md](WORKLOG.md),
chi tiết ở [QWEN35_RUNBOOK.md](docs/QWEN35_RUNBOOK.md). `CODEX_MASTER_SPEC.md`
đã được bỏ khỏi checkout trước đó; spec lịch sử có ở revision `d1b9c68`,
kiến trúc hiện hành là `architecture.mmd`.

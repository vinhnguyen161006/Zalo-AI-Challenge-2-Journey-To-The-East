# Chạy đánh giá và nộp DataMate

## Quy trình hiện hành: image 4B + 9B

Docker đã chuyển sang `E:\DockerDesktopWSL`; image `datamate-local:qwen35`
đã build và qua 202 test Linux. Chưa có artifact calibration, PA holdout hoặc
file public đầy đủ. Không lấy các kết quả 3B lịch sử phía dưới làm kết quả bản này.

Lượt hiện hành dùng CPU 12 luồng; GPU T1000 nạp được một phần model nhưng
kiểm tra sinh JSON bị timeout 120 giây, nên chưa dùng GPU cho calibration.

```powershell
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization-cpu12 -Cpus 12 -PrepareOnly
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization-cpu12 -Cpus 12
```

Lần đầu chỉ chuẩn bị; lần sau chạy calibration trên train, holdout doanh nghiệp
riêng, đóng gói policy đã hiệu chỉnh và chạy public. Có thể chạy lại đúng lệnh để
resume. Không chạy hai job model đồng thời. Xem tiến độ:

```powershell
Get-Content outputs/docker-finalization-cpu12/status.json
Get-Content outputs/docker-finalization-cpu12/training/status.json
```

Xem tiến độ tự cập nhật mỗi 10 giây, gồm số câu đã lưu, container và câu đang xử lý:

```powershell
.\scripts\watch_progress.ps1 -Watch
```

Thêm `-Logs` để xem log chi tiết; bỏ `-Watch` để xem một lần.
`Ctrl+C` chỉ đóng màn hình theo dõi, không dừng inference. Nếu Docker tắt, lệnh
vẫn hiển thị số câu đã lưu; trạng thái `running` trong file có thể là trạng thái
cũ. Số câu bao gồm cả `abstain`, không phải số câu trả lời đúng. Lệnh chỉ đọc,
không cần khởi động lại workflow đang chạy.

Chỉ khi trạng thái ngoài là `complete`, lấy
`outputs/docker-finalization-cpu12/public/submission.jsonl` để upload lên cổng thi.
Workflow đã chạy kiểm tra format local; cần kiểm tra thông báo của cổng sau upload.
Image lúc đó là `datamate-local:calibrated`. Nếu calibration thiếu bằng chứng,
workflow dừng với `failed` và giữ log; không coi đó là hoàn tất mô hình.

Vòng cuối nộp image theo Docker guideline riêng của BTC. Hiện chưa có guideline
đó trong dự án, nên chưa xác nhận registry/archive hay image nền mà BTC yêu cầu.
Không cần xuất file tar lớn cho đến khi biết hình thức nộp.

## Các lệnh baseline lịch sử

**Bản hai model mới:** xem [QWEN35_RUNBOOK.md](QWEN35_RUNBOOK.md).
Cặp Qwen3.5 4B + 9B đã cài và kiểm tra JSON. Workflow cũ đã dừng do code đổi;
chưa có PA holdout. Các lệnh 3B bên dưới mô tả baseline trước đó và image đã test của baseline.
Để chạy bản mới, chỉ định `-Config configs/qwen35.yaml`; để dùng policy đã hiệu
chỉnh, chỉ định final-config do workflow tạo sau khi đủ bằng chứng.

`predict.ps1` hiện mặc định dùng cặp 4B + 9B qua
`configs/qwen35-evaluation.yaml`. Phần lệnh 3B phía dưới là hướng dẫn lịch sử;
không dùng để lựa chọn model cho image cuối. Quy trình hiện hành và lệnh nộp
với final-config nằm trong QWEN35_RUNBOOK.md.

Pipeline Windows dùng Ollama cục bộ với `qwen2.5-coder:3b-instruct` Q4_K_M.
Planner và PandasCoder dùng chung checkpoint; SQLCoder cũng dùng checkpoint này
trong cấu hình máy 4 GB. Đây là baseline thử nghiệm, chưa phải cặp model 4B/9B
trong kiến trúc mục tiêu. Các câu bị lỗi hoặc không đủ bằng chứng sẽ `abstain`.
Không có gọi API inference bên ngoài. Xem kết quả đo mới nhất trong
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).
Model 3B hiện có dùng giấy phép **qwen-research** theo model card; chưa xác nhận
đủ điều kiện nộp image cuối. Chi tiết model/hash ở [MODEL_PARAMETER_REPORT.md](MODEL_PARAMETER_REPORT.md).

## 1. Kiểm tra môi trường Windows

Mở PowerShell tại thư mục dự án. Ollama và model đã có trên máy này.

```powershell
ollama list
.\.venv\Scripts\python.exe -m datamate check-models --config configs/ollama.yaml
```

Nếu không kết nối được Ollama, chạy `ollama serve` ở một cửa sổ PowerShell khác
rồi chạy lại kiểm tra. Không cần chạy thêm server nếu Ollama đang hoạt động.
Lệnh kiểm tra chỉ xác nhận sinh JSON, không đo độ chính xác.

## 2. Evaluate trên train

```powershell
.\.venv\Scripts\python.exe -m datamate evaluate --config configs/ollama.yaml --labels data/train/answers.jsonl --limit 20 --output outputs/eval/train20.jsonl
```

Kết quả cuối in `correct`, `wrong`, `abstentions`, `coverage` và
`penalized_accuracy = (correct - wrong) / total`. `--limit 20` chấm đúng tập con
20 câu đầu. Đây là phép chấm local theo mô tả đề, chưa được BTC chứng nhận.
Labels chỉ được đọc ở bước chấm sau inference; không đưa vào prompt.
Bỏ `--limit 20` để chạy toàn bộ train. Thêm `--resume` để tiếp tục cùng lần chạy.
Mỗi lần đổi code, prompt hoặc config phải chọn tên output mới.
Log của lệnh ví dụ nằm trong `outputs/eval/train20.logs/`, tách theo tên file
evaluate để không ghi đè log của thí nghiệm khác.

## 3. Tạo file public submission

```powershell
.\predict.ps1 -Split data/public_test -Result outputs/public-final
```

Nếu PowerShell chặn script do execution policy, dùng lệnh tương đương:

```powershell
.\.venv\Scripts\python.exe -m datamate submit --config configs/ollama.yaml --split data/public_test --result outputs/public-final --resume
```

Lệnh đọc rulebook và lịch của chính public split. Không dùng `--limit` cho file
nộp đầy đủ. Output được ghi sau từng câu, có checkpoint để tiếp tục khi ngắt.
Chạy lại đúng lệnh sẽ resume; không chỉnh sửa thủ công file JSONL/checkpoint.
Log mỗi câu nằm ở `outputs/public-final/logs/`, có câu trả lời, thời gian,
chương trình thực thi và lý do từ chối. Cache nằm ngoài dữ liệu nguồn.

```powershell
.\.venv\Scripts\python.exe -m datamate validate --config configs/public.yaml --predictions outputs/public-final/submission.jsonl
```

Chỉ khi báo `LOCAL_FORMAT_PASS` với đủ 1.000 câu của public hiện có, tải file
**`outputs/public-final/submission.jsonl`** lên mục nộp bài của cuộc thi DataMate
trên cổng Zalo AI Challenge. Nộp file JSONL, không nộp thư mục cache/logs hoặc
file checkpoint. Kiểm tra kết quả chấm của cổng; format hợp lệ không có nghĩa
là trả lời đúng. Không có nhãn public để tự báo PA public tại máy.

## 4. Image offline cho vòng cuối

`Dockerfile` cũ là smoke CPU. `Dockerfile.offline` là recipe mới chứa source,
dependencies, Ollama và weights; `predict.sh` tự khởi động server trong container,
đọc `/data`, ghi `/result/submission.jsonl` và `/result/logs/`.
Image `datamate-local:baseline` **đã build và chạy inference CPU offline thành công**.
Ba câu synthetic cho kết quả 1 đúng, 0 sai, 2 abstain; local PA 0,3333, đủ 3 dòng
qua kiểm tra format. Kết quả nằm ở `outputs/docker-fixture-01/`, thời gian xử lý
cộng lại khoảng 302 giây, chưa gồm startup. Đây chưa phải benchmark public.
Docker dùng `configs/ollama-docker.yaml` để pin digest manifest Linux;
Windows tiếp tục dùng `configs/ollama.yaml`. Kết quả giữa hai môi trường đã khác
nhau trên fixture, nên chưa xác nhận tính tái lập theo điều lệ.
Chưa xác nhận image nền theo guideline riêng của BTC hoặc giới hạn 6 giờ trên
2 × RTX 5090. Cần hoàn tất các kiểm tra đó trước khi nộp image cuối.

Checkpoint hiện tại đã được xuất có kiểm tra SHA-256 vào
`models/ollama-export/` (bị Git ignore). Nếu chuẩn bị lại ở máy khác:

```powershell
.\.venv\Scripts\python.exe scripts/export_ollama.py --destination models/ollama-export
```

Exporter chỉ sao chép một model được chỉ định và các blob tham chiếu, không lấy
khóa tài khoản Ollama hay các model khác. Nếu đích đã chứa dữ liệu, dùng đích mới.
Khởi động Docker Desktop với Linux containers, sau đó:

```powershell
docker build -f Dockerfile.offline --build-context model_store=./models/ollama-export -t datamate-local:baseline .
New-Item -ItemType Directory -Force outputs/docker-result
docker run --rm --network none --mount "type=bind,source=$((Resolve-Path data/public_test).Path),target=/data,readonly" --mount "type=bind,source=$((Resolve-Path outputs/docker-result).Path),target=/result" datamate-local:baseline
```

Để thử nhanh lại đúng ba câu synthetic, dùng thư mục kết quả mới:

```powershell
New-Item -ItemType Directory -Force outputs/docker-smoke-next
docker run --rm --network none --env DATAMATE_CONFIG=configs/ollama-docker-fixture.yaml --mount "type=bind,source=$((Resolve-Path tests/fixtures/synthetic_business).Path),target=/data,readonly" --mount "type=bind,source=$((Resolve-Path outputs/docker-smoke-next).Path),target=/result" datamate-local:baseline
.\.venv\Scripts\python.exe -m datamate validate --config configs/ollama-fixture.yaml --predictions outputs/docker-smoke-next/submission.jsonl
.\.venv\Scripts\python.exe -m datamate score --predictions outputs/docker-smoke-next/submission.jsonl --labels tests/fixtures/synthetic_business/labels.jsonl
```

Build có thể dùng mạng để tải dependencies/image nền; inference chạy với
`--network none`. Lệnh trên kiểm thử CPU. Trên host Linux có NVIDIA Container
Toolkit đã cấu hình, thêm `--gpus all` để kiểm thử GPU. Không suy ra tốc độ máy
chấm từ máy Windows 4 GB này. Cấu trúc Ollama trong recipe dựa trên
[Dockerfile chính thức](https://github.com/ollama/ollama/blob/main/Dockerfile).

Sau khi image chạy offline thành công, kiểm tra JSONL bằng cùng lệnh validate,
so sánh PA của hai lần chạy, ghi thời gian toàn bộ, và khai báo model/revision,
giấy phép, tổng tham số trong README. Baseline này không có bước fine-tune.
Nộp image theo cách BTC quy định trong Docker guideline (registry hoặc archive);
không đoán hình thức nộp khi guideline chưa được cung cấp. Nếu BTC yêu cầu tar:

```powershell
docker save -o outputs/datamate-local.tar datamate-local:baseline
```

Vòng private phải dùng đúng solution đã đóng băng, chỉ thay split đầu vào.
Không huấn luyện bằng public/private, không sửa đáp án thủ công hoặc thêm logic
theo question/business ID.

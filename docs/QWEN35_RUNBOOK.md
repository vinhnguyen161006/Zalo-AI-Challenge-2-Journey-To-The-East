# Hệ thống Qwen3.5 4B + 9B

Planner và PandasCoder dùng chung `qwen3.5:4b`; SQLCoder và sửa SQL dùng
`qwen3.5:9b`. Hai checkpoint Q4_K_M đã cài trên máy. Model 3B là baseline lịch sử,
không tham gia inference của `configs/qwen35.yaml`.

Model card chính thức ghi Apache-2.0: [4B](https://huggingface.co/Qwen/Qwen3.5-4B),
[9B](https://huggingface.co/Qwen/Qwen3.5-9B). [Tin phát hành của Qwen](https://github.com/QwenLM/Qwen3.8#news)
ghi ngày 02/03/2026 cho hai model. Audit local đếm 13.949.079.280 phần tử tensor,
kể cả hai projector; đây là phép đếm GGUF, chưa phải chứng nhận eligibility của BTC.
Xem [MODEL_PARAMETER_REPORT.md](MODEL_PARAMETER_REPORT.md).

## Những phần đã có trong runtime

- Nạp CSV/XLSX/SQLite; giữ raw và nguồn gốc; đọc quy ước dd/mm/yyyy từ README,
  năm của sheet khi rulebook cho phép; view hợp nhất các partition tháng đã kiểm tra.
- Planner có bộ lọc, metric, kiểu và nguồn rõ ràng. Một phần luật trạng thái được
  biên dịch khi có trích dẫn và khóa đơn đã kiểm tra.
- FAST có template SQL từ PlanIR; SLOW gọi SQLCoder. AST allowlist và worker
  read-only vẫn kiểm tra mọi SQL, kể cả template.
- PandasCoder độc lập: filter/null/membership, phép tính Decimal, conditional,
  group nhiều metric, JOIN có kiểm tra khóa, pre-aggregate, UNION, sort, date,
  rounding, boolean và danh sách nhiều số. Không sinh/chạy Python tự do.
- Verifier kiểm tra các filter/metric/group/sort trực tiếp đã khai báo, kiểm tra
  multiplicity của JOIN và JOIN qua CTE đã gom nhóm; chạy phản ví dụ có giới hạn.
  Các phạm vi chưa chứng minh được vẫn có cảnh báo; đồng thuận không phải chứng minh.
- `unanswerable` có bằng chứng cho kỳ hoàn toàn ngoài ngày nguồn theo rulebook.
  Thiếu bằng chứng, lỗi, timeout và bất đồng trả `abstain`.
- Calibration dùng cận dưới Wilson từ kết quả chấm thật, tách doanh nghiệp.
  Artifact gắn với code, prompt, model, seed và giới hạn inference; không dùng
  confidence do LLM tự khai báo. Không quyết định đáp án theo business/question ID.

## Chạy và hiệu chỉnh

```powershell
.\.venv\Scripts\python.exe -m datamate check-models --config configs/qwen35-fixture.yaml
.\.venv\Scripts\python.exe scripts/prepare_evaluation.py --config configs/qwen35-evaluation.yaml --destination outputs/development-plan --per-business 20
.\.venv\Scripts\python.exe -m datamate evaluate --config configs/qwen35-evaluation.yaml --questions outputs/development-plan/train.jsonl --labels data/train/answers.jsonl --limit 10 --output outputs/eval/qwen35-development10.jsonl
```

Workflow tự tạo tập theo doanh nghiệp, chạy calibration, fit artifact và đánh giá
holdout. Không đọc labels lúc sinh chương trình; chỉ đọc khi chấm/fit.

```powershell
.\scripts\finish_model.ps1 -Work outputs/final-seed12-32k -QuestionsPerBusiness 20
```

`question-plan/manifest.json` ghi doanh nghiệp và fingerprint dữ liệu. Trong
calibration và holdout, các câu được xen kẽ theo doanh nghiệp. Mỗi tập mặc định
120 câu từ 6 doanh nghiệp khi dùng mặc định 20 câu/doanh nghiệp. Seed 12 đưa
ba doanh nghiệp đã dùng khi sửa lỗi vào development; không dùng tập seed 7 cũ
để công bố calibration/holdout. Job dừng khi code đổi hoặc một pha lỗi. Có thể chạy lại
đúng lệnh để resume nếu code/config/data không đổi. Đổi code thì dùng Work mới.

Cấu hình mặc định của workflow là `configs/qwen35-evaluation.yaml`, context
32K để chứa schema/README lớn cùng chỗ dự phòng output. Để thử
fallback dùng chung 9B cho các vai, truyền
`-Config configs/qwen35-single9b-evaluation.yaml`. Không suy ra fallback tốt hơn
khi chưa có số đo. Trạng thái các pha và lỗi được ghi trong `Work/status.json`.
Lệnh khuyến nghị cho tập mới:

```powershell
.\scripts\finish_model.ps1 -Work outputs/final-seed12-32k -QuestionsPerBusiness 20
Get-Content outputs/final-seed12-32k/status.json
```

Không tạo final-config khi chưa có bin đủ ít nhất 20 đáp án và 3 doanh nghiệp,
với cận dưới độ chính xác lớn hơn 0,5. Nếu chưa đủ, đọc trace để sửa lỗi và chạy
thí nghiệm mới hoặc thu thập thêm câu train; không tự gán confidence.
Khi workflow thành công, dùng `final-config.yaml` do nó tạo để predict/evaluate.

## Public submission

Ví dụ sau dùng policy đã hiệu chỉnh **sau khi workflow thành công**:

```powershell
.\predict.ps1 -Config outputs/final-seed12-32k/final-config.yaml -Split data/public_test -Result outputs/public-qwen35
.\.venv\Scripts\python.exe -m datamate validate --config configs/public.yaml --predictions outputs/public-qwen35/submission.jsonl
```

Nộp `outputs/public-qwen35/submission.jsonl` khi đủ số câu và LOCAL_FORMAT_PASS.
Chạy với `-Config configs/qwen35.yaml` là baseline chưa hiệu chỉnh để nghiên cứu,
không phải kết quả đã đo PA trên holdout. File JSONL hợp lệ không bảo đảm đáp án đúng.

## Đóng gói offline

Docker Desktop đã chuyển kho dữ liệu sang `E:\DockerDesktopWSL` và daemon hoạt
động trở lại. Image hai model đã build thành công; đang bổ sung workflow và
kiểm tra Linux/offline. Chưa có artifact calibration hoặc image hiệu chỉnh cuối.

`models/qwen35-export/` đã chứa đúng hai model và các blob đã kiểm SHA-256,
dùng hardlink để không nhân đôi dung lượng trên cùng ổ. Không sửa blob ở đó.
Chuẩn bị lại bằng thư mục mới:

```powershell
.\.venv\Scripts\python.exe scripts/export_ollama.py --destination models/qwen35-export-new --model qwen3.5:4b --model qwen3.5:9b --hardlink
```

Recipe baseline hai model (chưa chứa artifact calibration):

```powershell
docker build -f Dockerfile.offline --build-context model_store=./models/qwen35-export --build-arg DATAMATE_CONFIG=configs/qwen35-docker.yaml -t datamate-local:qwen35 .
```

Lượt build fallback 9B trước đó đã hết dung lượng trong bước COPY weights và
được dừng. Sau khi chuyển ổ, bản build hai model đã hoàn thành. Image `datamate-local:baseline`
đã test trước đó dùng model 3B và runtime cũ. Không nhầm hai artifact.
`qwen35-docker.yaml` pin digest manifest Linux; Windows giữ digest API Windows.
Phải fit/kiểm tra calibration cho đúng môi trường và signature của image cuối,
đóng gói artifact và config vào image, rồi chạy với network none, input read-only.
Chưa có kiểm chứng GPU 5090, thời gian cả split, evaluator chính thức hoặc tính
tái lập đạt điều lệ. Hướng dẫn mount và upload ở [SUBMIT_VI.md](SUBMIT_VI.md).

Đọc tổng hợp lỗi và thời gian từ log của một lượt đã hoàn thành:

```powershell
.\.venv\Scripts\python.exe scripts/report_run.py --logs outputs/final-seed12-32k/calibration.logs --output outputs/final-seed12-32k/trace-summary.json
```

Báo cáo này không đọc đáp án tham chiếu và không phải số đo accuracy.

## Workflow Linux đến file public

Sau khi image `datamate-local:qwen35` qua kiểm tra offline, dùng workflow sau.
`-PrepareOnly` chỉ kiểm tra mount, chia doanh nghiệp và tạo cấu hình; không đọc
nội dung labels hay chạy model. Chạy lại không có cờ này để inference/calibration.

```powershell
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization -PrepareOnly
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization
```

Máy Windows hiện tại nhận GPU trong Docker, nhưng kiểm tra JSON thực tế trên
T1000 đã timeout 120 giây. Lượt đang chạy chọn CPU:

```powershell
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization-cpu12 -Cpus 12
```

Không chạy thêm lệnh nếu controller hiện hành vẫn đang chạy. Để thử GPU ở
lượt khác sau khi xử lý lỗi timeout, chọn
thư mục mới và giữ nguyên tham số khi resume:

```powershell
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization-gpu -Cpus 12 -UseGpu -PrepareOnly
.\scripts\finish_submission.ps1 -Work outputs/docker-finalization-gpu -Cpus 12 -UseGpu
```

`-UseGpu` thêm `--gpus all` cho cả calibration/holdout và public. Tùy chọn này
được lưu trong `request.json`; không đổi thiết bị giữa chừng trong cùng Work.
GPU local T1000 không chứng minh tốc độ hoặc tính tái lập trên máy RTX 5090.

Workflow giữ một tag image cố định theo image ID, dùng đúng image đó cho
calibration/holdout, chỉ mount train khi hiệu chỉnh. Các container chạy network
none; dữ liệu nguồn mount read-only. Khi đủ bằng chứng, `Dockerfile.calibrated`
chỉ thêm artifact và cấu hình triển khai, kiểm tra signature ngay lúc build.
Public chỉ được mount cho inference sau bước này. Không có fine-tuning weights.

Kết quả thành công: `outputs/docker-finalization/public/submission.jsonl`,
image `datamate-local:calibrated`, log từng câu trong thư mục `public/logs/`.
Trạng thái ngoài: `outputs/docker-finalization/status.json`; trạng thái train:
`outputs/docker-finalization/training/status.json`. Các container được giữ để
đọc log và resume. Dùng Work mới khi thay model/code/dataset hoặc tham số tập.

Nếu calibration không đủ số mẫu/doanh nghiệp hoặc cận dưới không vượt 0,5,
workflow dừng; không tự bật policy chưa có bằng chứng. Không chạy đồng thời
job inference Windows và Docker trên máy RAM/GPU nhỏ. Lượt native Windows cũ
đã được dừng và giữ nguyên các kết quả đã ghi; chưa có PA holdout từ lượt đó.
Kết quả local không thay thế kiểm tra evaluator, GPU và thời gian của BTC.

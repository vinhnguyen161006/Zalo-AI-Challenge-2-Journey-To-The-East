# WORKLOG — DataMate 2026 (AEGDA)

Danh sách các task cần làm để đi từ code base hiện tại tới bài nộp DataMate. Các task được
xếp theo **đúng luồng dữ liệu của [architecture.mmd](architecture.mmd)**: từ INPUT, qua
harness tất định, model, hai đường FAST/SLOW, bộ kiểm chứng, quyết định, tới file nộp. Mọi
lỗi, timeout hoặc hết ngân sách đi nhánh `FAIL`: abstain kèm mã lý do, batch chạy tiếp.

Tài liệu liên quan: [CLAUDE.md](CLAUDE.md) (luật viết code, ràng buộc cuộc thi) ·
[CODEX_MASTER_SPEC.md](CODEX_MASTER_SPEC.md) (đặc tả kỹ thuật) · [docs/](docs/) ·
[README.md](README.md) · rulebook: [train](data/train/rulebook.md),
[public test](data/public_test/rulebook.md)

Cập nhật lần cuối: 2026-11-05 (theo sơ đồ mới: nhánh pandas `DSL4`, node `TIME`, `FAIL`,
`MODELS`).

## 0. Hiện trạng

Repo đã có khung chạy được từ đầu đến cuối: nạp CSV/XLSX/SQLite vào DuckDB, catalog có
nguồn gốc dữ liệu, chặn SQL nguy hiểm bằng AST, chạy SQL trong process riêng, ghi JSONL có
resume, CLI và 98 test pass (ở commit `ed147ec`). Cả 30 doanh nghiệp train nạp được.

Tuy vậy, **hệ thống chưa trả lời được câu nào.** `predict` cố ý ghi `abstain` cho mọi câu,
model chưa được nối, và audit ngày 04/11 tìm ra các lỗi chặn đường:

- `datamate evaluate` không đọc được `answers.jsonl` thật vì nhãn có thêm khóa `alias`.
- 588 cột bị coi là "số mơ hồ" và 245 cột "ngày mơ hồ" nên giữ dạng chữ. Ví dụ cột
  `gia_ban`, `thanh_tien` của biz_101 ghi `"560,000"` nên không cộng được.
- Một câu SQL thiếu dấu nháy làm sập cả batch.
- Context gửi model chứa số thứ tự của mọi dòng nguồn, nên với doanh nghiệp lớn thì vượt
  giới hạn.
- Bộ hiểu thời gian nhầm "Tuấn" thành "tuần", "tủ lạnh" thành "từ".
- Chưa có thư viện tính chỉ số nghiệp vụ (doanh thu, công nợ, tồn kho…) theo rulebook.

**Thay đổi đang dở trong working tree (chưa commit, chưa chạy quality gate):** parser số và
ngày theo cột, bắt lỗi `SqlglotError` và mở rộng allowlist SQL, bỏ `source_rows` khỏi context,
đưa ứng viên thực thể vào context, chuyển giới hạn vào config. `evidence_context` đã đổi chữ ký
(thêm `entities`) nhưng `pipeline.py` chưa đổi lời gọi. Các task liên quan ghi `IN_PROGRESS`.

Dữ liệu đang có:

| Bộ | Vị trí | Nội dung |
| --- | --- | --- |
| Train | `data/train/` | 3.000 câu, 30 doanh nghiệp, có `answers.jsonl` |
| Public test | `data/public_test/` | 1.000 câu, 14 doanh nghiệp `dev_001`–`dev_014`, không có đáp án |

Phân bố loại đáp án trong train: number 1.884, percent 578, list 193, date 145, string 88,
unanswerable 70, boolean 23, table 19. **Rulebook public test khác train**: thêm luật ca tiền
mặt, khách trung thành, giờ cao điểm, nhà cung cấp chính và ba ngành mới (Mẹ và bé, Sửa xe,
Điện thoại). Lịch âm hai bộ giống nhau.

Mốc thời gian: nộp giải pháp **16/11/2026 23:59**, private test 20/11, kết quả cuối 23/11.
Chưa có GPU và chưa có bộ kit chính thức (validator, hướng dẫn nộp, luật đếm tham số).

## 1. Nguyên tắc

- **Thứ tự task đi theo luồng của sơ đồ.** Cột "Cần xong trước" ở §2 là ràng buộc thật;
  task không phụ thuộc nhau thì làm song song. Một task chỉ coi là xong khi đạt mục "Xong khi",
  kiểm chứng bằng lệnh chạy được, không phải bằng cảm giác.
- **Ba thứ không được đảo thứ tự:**
  1. Bộ chấm và chia split theo doanh nghiệp (Task 2) phải xong trước mọi thay đổi muốn đo
     điểm. Danh sách doanh nghiệp holdout được chốt ở Task 2 và không đổi.
  2. Primitive nghiệp vụ lõi (Task 7, node RULE) phải xong trước template FAST (Task 11).
     Model chỉ chọn phép tính và tham số; công thức doanh thu, đơn hợp lệ… nằm trong code.
  3. Holdout chỉ được chấm một lần (cuối Task 15), sau khi đã đóng băng template, prompt và
     ngưỡng trả lời. `predict` chỉ được bỏ chế độ "luôn abstain" sau bước đó.
- **Không dùng đáp án train làm bảng tra.** Nhãn chỉ dùng để chấm điểm và hiệu chỉnh.
- **Mỗi task (hoặc một phần task) làm trên một nhánh riêng** khi commit, không commit thẳng
  vào `main`. Không commit `data/`, `models/`, `outputs/`.
- **Một thay đổi xong khi** đạt quality gate ở §4 của [CLAUDE.md](CLAUDE.md): `pytest`,
  `ruff check`, `ruff format --check`, `mypy` sạch; có test cho hành vi mới; không comment `#`;
  không tiếng Việt trong code (trừ dữ liệu và từ khóa cần khớp); không magic number.
- **Viết hàm nào thì viết test của hàm đó cùng lúc**, trên fixture synthetic trong
  `tests/fixtures/`. Đến cuối Task 17, ma trận test T01–T26 của spec §16 phải đủ.
- **Xong bước nào thì nối bước đó vào lệnh chung**: thêm target vào `Makefile` để
  `make all` lớn dần theo từng task.
- **Trạng thái** dùng nhãn của CLAUDE.md: `NOT_STARTED`, `IN_PROGRESS`, `DONE_TESTED`,
  `BLOCKED_BY_GPU`, `BLOCKED_BY_OFFICIAL_KIT`. Cập nhật `docs/IMPLEMENTATION_STATUS.md` khi
  xong mỗi task.

### Giải thích vài thuật ngữ dùng trong file này

| Thuật ngữ | Nghĩa |
| --- | --- |
| abstain | Bỏ trống câu hỏi. Được 0 điểm. Dùng khi hệ thống không đủ chắc. |
| unanswerable | Khẳng định "dữ liệu không đủ để trả lời". Đây là một đáp án, có thể đúng (+1) hoặc sai (−1), khác với abstain. |
| harness | Toàn bộ phần code tất định, không dùng model, chạy offline: nạp dữ liệu, typed views, catalog, rulebook, time engine và lịch. |
| rulebook | File luật nghiệp vụ của từng bộ dữ liệu: doanh thu tính thế nào, tuần bắt đầu thứ mấy, đáp án ghi ra sao. Train và public test có rulebook khác nhau. |
| primitive nghiệp vụ | Một hàm code đã kiểm chứng, sinh SQL cho một khái niệm của rulebook, ví dụ "đơn hợp lệ" hoặc "nợ của khách tại ngày X". |
| PlanIR | Bản kế hoạch dạng JSON mà model 4B viết ra từ câu hỏi: cần bảng nào, cột nào, kỳ nào, loại đáp án gì. |
| FAST / SLOW | Hai đường xử lý. FAST: câu rõ ràng, dùng template hoặc 1 SQL. SLOW: câu khó, sinh nhiều SQL và một lời giải pandas độc lập để đối chiếu. Router chọn đường, kèm mã lý do và ngân sách. |
| lời giải pandas (`DSL4`) | Lời giải độc lập: model 4B viết danh sách thao tác pandas dạng JSON có kiểu (filter / aggregate / group / sort / select). Code của dự án (`execution/dataops_dsl.py`) đọc JSON đó và gọi pandas. Không chạy Python do model sinh, không `eval` / `exec`. Nó không được thấy SQL, PlanIR hay kết quả. |
| nhánh `FAIL` | Kế hoạch không hợp lệ, hết ngân sách, timeout hoặc lỗi chạy thì ra abstain kèm mã lý do, không bao giờ ra `unanswerable`. Batch chạy tiếp. |
| fanout | JOIN làm nhân bản dòng. Ví dụ một đơn có ba lần thanh toán, JOIN thẳng thì doanh thu đơn đó bị cộng ba lần. |
| split theo doanh nghiệp | Chia 30 doanh nghiệp train thành ba nhóm: phát triển, hiệu chỉnh, holdout. Câu của cùng một doanh nghiệp không bao giờ nằm ở hai nhóm. |
| holdout | Nhóm doanh nghiệp cất riêng, chỉ chấm một lần ở cuối. Đây là con số điểm đáng tin duy nhất. |
| calibration (hiệu chỉnh) | Biến các tín hiệu của hệ thống thành xác suất đúng thật. Vì sai bị −1, chỉ trả lời khi xác suất đúng > 0,5. |

## 2. Luồng hệ thống và 17 task

### Ánh xạ sơ đồ → task

| Phần | Node trong `architecture.mmd` | Task | Module chính |
| --- | --- | --- | --- |
| 0. Nền | `IN` (INPUT) | 1 | `adapters/official_kit.py`, `configs/` |
| 0. Nền | (ngoài sơ đồ: đo điểm) | 2 | `eval/scorer.py` |
| 1. Harness | `IG` Immutable raw ingest | 3 | `data/ingest.py`, `data/normalization.py`, `data/excel_tables.py` |
| 1. Harness | `DB` DuckDB typed views | 4 | `data/semantic_views.py`, `semantics/units.py` |
| 1. Harness | `CAT` Schema catalog + join graph + grounding | 5 | `data/column_roles.py`, `data/join_graph.py`, `data/grounder.py` |
| 1. Harness | `RULE` Rulebook resolver, `TIME` Time and calendar engine | 6 | `semantics/rulebook.py`, `semantics/time_engine.py` |
| 1. Harness | `RULE` định nghĩa nghiệp vụ có rule id + source span | 7, 8 | `semantics/business_ops.py` |
| 2. Hiểu câu hỏi | `MODELS` (hạ tầng cho `P4`, `SQL9`, `DSL4`) | 9 | `configs/models.yaml`, `llm/client.py` |
| 2. Hiểu câu hỏi | `P4` Planner 4B, `GUARD` Plan validator | 10 | `llm/agents.py`, `prompts/planner.md` |
| 3. Thực thi | `ROUTER`, `FSQL`, `FEXEC` | 11 | `routing/router.py`, `execution/templates.py`, `execution/sql_policy.py` |
| 3. Thực thi | `SQL9`, `DSL4` Pandas coder, `EXEC` | 12 | `pipeline.py`, `execution/dataops_dsl.py`, `execution/worker.py` |
| 4. Kiểm chứng | `CHECK` Evidence-aware verifier | 13 | `verification/invariants.py`, `verification/sql_facts.py` |
| 4. Kiểm chứng | `DIS`, `DPC` Differential verification | 14 | `verification/differential.py` |
| 4. Kiểm chứng | `DEC` Decision policy, `FAIL` lỗi / timeout / hết ngân sách | 15 | `decision/answerability.py`, `decision/calibration.py`, `decision/policy.py` |
| 5. Đầu ra | `OUT` Formatter + writer | 16 | `adapters/submission_writer.py`, `verification/canonicalize.py` |
| 5. Đầu ra | (ngoài sơ đồ: chạy thật, nộp) | 17 | `Dockerfile.gpu`, `docs/OFFICIAL_RULE_AUDIT.md` |

### Tổng quan

| # | Node | Task | Cần xong trước | Lệnh chạy | Trạng thái |
| --- | --- | --- | --- | --- | --- |
| 1 | `IN` | Môi trường và input từng bộ dữ liệu | — | `make install`, `make check` | IN_PROGRESS |
| 2 | — | Bộ chấm cục bộ và chia split | 1 | `make eval-train` | NOT_STARTED |
| 3 | `IG` | Ingest & normalize: đọc đúng số và ngày | 1 | `make inspect-all`, `make inspect-public` | IN_PROGRESS |
| 4 | `DB` | Typed views ngữ nghĩa và quy đổi đơn vị | 3 | `make inspect-all` | NOT_STARTED |
| 5 | `CAT` | Catalog: vai trò cột, join graph, grounding | 4 | `make inspect-all` | IN_PROGRESS |
| 6 | `RULE`, `TIME` | Rulebook theo bộ và time engine | 1 | `make check` | IN_PROGRESS |
| 7 | `RULE` | Primitive nghiệp vụ lõi: doanh thu, xếp hạng | 5, 6 | `make check` | NOT_STARTED |
| 8 | `RULE` | Primitive nghiệp vụ mở rộng: nợ, kho, ngành | 7 | `make check` | NOT_STARTED |
| 9 | `MODELS` | Dựng GPU, model và audit tham số | — | `datamate audit-models` | BLOCKED_BY_GPU |
| 10 | `P4`, `GUARD` | Planner 4B và kiểm tra kế hoạch | 5, 6, 9 | `make eval-train` | IN_PROGRESS |
| 11 | `ROUTER`, `FSQL`, `FEXEC` | Router và đường FAST | **7**, 10 | `make eval-train` | IN_PROGRESS |
| 12 | `SQL9`, `DSL4`, `EXEC` | Đường SLOW: nhiều SQL, lời giải pandas độc lập, sửa lỗi | 10, 11 | `make eval-train` | IN_PROGRESS |
| 13 | `CHECK` | Bộ kiểm chứng dựa trên bằng chứng | 11, 12 | `make check` | NOT_STARTED |
| 14 | `DIS`, `DPC` | Phát hiện bất đồng và kiểm chứng sai khác | 13 | `make eval-train` | NOT_STARTED |
| 15 | `DEC`, `FAIL` | Quyết định: trả lời / unanswerable / abstain | 2, 13, 14 | `make eval-holdout` | NOT_STARTED |
| 16 | `OUT` | Formatter đáp án theo §9 | 2 | `make check` | NOT_STARTED |
| 17 | — | Benchmark, Docker, nộp bài | 15, 16 | `make predict-public`, `make all` | BLOCKED_BY_OFFICIAL_KIT |

Làm song song được:
- Task 9 (GPU) với Task 2–8. Task 9 cần bạn cấp máy có GPU.
- Task 3 và Task 6 với nhau ngay sau Task 1.
- Task 16 (formatter) ngay sau Task 2. Nó đứng cuối luồng nhưng không phụ thuộc model, và
  nên xong sớm để đo điểm các task giữa.
- Task 11–14 viết và test được bằng fake model trước khi Task 9 có GPU.
- Bước audit luật chính thức của Task 17 làm ngay từ đầu. Việc này cần bạn đăng nhập portal.

Lịch gợi ý theo deadline 16/11:

| Ngày | Phần | Task |
| --- | --- | --- |
| 04–06/11 | Nền + đầu harness | 1, 2, 3, 6 |
| 06–10/11 | Harness + đầu ra | 4, 5, 7, 8, 16 (song song: 9) |
| 10–13/11 | Model, thực thi, kiểm chứng | 10, 11, 12, 13, 14, 15 |
| 13–16/11 | Nộp | 17 |

## 3. Chi tiết từng task

## Phần 0 — Nền

---

### Task 1 — Môi trường và input từng bộ dữ liệu · node `IN`

**Lệnh:** `make install`, `make check`

**Mục đích.** Node INPUT của sơ đồ gồm gói dữ liệu, README, rulebook, lịch và câu hỏi. Ai clone
repo về cũng phải cài được đúng phiên bản và chạy được trên cả hai bộ train và public test bằng
một lệnh. Hiện chỉ có config cho train và fixture; public test chưa chạy được. Test e2e lại ghi
thẳng vào thư mục `outputs/` thật, nên lần chạy đầu trên máy mới có lúc lỗi `FileNotFoundError`
rồi lần sau lại pass.

**Cần làm**
1. Tạo `configs/public_test.yaml` (kế thừa `default.yaml`) trỏ tới `data/public_test/`.
2. Thêm target `check` (lint + format + typecheck + test), `inspect-public`, `eval-train`
   vào `Makefile`; target chưa có lệnh thì thêm ở task tương ứng.
3. Cho test e2e dùng thư mục output tạm thay vì `outputs/` thật.
4. Cập nhật README: dữ liệu nằm ở `data/train`, `data/public_test`; cách giải nén zip.

**File cần code**

| File | Viết gì |
| --- | --- |
| `configs/public_test.yaml` | **tạo mới**: đường dẫn data, rulebook, calendar, questions của public test |
| `Makefile` | target `check`, `inspect-public` |
| `tests/e2e/test_cli.py` | dùng `output_root` tạm |
| `README.md` | đường dẫn `data/`, lệnh cho public test |

**Xong khi**
- `make check` sạch trên máy mới, chạy hai lần liền không lỗi.
- `datamate preflight --config configs/public_test.yaml` báo 1.000 câu, 14 doanh nghiệp.
- `datamate inspect --config configs/public_test.yaml --all-businesses` nạp 14/14.

---

### Task 2 — Bộ chấm cục bộ và chia split · ngoài sơ đồ

**Lệnh:** `make eval-train`

**Mục đích.** Không đo được điểm thì không biết sửa gì có ích. Hiện `datamate evaluate` dừng
ngay ở dòng nhãn đầu tiên với lỗi `"Invalid output keys"`, vì cả 3.000 nhãn có khóa `alias`
(các cách viết khác được chấp nhận của cùng đáp án). Task này viết bộ chấm theo đúng §9 của
rulebook và chốt cách chia doanh nghiệp cho mọi thử nghiệm sau.

**Cần làm**
1. Viết hàm đọc nhãn riêng, chấp nhận `alias` và `reason`.
2. So đáp án theo từng loại, đúng §9:
   - number, percent: khớp tuyệt đối, chỉ bỏ qua sai số dấu phẩy động;
   - string: không phân biệt hoa thường, khoảng trắng, dấu câu; chấp nhận mọi `alias`;
   - list: đúng cả tập và thứ tự;
   - table: so cột theo tên, lệch tên thì theo vị trí; thứ tự dòng không quan trọng;
   - date: đúng chuỗi `YYYY-MM-DD` hoặc `YYYY-MM`;
   - unanswerable: chỉ cần đúng type.
3. Báo cáo điểm `+1 / −1 / 0`, số đúng, sai, abstain, coverage, chia theo loại đáp án.
4. Chia 30 doanh nghiệp train thành phát triển / hiệu chỉnh / holdout bằng `split_businesses`
   (seed cố định); ghi danh sách id ra file. Thêm tùy chọn chạy `predict`/`evaluate` trên một nhóm.
5. Chạy baseline E0 (toàn abstain, điểm 0) và ghi vào `docs/EXPERIMENT_LOG.md`.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/eval/scorer.py` | đọc nhãn, so theo loại, báo cáo; split theo doanh nghiệp |
| `src/datamate/cli.py` | tùy chọn `--businesses` / `--split` cho `predict`, `evaluate` |
| `experiments/splits/seed7.json` | **tạo mới**: chỉ chứa business id của từng nhóm |
| `Makefile` | target `eval-train`, `eval-holdout` |
| `tests/unit/test_scorer.py` | **tạo mới**: mỗi loại đáp án một test, thêm test `alias`, test table đảo dòng |

**Xong khi**
- `datamate evaluate --config configs/dev.yaml --labels data/train/answers.jsonl` chạy hết
  3.000 câu không lỗi.
- Output ghi rõ `LOCAL_RULEBOOK_SCORER`, không nhận là evaluator chính thức.
- File split có ba nhóm không trùng doanh nghiệp; `EXPERIMENT_LOG.md` có dòng E0.

## Phần 1 — Harness tất định (0 tham số model)

---

### Task 3 — Ingest & normalize: đọc đúng số và ngày · node `IG`

**Lệnh:** `make inspect-all`, `make inspect-public`

**Mục đích.** Nạp dữ liệu sai thì mọi phép tính sau đều sai. Ở commit hiện tại, parser xét từng
ô riêng lẻ:
- `"560,000"` có thể là 560 nghìn hoặc 560 phẩy, nên bị coi là mơ hồ, dù cả cột đều cách
  nhau đúng 3 chữ số.
- `"1,200,000"` bị coi là mơ hồ trong khi `"1.200.000"` thì đọc được.
- Số thực có sẵn trong Excel như `0.125` cũng bị coi là mơ hồ.
- Ngày `03/04/2026` bị coi là mơ hồ dù cùng cột có `25/04/2026` chứng minh ngày đứng trước.

Kết quả là 588 cột số và 245 cột ngày bị để dạng chữ. Bộ kiểm tra coi mọi bảng có cột
mơ hồ là lỗi cứng, nên các bảng bán hàng chính bị loại. Bản gốc (raw) và nguồn gốc từng dòng
phải được giữ nguyên. Node `IG` của sơ đồ là "immutable raw ingest": giữ SHA-256 và nguồn gốc,
mở SQLite với `mode=ro`, kiểm tra đường dẫn không thoát khỏi thư mục cho phép.

**Cần làm**
1. Giá trị số gốc từ Excel/SQLite (`int`, `float`) chuyển thẳng sang `Decimal`.
2. Chấp nhận dấu phẩy phân cách nghìn giống dấu chấm.
3. Suy kiểu phân cách theo **cả cột**: chỉ cần một ô chứng minh được kiểu thì áp cho cả cột;
   trong cột có mâu thuẫn thì giữ cảnh báo. *(đang dở trong working tree: `number_style`)*
4. Suy thứ tự ngày/tháng theo cả cột *(đang dở: `date_day_first`)*; đọc ngày có giờ
   (`dd/mm/yyyy HH:MM`), giữ giờ để tính ca và giờ cao điểm.
5. Ngày không ghi năm thì lấy năm của sheet hoặc tên file (rulebook §8), ví dụ `BanHang_T5_2026.xlsx`.
6. Tăng `PARSER_VERSION` để cache cũ tự hết hiệu lực.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/data/normalization.py` | số gốc, dấu phẩy nghìn, kiểu số theo cột, ngày có giờ |
| `src/datamate/data/ingest.py` | `_infer` suy theo cột, năm từ tên sheet/file, `PARSER_VERSION` |
| `docs/DATA_INGESTION_REPORT.md` | số cột cảnh báo trước và sau, cho cả train và public test |
| `tests/unit/test_normalization.py` | `0.125`; `"1,200,000"`; cột `"560,000"`; cột mâu thuẫn; cột ngày day-first; ngày không năm |

**Xong khi**
- Train vẫn nạp 30/30, public test 14/14.
- Cột `gia_ban`, `thanh_tien` của biz_101 có kiểu `DECIMAL`.
- Số cột `ambiguous_numeric` và `ambiguous_date` giảm rõ, có số liệu trong báo cáo; cột nào
  còn cảnh báo thì có lý do.

---

### Task 4 — Typed views ngữ nghĩa và quy đổi đơn vị · node `DB`

**Lệnh:** `make inspect-all`

**Mục đích.** Node DB là "DuckDB typed views": một lớp bảng sạch, cùng quy ước cho mọi shop.
Giữ riêng 0 / NULL / thiếu, tiền dùng `Decimal`, cache theo fingerprint của từng doanh nghiệp.
File xuất từ phần mềm bán hàng không dùng thẳng được. Rulebook §8 quy định:
- Ô trống ở các dòng sau của cùng một đơn mang giá trị của dòng đầu (khách, trạng thái…).
- Hai dòng giống hệt nhau trong cùng một đơn là lỗi xuất file, chỉ tính một lần.
- Shop không có cột trạng thái thì đọc trạng thái từ cột Ghi chú.

Ngoài ra hàng có đơn vị lớn và nhỏ (thùng, chai…), và "bán chạy" phải tính theo đơn vị cơ bản.

**Cần làm**
1. Tạo view ngữ nghĩa `sem__*` cho từng bảng:
   - điền ô trống theo nhóm đơn (chỉ khi xác định được cột mã đơn);
   - bỏ dòng trùng trong cùng đơn;
   - lấy trạng thái từ ghi chú khi cần;
   - loại dòng tổng cộng.

   Bảng `raw__*` giữ nguyên.
2. Ghi số dòng trước và sau mỗi bước vào catalog.
3. Đọc bảng quy đổi đơn vị (ví dụ `DanhSachQuyDoi.xlsx`) và README; đơn vị không có dòng quy
   đổi là đơn vị cơ bản, hệ số 1. Tạo cột số lượng quy về đơn vị cơ bản.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/data/semantic_views.py` | **tạo mới**: điền nhóm, bỏ trùng, trạng thái từ ghi chú, loại dòng tổng |
| `src/datamate/semantics/units.py` | **tạo mới**: bảng quy đổi, số lượng theo đơn vị cơ bản |
| `src/datamate/data/ingest.py` | tạo view `sem__*` sau khi nạp |
| `src/datamate/types.py` | `Catalog` thêm số dòng mỗi bước, bảng quy đổi |
| `tests/integration/test_ingestion.py` | nhóm đơn, dòng trùng, trạng thái trong ghi chú, dòng tổng, hàng nhiều cấp đơn vị |

**Xong khi**
- Mỗi doanh nghiệp có view `sem__*` trong snapshot, bảng `raw__*` không đổi.
- Số dòng trước/sau mỗi bước có trong catalog.

---

### Task 5 — Catalog: vai trò cột, join graph, grounding · node `CAT`

**Lệnh:** `make inspect-all`

**Mục đích.** Node CAT gồm schema catalog, join graph và value grounding, có kiểm tra khóa và
cardinality. Mỗi shop đặt tên cột khác nhau. Model và primitive cần biết cột nào là mã đơn, ngày
đặt, thành tiền…; bảng nào nối với bảng nào và quan hệ 1-n hay n-n; tên hàng "ca phe sua" trong
câu hỏi ứng với giá trị nào trong dữ liệu.

**Cần làm**
1. Gán **vai trò cột** cho từng doanh nghiệp:
   - đơn hàng: mã đơn, ngày đặt, giờ, trạng thái, hình thức thanh toán, nhân viên;
   - khách: tên khách, mã khách, nhóm khách;
   - dòng hàng: mặt hàng, mã hàng, nhóm hàng, số lượng, đơn vị, đơn giá, thành tiền dòng;
   - tiền: giảm giá dòng/đơn, phí giao, VAT;
   - kho: nhà cung cấp, lô, hạn dùng.

   Vai trò không chắc thì để `unknown` kèm ứng viên.
2. Nhận diện ngành của shop từ README (cà phê, nhà thuốc, vật liệu xây dựng, mẹ và bé, sửa xe,
   điện thoại…).
3. Dựng join graph: cột khóa chung giữa các bảng, kiểm tra tính duy nhất và cardinality
   (1-1, 1-n, n-n) bằng truy vấn đếm thật. Ghi lại để bộ kiểm chứng (Task 13) dùng phát hiện fanout.
4. Grounding thực thể: giới hạn đọc từ config, bỏ giá trị quá ngắn, đánh dấu mơ hồ khi top 2
   gần nhau *(đang dở trong working tree)*. Nối kết quả vào context gửi model.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/data/column_roles.py` | **tạo mới**: gán vai trò cột, nhận diện ngành |
| `src/datamate/data/join_graph.py` | **tạo mới**: khóa chung, kiểm tra unique và cardinality |
| `src/datamate/data/grounder.py` | giới hạn từ config, lọc giá trị ngắn |
| `src/datamate/types.py` | `Catalog` thêm vai trò cột, ngành, join graph |
| `tests/integration/test_ingestion.py` | vai trò cột trên fixture; khóa 1-n bị phát hiện đúng; thực thể mơ hồ |

**Xong khi**
- `outputs/column_roles_report.json` có đủ 44 doanh nghiệp (30 train + 14 public).
- Mọi sổ bán hàng có đủ vai trò bắt buộc (mã đơn, ngày, trạng thái, số lượng, thành tiền), hoặc
  ghi rõ thiếu vai trò nào.
- Join graph của mỗi doanh nghiệp ghi được cardinality của từng cạnh.

---

### Task 6 — Rulebook theo bộ và time engine · node `RULE` (1/3), `TIME`

**Lệnh:** `make check`

**Mục đích.** Sơ đồ tách hai node, cả hai lấy thẳng từ INPUT. `RULE` là bộ giải rulebook: mỗi
định nghĩa trích dẫn rule id và dòng nguồn; không có luật thì gắn cờ rủi ro, không tự nghĩ công
thức. `TIME` là time engine: khoảng nửa mở, tuần bắt đầu thứ Hai, tính đến `as_of_date`, ngày âm
chỉ lấy từ `calendar.csv` của bộ đang chạy. Câu hỏi nào cũng gắn với một khoảng thời gian, và
hiểu sai kỳ là sai đáp án. Bộ hiểu thời gian hiện
mới xử lý một phần §2 và tìm từ khóa sau khi bỏ dấu:
- "Khách **Tuấn** mua bao nhiêu đơn tháng này" bị hiểu là "tuần" và bỏ qua "tháng này".
- "**Tủ** lạnh…", "**Đèn** LED…" bị đánh dấu mơ hồ.

Rulebook hiện bị tách theo từng dòng văn bản, trong khi public test có luật khác train.

**Cần làm**
1. Đọc bảng markdown của rulebook thành luật `{mục, ngành, thuật ngữ, định nghĩa, dòng nguồn}`;
   tìm luật theo thuật ngữ có trong câu hỏi; luôn nạp rulebook của bộ đang chạy.
2. Tìm từ khóa thời gian trên chuỗi **còn dấu** (`tuần`, `tết`, `từ`, `đến`, `năm`); chỉ dùng
   bản không dấu khi cả câu không có dấu (ví dụ "Rieng ngay hom nay").
3. Hỗ trợ đủ các cách nói, mọi khoảng đều dạng `[start, end)`:
   - ngày X, hôm nay, hôm qua;
   - "Tháng M/Y", "Tháng M năm Y";
   - tháng M hoặc quý Q không ghi năm (năm nay, nếu sau hôm nay thì năm trước);
   - từ X đến Y (gồm cả hai đầu); N ngày gần nhất; từ đầu năm;
   - tuần này, tuần trước.
4. Hỗ trợ các cách nói cần hai khoảng hoặc mốc trạng thái:
   - tháng này so với tháng trước (cùng số ngày);
   - % tăng trưởng so với kỳ trước;
   - Tết năm Y và "so với trước Tết", tra `calendar.csv`;
   - "đến hết ngày X / tính đến hôm nay" (trạng thái từ đầu dữ liệu);
   - ca sáng 06:00–13:59, ca chiều 14:00–21:59;
   - trung bình mỗi ngày thứ X (public).
5. Báo các luật của bộ đang chạy mà chưa có primitive (`not_compiled`).

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/semantics/rulebook.py` | đọc bảng luật, tìm theo thuật ngữ, báo luật chưa hỗ trợ |
| `src/datamate/semantics/time_engine.py` | khớp từ khóa có dấu, đủ bảng §2, kỳ so sánh, ca, cutoff; đọc lịch một lần |
| `src/datamate/types.py` | `RuleEvidence` có cấu trúc; `TimeResolution` thêm kỳ so sánh và cutoff |
| `tests/unit/test_config_time.py` | mỗi dòng bảng §2 ít nhất một test; "Tuấn", "tủ lạnh", "bánh tét"; năm nhuận; tháng trước thiếu ngày 31; Tết vắt năm |
| `tests/unit/test_rulebook.py` | **tạo mới**: đọc cả hai rulebook, đếm luật theo mục và ngành |

**Xong khi**
- Các câu mẫu public test ("Tháng 11 năm 2025…", "Tháng 5/2026…", "Rieng ngay hom nay…") ra
  đúng khoảng.
- Có danh sách luật public test chưa có primitive, dùng làm đầu vào cho Task 8.

---

### Task 7 — Primitive nghiệp vụ lõi: doanh thu, xếp hạng · node `RULE` (2/3)

**Lệnh:** `make check`

**Mục đích.** Node RULE ghi "each definition cites rule id + source span; no rule: flag risk,
never invent a formula". Nghĩa là mỗi định nghĩa phải thành code có nguồn, không để model tự nghĩ. Hơn 80% đáp án là số và phần trăm, phần lớn xoay
quanh doanh thu, số đơn, bán chạy. Các định nghĩa rất dễ sai, ví dụ:
- đơn có trả hàng **dù chỉ một phần** bị bỏ cả đơn;
- doanh thu **không** trừ giảm giá trên cả đơn, không cộng VAT, không gồm phí giao;
- bán chạy tính theo **số lượng quy về đơn vị cơ bản**, không theo số tiền.

**Cần làm**
1. Đơn hàng và doanh thu (§1):
   - đơn hợp lệ; doanh thu một đơn và của kỳ; số đơn; khách đã mua;
   - doanh thu theo hình thức thanh toán: COD là hình thức riêng; đơn ghi nợ không tính vào tiền mặt;
   - doanh thu theo nhóm khách: khách vãng lai không thuộc nhóm nào, mẫu số là cả shop;
   - tỉ lệ đơn huỷ: mẫu số là mọi đơn, làm tròn 2 chữ số;
   - % tăng trưởng và chênh lệch so với kỳ trước.
2. Xếp hạng và khách (§3):
   - bán chạy; bỏ "Phí phục vụ" khỏi xếp hạng nhưng vẫn tính vào doanh thu;
   - xác định khách theo mã, hoặc theo tên chỉ khác cách viết; khách lẻ không tính;
   - nhân viên bán giỏi nhất theo doanh thu;
   - giá trị trung bình đơn, làm tròn đồng, nửa đồng làm tròn lên;
   - bằng điểm thì xếp theo mã tăng dần.
3. Luật xếp hạng thêm của public test:
   - khách trung thành (nhiều ngày có mua nhất); khách sộp (từ 3 đơn); khách hay đổi ý;
   - mặt hàng được ưa chuộng; ngày đắt khách; giờ cao điểm; nhân viên chăm chỉ.
4. Mỗi hàm nhận vai trò cột (Task 5), view `sem__*` (Task 4) và khoảng thời gian (Task 6), trả
   về SQL DuckDB và mã luật đã dùng.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/semantics/business_ops.py` | **tạo mới**: các primitive §1, §3 |
| `tests/fixtures/synthetic_business/` | bổ sung đơn huỷ, đơn trả một phần, phí phục vụ, khách trùng tên khác mã, hàng nhiều đơn vị |
| `tests/unit/test_business_ops.py` | **tạo mới**: mỗi primitive một test có đáp án tính tay |

**Xong khi**
- Mọi primitive pass test trên fixture.
- Chạy thử trên 5 doanh nghiệp train cho ra số, không lỗi SQL. Chỉ kiểm tra chạy được; không
  so với đáp án để chỉnh công thức.

---

### Task 8 — Primitive nghiệp vụ mở rộng: nợ, kho, ngành · node `RULE` (3/3)

**Lệnh:** `make check`

**Mục đích.** Phủ nốt các mục còn lại của rulebook. Đây là phần nhiều bẫy nhất:
- nợ tính theo **tổng đơn gồm phí giao**, còn doanh thu thì không;
- đơn COD không phải khách nợ;
- giá vốn lấy từ dòng bán, không tự tính lại từ phiếu nhập;
- luật ngành thắng luật chung.

**Cần làm**
1. Công nợ (§4):
   - nợ một đơn tại ngày X; tổng nợ chỉ cộng đơn còn nợ > 0; đơn trả hàng vẫn tính nếu còn nợ;
   - bỏ đơn COD và đơn sàn TMĐT;
   - tuổi nợ tính từ ngày đặt (đơn đặt hôm nay là 0 ngày);
   - nợ thêm và thu nợ trong kỳ;
   - bảng nợ theo khách: khách lẻ có trong tổng nợ nhưng không có dòng riêng.
2. Kho, nhập, giá vốn (§5):
   - tiền nhập trong kỳ (bỏ phiếu huỷ); nhà cung cấp nhập nhiều tiền nhất và nhà cung cấp chính;
   - tồn hết ngày X; giá trị tồn kho; hàng hết cuối ngày; hàng ế;
   - giá bán X ngày D; hiệu ứng tăng giá;
   - lãi gộp, lãi ròng tháng;
   - tồn theo đơn vị lớn dạng `[số nguyên, số lẻ]`.
3. Theo ngành (§6):
   - cà phê: size M/L, topping ghi chung ô là mặt hàng riêng;
   - nhà thuốc: kê đơn, hàng hết hạn trong N ngày, hoạt chất, lô gần hết hạn;
   - vật liệu xây dựng: số đợt giao;
   - mẹ và bé: voucher, điểm, phí ship là số dương; size theo cột; thành viên mới;
   - sửa xe: lượt xe, phiếu bảo hành, biển số, khách quen;
   - điện thoại: thu máy cũ, trả góp, bảo hành, dòng máy, hãng.
4. Ưu tiên theo tần suất câu hỏi đo ở Task 11 bước 1. Thiếu thời gian thì luật nào chưa có
   primitive sẽ đi đường SLOW hoặc abstain.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/semantics/business_ops.py` | primitive §4, §5, §6 |
| `src/datamate/data/column_roles.py` | thêm vai trò cho phiếu nhập, sổ chi phí, sổ nợ, lô thuốc, sổ sửa xe, sổ góp |
| `tests/unit/test_business_ops.py` | trả nhiều lần, trả cùng ngày mua, COD, hết hàng rồi nhập lại trong ngày, phiếu huỷ, từng luật ngành |

**Xong khi**
- Danh sách `not_compiled` của Task 6 ngắn lại; mỗi luật còn lại có lý do.
- Mọi primitive mới pass test trên fixture.

## Phần 2 — Hiểu câu hỏi

---

### Task 9 — Dựng GPU, model và audit tham số · node `MODELS`

**Lệnh:** `datamate audit-models --config configs/models.yaml`

**Mục đích.** Node `MODELS` mô tả hai checkpoint cho các vai trò model: Qwen3.5-4B (`P4` lập kế
hoạch, `DSL4` lời giải pandas độc lập) và Qwen3.5-9B (`SQL9` sinh SQL, sửa lỗi, critic ở `DPC`).
Phải có cấu hình dự phòng `FALLBACK_SINGLE_9B`. Tổng tham số phải
≤ 15B **theo cách đếm của BTC**, và cách đếm đó chưa được xác nhận (có tính vision encoder, tied
weights không). Task này cần bạn cấp một máy Linux hoặc WSL2 có GPU.

**Cần làm**
1. Cài vLLM, chọn bản đã có bằng chứng hỗ trợ Qwen3.5; ghi phiên bản đã pin.
2. Tải hai checkpoint vào `models/planner`, `models/reasoner`. Ghi revision và SHA-256 từng shard.
   Kiểm tra ngày phát hành và license so với luật.
3. Chạy `audit-models` để đếm tensor thật. Nếu tổng > 15B hoặc cách đếm chưa rõ thì cấu hình
   `FALLBACK_SINGLE_9B` (một model 9B đảm nhận mọi vai).
4. Smoke test:
   - chạy hai server vLLM (`:8001` planner, `:8000` reasoner);
   - kiểm tra chat template và structured output `json_schema`;
   - 20 câu train qua đủ planner → SQL → chạy SQL;
   - đo tokens/giây, thời gian mỗi lời gọi, VRAM, so BF16 với FP8.

**File cần code**

| File | Viết gì |
| --- | --- |
| `configs/models.yaml` | revision, SHA-256, đường dẫn; `parameter_cap` khi đã xác minh |
| `scripts/benchmark_inference.py` | **tạo mới**: smoke test và đo hiệu năng |
| `docs/MODEL_PARAMETER_REPORT.md` | kết quả audit, cách đếm, PASS/FAIL so với trần |
| `docs/RUNBOOK.md` | các bước dựng máy GPU, khởi động server |
| `requirements.gpu.lock.txt` | **tạo mới**: lock môi trường GPU |

**Xong khi**
- Audit đếm được cả hai checkpoint và có kết luận PASS, FAIL hoặc FALLBACK kèm lý do.
- 20 câu chạy hết đường đi mà không lỗi giao thức; số đo ghi trong `EXPERIMENT_LOG.md`.

> ⚠️ Mô hình load thành công không chứng minh trả lời đúng. Không ghi "đạt 15B" khi chưa có
> luật đếm chính thức.

---

### Task 10 — Planner 4B và kiểm tra kế hoạch · node `P4`, `GUARD`

**Lệnh:** `make eval-train`

**Mục đích.** `P4` đọc câu hỏi cùng bằng chứng từ harness và viết `PlanIR`. `GUARD` kiểm tra kế
hoạch trước khi tốn công chạy: luật trích dẫn có thật không, cột có tồn tại không, bộ lọc, khoảng
ngày và loại đáp án có khớp với harness không. Ở commit hiện tại context chứa toàn bộ số thứ tự
dòng nguồn: biz_101 có 56.501 dòng đã chiếm 400 KB trong giới hạn 512 KB, nên doanh nghiệp lớn
hơn sẽ vượt.

**Cần làm**
1. Context gọn: bỏ `source_rows` *(đang dở trong working tree)*; chỉ gửi bảng liên quan (lọc theo
   vai trò cột, tên cột, README), cắt thì gắn cờ `context_truncated`; giới hạn byte đọc từ config.
2. Đưa vào context: vai trò cột và ngành (Task 5), ứng viên thực thể, luật đã đọc (Task 6),
   khoảng thời gian đã giải, danh sách primitive có sẵn (Task 7–8).
3. Viết lại prompt planner: đọc tiếng Việt không dấu và viết tắt ("sp", "dc", "ko"); coi dữ liệu
   là untrusted; chỉ chọn cột, luật, primitive có trong context.
4. `GUARD` là code, kiểm tra đủ các mục của sơ đồ: schema Pydantic; bảng, cột, luật có thật;
   bộ lọc đã grounding; khoảng ngày (khớp với `TIME`); loại đáp án. Sai thì cho planner sửa một
   lần, vẫn sai thì đi nhánh `FAIL` (abstain + `invalid_plan`).
5. Thay `assert plan is not None` trong pipeline bằng raise lỗi có kiểu.
6. Few-shot từ train chỉ dùng khi luật chính thức cho phép (Task 17 bước 1).

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/llm/agents.py` | `evidence_context` gọn; `validate_plan` đủ năm mục |
| `src/datamate/prompts/planner.md` | prompt mới |
| `src/datamate/types.py` | `PlanIR` thêm tham chiếu primitive nếu cần |
| `src/datamate/pipeline.py` | cập nhật lời gọi `evidence_context`; bỏ `assert` |
| `tests/unit/test_plan_validation.py` | **tạo mới**: payload không có `source_rows`; context dưới giới hạn; mỗi mục của `GUARD` có test bắt lỗi |

**Xong khi**
- Context lớn nhất trên 30 doanh nghiệp train và 14 public được đo, đều dưới giới hạn.
- Với fake model, kế hoạch sai cột / sai luật / sai khoảng ngày đều bị `GUARD` chặn.
- Khi có GPU: tỉ lệ `PlanIR` hợp lệ trên nhóm phát triển được ghi vào log.

## Phần 3 — Thực thi

---

### Task 11 — Router và đường FAST · node `ROUTER`, `FSQL`, `FEXEC`

**Lệnh:** `make eval-train` · **Cần có:** Task 7 xong

**Mục đích.** `ROUTER` là code, chọn FAST hoặc SLOW kèm mã lý do và ngân sách; hết ngân sách thì
đi `FAIL`. FAST: một template đã kiểm chứng ghép từ primitive (hoặc một SQL). `FEXEC` qua
allowlist AST của sqlglot (chỉ `SELECT` / CTE), chạy trong worker riêng có giới hạn thời gian, bộ
nhớ, số dòng, rồi kiểm tra kiểu và bất biến. Đường
này nhanh và ít sai nhất, nên càng nhiều câu đi FAST càng tốt. Bộ chạy SQL ở đây dùng chung với
đường SLOW, và ở commit hiện tại có hai lỗi:
- Câu SQL thiếu dấu nháy (`SELECT 'abc FROM t`) ném lỗi `TokenError` không ai bắt, nên cả batch
  dừng. Chạy `--resume` thì lại dừng đúng ở câu đó.
- Allowlist chặn cả `LIKE`, `lower()`, `date_trunc`, nên không viết được câu lọc tên hay nhóm
  theo tháng.

**Cần làm**
1. Phân loại 3.000 câu train theo ý định bằng từ khóa: doanh thu kỳ, top bán chạy, đếm đơn,
   % tăng trưởng, công nợ, tồn kho, lãi… Ghép với loại đáp án để biết nhóm nào nhiều điểm nhất.
   Chỉ dùng câu hỏi và `type`, không lưu đáp án.
2. Viết template cho khoảng 10 nhóm lớn nhất, mỗi template = primitive + khoảng thời gian +
   thực thể. Model chỉ chọn template và tham số.
3. Router: câu khớp template, một bảng hoặc join đã chứng minh cardinality, thời gian và thực thể
   không mơ hồ thì FAST; còn lại SLOW. Mỗi quyết định có mã lý do.
4. Bộ chạy chỉ-đọc *(đang dở trong working tree)*:
   - bắt `sqlglot.errors.SqlglotError`, đưa `traverse_scope` vào cùng `try`;
   - thêm hàm thuần vào allowlist: `like`, `ilike`, `lower`, `upper`, `trim`, `length`,
     `substring`, `concat`, `timestamptrunc`, `timetostr`, `hour`, `dayofweek`, `floor`, `ceil`;
   - vẫn chặn mọi I/O, extension, table function.
5. FAST thất bại ở bước kiểm tra thì nâng lên SLOW, ghi lý do. Timeout hoặc lỗi worker mà không
   còn ngân sách thì đi `FAIL`.

**File cần code**

| File | Viết gì |
| --- | --- |
| `scripts/question_taxonomy.py` | **tạo mới**: phân loại câu hỏi, xuất bảng tần suất |
| `src/datamate/execution/templates.py` | **tạo mới**: template theo nhóm câu hỏi |
| `src/datamate/routing/router.py` | điều kiện FAST theo template, cardinality, độ mơ hồ |
| `src/datamate/execution/sql_policy.py` | bắt lỗi sqlglot, mở rộng allowlist |
| `tests/security/test_execution.py` | SQL hỏng bị từ chối chứ không sập; hàm mới được phép; 17 mẫu nguy hiểm cũ vẫn bị chặn |
| `tests/integration/test_pipeline.py` | câu khớp template đi FAST với fake model; FAST lỗi thì lên SLOW |
| `docs/EXPERIMENT_LOG.md` | bảng tần suất nhóm × loại đáp án |

**Xong khi**
- Có bảng tần suất nhóm × loại đáp án.
- Trên nhóm phát triển đo được tỉ lệ câu đi FAST và độ chính xác của FAST. Lúc này vẫn là độ
  chính xác "nếu trả lời", vì `predict` còn abstain.

---

### Task 12 — Đường SLOW: nhiều SQL, lời giải pandas độc lập, sửa lỗi · node `SQL9`, `DSL4`, `EXEC`

**Lệnh:** `make eval-train`

**Mục đích.** Câu khó đi SLOW. `SQL9` sinh nhiều SQL theo các chiến lược khác nhau (trực tiếp,
CTE, tổng hợp trước rồi mới join). `DSL4` (Pandas coder 4B) viết một lời giải độc lập dạng danh
sách thao tác pandas JSON có kiểu (filter / aggregate / group / sort / select), **chỉ từ câu hỏi
và bằng chứng của `CAT`, không thấy SQL, PlanIR hay kết quả**. `EXEC` chạy trong sandbox: SQL qua
allowlist AST và worker riêng; thao tác pandas qua interpreter của dự án gọi pandas, **không chạy
Python do model sinh, không `eval` / `exec`**. Sửa lỗi có giới hạn, giữ nghĩa câu hỏi và không
nới guardrail. Timeout hoặc lỗi thì đi `FAIL`. Ở commit hiện tại:
- `Pipeline.solve` dài 160 dòng, lồng 6 cấp, không test riêng được vòng sửa lỗi.
- Bảng 56.501 dòng của biz_101 vượt giới hạn 10.000 dòng của nhánh pandas, nên nhánh này luôn lỗi.
  Lỗi đó không có `try` riêng, nên các SQL đã chạy tốt cũng bị bỏ theo.

**Cần làm**
1. Tách `Pipeline.solve` thành `_make_plan`, `_run_sql_candidates`, `_cross_check_dsl`,
   `_write_trace`. Hành vi không đổi.
2. Nhánh pandas có `try` riêng và giới hạn dòng nguồn riêng (`max_dsl_source_rows`); lỗi ghi
   `dsl_failed`, không xóa bằng chứng SQL.
3. Chuyển các con số cứng vào `Limits`/`Models` *(đang dở trong working tree)*: 512000,
   `max_tokens` 4096, 32000, 2000000, số ứng viên SLOW 3.
4. Viết lại prompt `sql_coder.md`, `dsl_coder.md`: dùng primitive làm khối dựng; Pandas coder chỉ
   trả JSON đúng schema `DataOpsCandidate`, không nhận khóa SQL, PlanIR hay kết quả nào.
5. Sửa lỗi theo kết quả chạy: tối đa `max_repairs` lần, chỉ dựa trên loại lỗi, không đổi nghĩa
   câu hỏi; SQL trùng hash thì dừng.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/pipeline.py` | tách hàm, `try` riêng cho nhánh pandas |
| `src/datamate/config.py` | các trường giới hạn mới |
| `src/datamate/llm/client.py`, `src/datamate/execution/worker.py`, `src/datamate/execution/sql_executor.py` | đọc giới hạn từ config |
| `src/datamate/prompts/sql_coder.md`, `dsl_coder.md` | prompt mới |
| `tests/integration/test_pipeline.py` | vòng repair dừng đúng; SQL trùng hash dừng; nhánh pandas lỗi vẫn giữ kết quả SQL; payload Pandas coder không có khóa SQL hay PlanIR |
| `tests/security/test_execution.py` | output Pandas coder không phải JSON đúng schema (ví dụ chuỗi code Python) bị từ chối, không chạy |

**Xong khi**
- Toàn bộ test cũ vẫn pass, và có test riêng cho vòng repair, nâng FAST→SLOW, nhánh pandas lỗi.
- Khi có GPU: trên nhóm phát triển đo được số ứng viên chạy được, số lần repair, độ chính xác SLOW.

## Phần 4 — Kiểm chứng và quyết định

---

### Task 13 — Bộ kiểm chứng dựa trên bằng chứng · node `CHECK`

**Lệnh:** `make check`

**Mục đích.** `CHECK` là code: gom kết quả thành cụm, kiểm tra nguồn gốc đến từng ô, cardinality
của JOIN (tổng hợp trước phía nhiều, ghi số dòng trước / sau), luật, thời gian, đơn vị, và so SQL
với pandas. Ở commit hiện tại bộ kiểm tra coi **mọi JOIN là lỗi cứng**, và coi
mọi bảng có cột cảnh báo là lỗi cứng, nên gần như câu nào cũng bị chặn. Nhiều ứng viên cùng ra một
số **không chứng minh được** số đó đúng: 5 SQL cùng mắc lỗi fanout vẫn ra cùng một số sai.

**Cần làm**
1. Từ cây AST lấy cột ngày dùng để lọc, so với vai trò "ngày đặt" (Task 5) và khoảng thời gian
   đã giải (Task 6).
2. Kiểm tra có lọc trạng thái đơn theo §1 khi câu hỏi là doanh thu hoặc số đơn.
3. Đo số dòng trước và sau JOIN, dựa vào join graph (Task 5), để phát hiện fanout, thay cho luật
   "có JOIN là lỗi". Lời giải đi qua primitive đã kiểm chứng thì không bị đánh lỗi JOIN.
4. Cảnh báo cột nguồn chỉ thành lỗi cứng khi cột đó thật sự được dùng trong lời giải.
5. Gom kết quả các ứng viên thành cụm theo giá trị đã chuẩn hóa; so cụm SQL với kết quả pandas;
   ghi nguồn gốc từng cụm. Chỉ đặt `provenance_complete=true` khi có lineage đến từng ô.
6. Kiểm tra đơn vị: số lượng đã quy về đơn vị cơ bản (Task 4), tiền là VND, phần trăm đúng thang.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/verification/sql_facts.py` | **tạo mới**: đọc cột lọc, khoảng ngày, trạng thái từ AST |
| `src/datamate/verification/invariants.py` | kiểm tra fanout bằng số dòng; cảnh báo theo cột được dùng; gom cụm |
| `tests/unit/test_verification_budgets.py` | T08 (một đơn nhiều thanh toán), T14 (5 SQL cùng sai do fanout vẫn bị bắt), lọc sai cột ngày bị bắt |

**Xong khi**
- Test T08, T14 pass.
- Câu dùng primitive đã kiểm chứng không còn bị `join_grain_not_proven`.

---

### Task 14 — Phát hiện bất đồng và kiểm chứng sai khác · node `DIS`, `DPC`

**Lệnh:** `make eval-train`

**Mục đích.** `DIS` hỏi "các lời giải có bất đồng không, hoặc câu có rủi ro cao không?". Có thì
qua `DPC`: tạo dữ liệu thử nhỏ (challenge data) hoặc phản ví dụ nhắm đúng chỗ hai lời giải khác
nhau, chạy lại để xem lời giải nào đúng định nghĩa. Model 9B chỉ làm critic khi thật cần. Phần
này được giới hạn ngân sách thời gian và số lời gọi.

**Cần làm**
1. Định nghĩa "rủi ro cao": cụm kết quả bất đồng; SQL khác pandas; join chưa chứng minh; thời gian
   hoặc thực thể mơ hồ; luật `not_compiled`.
2. Kiểm chứng sai khác: lấy đúng các dòng nguồn mà hai lời giải xử lý khác nhau (ví dụ đơn có
   trả hàng, đơn nhiều thanh toán), chạy cả hai trên tập nhỏ đó, so với primitive.
3. Critic 9B (tùy chọn): chỉ nhận định nghĩa luật và tập dòng nhỏ, trả về lựa chọn có kiểu, không
   tự sinh đáp án.
4. Kết quả `DPC` là một đặc trưng cho Task 15, không tự quyết định trả lời.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/verification/differential.py` | **tạo mới**: phát hiện rủi ro, tạo tập dòng thử, so lời giải |
| `src/datamate/routing/budgets.py` | ngân sách riêng cho `DPC` |
| `src/datamate/prompts/critic.md` | **tạo mới** nếu dùng critic |
| `tests/unit/test_differential.py` | **tạo mới**: hai lời giải khác nhau ở đơn trả hàng thì tập thử chỉ ra lời giải sai; hết ngân sách thì dừng |

**Xong khi**
- Trên fixture, `DPC` chỉ ra đúng lời giải sai ở các ca T08, T14.
- Thời gian thêm của `DPC` trên nhóm phát triển được đo và nằm trong ngân sách.

> Nếu thiếu thời gian, task này là P2: bỏ bước 2–3, chỉ giữ bước 1 làm đặc trưng cho Task 15.

---

### Task 15 — Quyết định: trả lời / unanswerable / abstain · node `DEC`, `FAIL`

**Lệnh:** `make eval-holdout`

**Mục đích.** `DEC` là code, chọn một trong ba kết quả theo từng loại đáp án, dựa trên bằng chứng
đã hiệu chỉnh, không dùng LLM để cho confidence. Chỉ trả lời khi `p > (A + C) / (R + C)`. R, C, A
phải lấy từ evaluator chính thức (`BLOCKED_BY_OFFICIAL_KIT`); theo §9 cục bộ (+1 / −1 / 0) thì
ngưỡng là 0,5. `FAIL` gom mọi lỗi, timeout, hết ngân sách từ `GUARD`, `ROUTER`, `FEXEC`, `EXEC`
thành abstain kèm mã lý do. `unanswerable` (70 câu train) là một đáp án riêng: kỳ hỏi nằm hoàn toàn
ngoài khoảng dữ liệu, hoặc shop không ghi loại thông tin đó. Nó **khác** với abstain, và khác với
"bằng 0".

**Cần làm**
1. **Unanswerable:**
   - lưu khoảng ngày có dữ liệu của từng shop và từng loại sổ;
   - kỳ nằm hoàn toàn ngoài khoảng thì ra `unanswerable`; kỳ chồng một phần thì tính trên phần
     có dữ liệu (§7);
   - shop không có vai trò cột mà câu hỏi cần (Task 5) thì ra `unanswerable`, kèm `reason` là chuỗi;
   - timeout, lỗi model, lỗi SQL luôn đi `FAIL` ra abstain, không bao giờ ra `unanswerable`;
     một câu lỗi không làm dừng batch.
2. **Đặc trưng cho mỗi câu:**
   - đi FAST hay SLOW; có dùng template không;
   - số ứng viên đồng ý; SQL và pandas có khớp không (đồng ý không phải bằng chứng đúng);
   - số lỗi cứng và mềm, số lần sửa, kết quả `DPC`;
   - độ mơ hồ thời gian và thực thể.
3. **Hiệu chỉnh:** học logistic hoặc isotonic trên nhóm hiệu chỉnh, theo từng loại đáp án. Ngưỡng
   trả lời đọc từ config: `p > (A + C) / (R + C)`, theo §9 cục bộ hiện là 0,5; thay bằng R, C, A
   chính thức khi có evaluator.
4. Đóng băng template, prompt, hàm hiệu chỉnh, ngưỡng. Sau đó **chấm holdout một lần**.
5. Bật trả lời trong `predict`: dưới ngưỡng thì abstain kèm mã lý do.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/decision/answerability.py` | **tạo mới**: khoảng dữ liệu, quyết định `unanswerable` |
| `src/datamate/decision/features.py`, `calibration.py` | **tạo mới**: đặc trưng, hàm hiệu chỉnh |
| `src/datamate/decision/policy.py` | quyết định trả lời / abstain theo ngưỡng |
| `src/datamate/pipeline.py` | gọi `DEC` thay cho abstain cố định |
| `configs/calibration.json` | tham số hiệu chỉnh, ngưỡng, danh sách doanh nghiệp đã dùng |
| `tests/unit/test_decision.py` | **tạo mới**: kỳ ngoài dữ liệu; kỳ chồng một phần; timeout ra abstain; hết ngân sách ra abstain; ngưỡng đúng chiều |
| `tests/integration/test_pipeline.py` | một câu lỗi giữa batch ra abstain kèm mã lý do, các câu sau vẫn chạy |

**Xong khi**
- Điểm holdout dương và cao hơn baseline E0, có khoảng tin cậy theo doanh nghiệp.
- Có Brier score và đường độ chính xác theo độ phủ.
- `EXPERIMENT_LOG.md` ghi đúng một lần chấm holdout.

> 🔒 Sau khi đã thấy điểm holdout thì không chỉnh template, prompt hay ngưỡng theo holdout nữa.
> Muốn chỉnh thì phải ghi lý do và coi điểm holdout cũ là đã hết giá trị.

## Phần 5 — Đầu ra và nộp bài

---

### Task 16 — Formatter đáp án theo §9 · node `OUT`

**Lệnh:** `make check` · **Làm sớm được:** ngay sau Task 2

**Mục đích.** `OUT` là formatter + writer, chỉ là code, không dùng model. Mỗi câu một dòng JSONL
có kiểu, giữ thứ tự và id đầu vào, ghi nguyên tử, có resume. Nhận đầu vào từ cả `DEC` và `FAIL`.
Tên file nộp chính thức chưa xác nhận (`PENDING_OFFICIAL_VERIFICATION`). Rulebook §9: **sai format là sai**, bị −1. Ví dụ:
- tiền phải là số nguyên VND;
- phần trăm ghi `12.4`, không ghi `0.124`;
- câu hỏi theo tháng thì đáp án là `"2025-09"`;
- tên phải giữ đúng dấu như trong dữ liệu.

**Cần làm**
1. Formatter dùng chung cho mọi đường giải:
   - tiền làm tròn về đồng; phần trăm và trung bình làm tròn 2 chữ số;
   - date `YYYY-MM-DD` hoặc `YYYY-MM`;
   - list giữ thứ tự câu hỏi, hòa thì theo mã;
   - table có cột đầu là tên đối tượng;
   - string lấy nguyên từ dữ liệu.
2. `unanswerable` ghi kèm `reason` là chuỗi; abstain ghi đúng dạng hiện có.
3. Chuẩn hóa dùng chung với bộ chấm Task 2, để điểm cục bộ không lệch vì format.
4. Giữ nguyên: một dòng mỗi câu, đúng thứ tự và id đầu vào, ghi nguyên tử, resume.

**File cần code**

| File | Viết gì |
| --- | --- |
| `src/datamate/adapters/submission_writer.py` | formatter theo §9, `reason` cho `unanswerable` |
| `src/datamate/verification/canonicalize.py` | làm tròn và chuẩn hóa dùng chung với bộ chấm |
| `tests/unit/test_writer_audit.py` | mỗi loại đáp án đúng và sai format; làm tròn nửa đồng; tên có dấu giữ nguyên |

**Xong khi**
- Mọi đáp án sinh ra đều qua `validate` và qua bộ chấm Task 2 mà không bị loại vì format.
- Có test phân biệt rõ ba kết quả: dữ liệu bằng 0, không có dữ liệu, và lỗi hệ thống.

---

### Task 17 — Benchmark, Docker, nộp bài · ngoài sơ đồ

**Lệnh:** `make predict-public`, `make all`

**Mục đích.** Chứng minh cả luồng chạy đủ 1.000 câu trong giới hạn thời gian, chạy offline trong
container đúng hướng dẫn BTC, và nộp được.

Phần luật chính thức cần bạn đăng nhập portal và tải về. Nên làm **ngay từ đầu**, song song
với các task khác, vì nó quyết định giới hạn thời gian, entrypoint và cách đếm tham số.

**Cần làm**
1. **Audit luật** (bạn tải tài liệu, Claude ghi audit):
   - xác minh từng dòng trong `docs/OFFICIAL_RULE_AUDIT.md`: cách đếm tham số, ngày phát hành
     weights, phần cứng, thời gian, entrypoint, chính sách few-shot và cache, chấm điểm;
   - có validator chính thức thì nối vào `datamate validate`.
2. **Benchmark** trên GPU đích:
   - chạy cả 1.000 câu public test;
   - đo thời gian nạp model, ingest, p50/p95/p99 mỗi câu, VRAM đỉnh;
   - ước tính cho private test, giữ biên an toàn ít nhất 20%;
   - vượt thời gian thì giảm số ứng viên hoặc tăng tỉ lệ FAST.
3. **Docker GPU:**
   - base image CUDA pin theo digest, lock dependency GPU;
   - weights đặt theo hướng dẫn BTC; entrypoint và đường mount đúng Submission Guidance;
   - chạy với `--network none`.
4. **Nộp thử public test:** `predict` → `validate` → bạn upload lên leaderboard. Ghi điểm vào
   log, so với bộ chấm cục bộ để phát hiện lệch. Không tune lặp theo leaderboard.
5. **Đóng gói:**
   - điền checklist spec §23; hoàn tất ma trận test T01–T26;
   - cập nhật README để người khác chạy lại được từ đầu;
   - `make all` chạy từ nạp dữ liệu đến file nộp.

**File cần code**

| File | Viết gì |
| --- | --- |
| `docs/OFFICIAL_RULE_AUDIT.md` | mỗi dòng `VERIFIED` kèm nguồn, hoặc ghi rõ còn thiếu |
| `configs/official.yaml` | giới hạn và đường dẫn chính thức |
| `src/datamate/config.py`, `src/datamate/cli.py` | mở official mode khi đủ điều kiện; nối validator chính thức |
| `Dockerfile.gpu`, `docker/entrypoint.sh` | **tạo mới**: image GPU chạy offline |
| `Makefile` | target `predict-public`, `all` |
| `docs/SUBMISSION_CHECKLIST.md`, `docs/IMPLEMENTATION_STATUS.md`, `README.md` | trạng thái cuối, lệnh tái lập |

**Xong khi**
- Container chạy hết public test không cần mạng, trong giới hạn thời gian có biên an toàn, và
  output qua validator.
- Có ít nhất một điểm leaderboard public được ghi lại.
- Trạng thái cuối là `READY_FOR_OFFICIAL_SUBMISSION` khi mọi gate chính thức đạt. Nếu chưa đạt
  thì ghi `IMPLEMENTED_BUT_NOT_VERIFIED` kèm bảng blocker.

> 🛑 Hạn nộp 16/11/2026 23:59. Nếu đến 14/11 Task 15 chưa xong, hãy nộp bản đang có. Bản đó
> chỉ trả lời các câu đi FAST qua primitive đã kiểm chứng, còn lại abstain. Đừng nộp bản trả
> lời mọi câu khi chưa hiệu chỉnh: sai bị −1 nên có thể ra điểm âm.

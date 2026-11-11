# Official rule audit

## Supplied organizer text update, 2026-11-09

The user's competition description and evaluation screenshots supersede older
assumptions below. Source level: **USER_SUPPLIED_ORGANIZER_TEXT**, not an independently
downloaded executable kit. No executable evaluator or separate Docker guideline was
supplied in this turn.

| Item | Supplied requirement |
|---|---|
| Score | PA = (correct - wrong) / N; abstain and missing answer score 0 |
| Models | All models used at inference total at most 15 billion parameters, including all MoE parameters; quantization allowed |
| Release/license | Weights publicly released before 26 October 2026, with a license permitting use; fine-tuning allowed |
| Offline | No external services, downloads or network access during inference |
| Data | Train, synthetic and public datasets permitted; public/private test data cannot be used for training or answer collection |
| Hardware | 2 RTX 5090, 32 GB each; 512 GB RAM; CUDA >= 12.8 with matching framework |
| Runtime | Entire private set including model loading within 6 hours |
| Submission | `predict.sh` reads `/data`, writes `/result/submission.jsonl`; logs under `/result/logs/` include ID, final answer, elapsed time |
| Reproducibility | Absolute PA difference between uploaded and rerun results <= 0.03 |
| Ranking | Criterion descending, code ascending for ties; boundary-tie questions excluded; fewer than k returns all candidates |

The local scorer now follows this supplied evaluation text where specified; see
EVALUATION_CONTRACT.md for unresolved details. It is not the official evaluator.
Eligibility of actual model checkpoints, Docker base image and target-hardware
performance remain unverified. `official` mode remains closed.

## Historical audit, 2026-11-04

Date: 2026-11-04, Asia/Bangkok. User supplied `train/`. The portal
https://challenge.zalo.ai/portal/datamate was opened, but its page extractor had
no readable text. No organizer evaluator/validator/deployment kit was found.
VERIFIED_LOCAL_SOURCE refers to direct observations in supplied files, not an
independent authentication of the release or certification of eligibility.

| Item | Evidence | Status |
|---|---|---|
| Input records | train/questions.jsonl: id, business_id, question, as_of_date; CLI measured 3,000 questions / 30 businesses | VERIFIED_LOCAL_SOURCE |
| Packages | train/data/biz_*/README.md and CSV/XLSX/SQLite | VERIFIED_LOCAL_SOURCE |
| Business rules | train/rulebook.md §§1–10; industry precedence in §6 | VERIFIED_LOCAL_SOURCE; formulas not fully compiled |
| Time | rulebook §2; Monday weeks, periods through as_of_date, calendar.csv lunar mapping | VERIFIED_LOCAL_SOURCE; supported subset implemented |
| Missing vs zero | rulebook §7 | VERIFIED_LOCAL_SOURCE |
| Output shape | rulebook §§7,9; typed answers, abstain/unanswerable, table shape | VERIFIED_LOCAL_SOURCE; local validator only |
| Scoring | rulebook §9 states +1/-1/0 and exact normalization rules | VERIFIED_LOCAL_SOURCE_TEXT; authoritative evaluator missing |
| Parameter accounting | exact inclusion of auxiliary/tied/quantized parameters not supplied | PENDING_OFFICIAL_VERIFICATION |
| Eligible weights | release cutoff/license policy not supplied | PENDING_OFFICIAL_VERIFICATION |
| Hardware and deadlines | no organizer runtime limits supplied | PENDING_OFFICIAL_VERIFICATION |
| Submission entrypoint and validator | no executable kit supplied | BLOCKED_BY_OFFICIAL_KIT |
| Cache/few-shot/offline policy | full official policy absent | PENDING_OFFICIAL_VERIFICATION |

Engineering limits in default.yaml are not official limits. Official mode fails
closed even if YAML flags are changed. Actual adapter/validator integration and
item-level evidence are required before this boundary can be opened.

# Official rule audit

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

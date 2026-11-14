---
number: 223
title: Monthly review job, stored in aux.db
stage: 'ops / review loop (architect plan 2026-11-13 item 3, R142)'
size: M
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #388 (branch feat/monthly-review); first run 2026-12-04 · accountable: implementer'
---
- **223. Monthly review job, stored in aux.db.** Value: the month's report existed only as a CLI (`monthly-report`), so nobody reviewed a month unless asked. Acceptance, tests first: a daily `monthly_review` scheduler job (06:30 UTC) builds on the 1st (or on a later tick within `monthly_review.catch_up_days` = 10) and reviews the calendar month before its slot in UTC whatever zone the slot arrives in, reuses `monthly_report` per account, and stores one row per month in aux.db `monthly_reviews` (migration 31, additive). Reads only: never a broker or an order (tests/test_monthly_review.py::test_month_boundary_uses_utc_and_catches_up, ::test_review_is_stored_in_aux_db_and_posted_once_per_month).

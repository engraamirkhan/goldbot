---
number: 223
rank: 74
title: Monthly review job, stored in aux.db
stage: 'ops / review loop (architect plan 2026-11-13 item 3, R142)'
size: M
status: 'implemented (2026-11-13, branch feat/monthly-review) · accountable: implementer · reviews: code-reviewer, sre'
---
- **223. Monthly review job, stored in aux.db.** Value: the month's report existed only as a CLI (`monthly-report`), so nobody reviewed a month unless asked. Acceptance, tests first: a `monthly_review` scheduler job on day 1 of each month at 06:30 UTC, caught up for 10 days after downtime and run once per slot, reviews the calendar month before its slot in UTC whatever zone the slot arrives in, reuses `monthly_report` per account, and stores one row per month in aux.db `monthly_reviews` (migration 30, additive; a re-run replaces it and keeps what was posted). Reads only: never a broker or an order (tests/test_monthly_review.py::test_month_boundary_uses_utc_and_catches_up, ::test_review_is_stored_in_aux_db_and_posted_once_per_month).

---
number: 225
rank: 74
title: Monthly spend, trial budget used and reliability scorecard
stage: 'finance / ops (R142)'
size: S
status: 'implemented (2026-11-13, branch feat/monthly-review) · accountable: implementer · reviews: cfo, cto, sre'
---
- **225. Monthly spend, trial budget used and reliability scorecard.** Value: the CFO and CTO had no monthly figure for what the system cost and how often it broke. Acceptance, tests first: agent spend from aux.db `agent_spend` (USD, converted at `monthly_review.gbp_per_usd`) plus the known hosting costs in `monthly_review.hosting_gbp_month` (docs/finance); the registry's trials for the month's quarter against its budget (read only, a flag when unreadable or exceeded); from `scheduler_jobs` the jobs failing and overdue (a due slot past its max_late_hours) as of the review and the failures recorded; from `health_alerts` the health messages, dead man's switch changes and the market minutes down (fail to ok, weekend close left out) (tests/test_monthly_review.py::test_reliability_counts_failing_and_stale_jobs_and_market_minutes_down). Limit: scheduler_jobs keeps current state only, so failures are cumulative, not per month.

---
number: 224
title: Monthly tie-out of net P&L against the broker balances flags, never passes
stage: 'finance / review loop (R142; finance audit RC-9)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #388 (branch feat/monthly-review) · accountable: implementer'
---
- **224. Monthly tie-out of net P&L against the broker balances flags, never passes.** Value: the daily ledger ties each day out, but nothing checked the month end to end against goldbot's own records. Acceptance, tests first: opening = last balance snapshot before 00:00 UTC on the 1st, closing = last one before the next 1st; closing - opening - (closed records' P&L + deposits + withdrawals + adjustments) beyond `monthly_review.balance_tolerance` + per-trade tolerance is UNRECONCILED and flags the review; so do no snapshots (`no_data`), a failed daily ledger in the month, a login or currency change, a baseline deal in the window and a record in another currency (tests/test_monthly_review.py::test_unreconciled_balance_flags_not_passes, ::test_a_failed_daily_ledger_in_the_month_flags_even_when_the_month_nets_out).

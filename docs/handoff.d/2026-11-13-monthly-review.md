- 2026-11-13 (branch feat/monthly-review; R142; BACKLOG 223-226; review loop item 3): a `monthly_review` scheduler
  job (day 1, 06:30 UTC, caught up for 10 days, once per slot) reviews the UTC calendar month before its slot
  (goldbot/ops/monthly_review.py): per account the monthly report (reused) and the tie-out of goldbot's net P&L
  against the broker balance snapshots in aux.db (RC-9; a difference beyond `monthly_review.balance_tolerance` +
  0.01 per trade, no snapshots, a failed daily ledger, a login or currency change all FLAG, never pass), agent spend
  (aux.db agent_spend) + known hosting (£4.52 E5.Flex), the quarter's trial budget used (K, read only) and a
  reliability scorecard (scheduler_jobs failing/overdue, health_alerts, market minutes down). Stored in aux.db
  `monthly_reviews` (aux migration 30, additive); one Telegram message (amounts) and one GitHub issue labelled
  `review-monthly` (no login, host or balance; outbox file without a token), each once a month. Reads only: no broker,
  no order. docs/process/review-cadence.md: the program-director clears open `review` and `review-monthly` issues
  first each session; no Claude crons, nothing pending on the owner.
- Merge note: the weekly review lane was planned to take aux migration 30; whichever lands second renumbers its
  migration to 31 (the sequence must stay 1..n without gaps). Limits: scheduler_jobs keeps current state only, so
  job failures are cumulative and failing/overdue are as of the review; the performance figures use opportunity days
  (monthly_report's convention) while the tie-out uses the UTC month.

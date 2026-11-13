- 2026-11-13 (branch feat/monthly-review, on top of feat/weekly-review; R142; BACKLOG 223-226; review loop item 3):
  a daily `monthly_review` scheduler job (06:30 UTC, goldbot/ops/monthly_review.py) reviews the UTC calendar month
  before its slot: built on the 1st, or on a later tick within `monthly_review.catch_up_days` (10) when the 1st was
  missed; a done month is a no-op. Per account the monthly report (reused) and the tie-out of goldbot's net P&L
  against the broker balance snapshots in aux.db (RC-9; a difference beyond `monthly_review.balance_tolerance` + 0.01
  per trade, no snapshots, a failed daily ledger, a login or currency change all FLAG, never pass), agent spend (aux.db
  agent_spend) + known hosting (£4.52 E5.Flex), the quarter's trial budget used (K, read only) and a reliability
  scorecard (scheduler_jobs failing/overdue, health_alerts, market minutes down). Stored in aux.db `monthly_reviews`
  (aux migration 31, after the weekly review's 30); one Telegram message (amounts) and one GitHub issue labelled
  `review-monthly` (no login, host or balance), each once a month. Reads only: no broker, no order.
- Retries (code review 2026-11-13, two MEDIUMs): every daily tick resends whatever has not gone out for any stored
  month (a GitHub outage or no token on the 1st is posted later; the text waits in
  state/aux/monthly_review/outbox/<month>.md meanwhile). A build that raises stores a `flagged` row with the error,
  sends nothing and is rebuilt on every tick until it succeeds (the scheduler marks a slot done before the job runs,
  so the retry lives in the job). Health check `monthly_review` warns on a failed build and on a review not fully sent
  a day after it was built; it is absent until a review is stored.
- docs/process/review-cadence.md: the program-director clears open `review` and `review-monthly` issues first each
  session; no Claude crons, nothing pending on the owner. Limits: scheduler_jobs keeps current state only, so job
  failures are cumulative and failing/overdue are as of the review; the performance figures use opportunity days
  (monthly_report's convention) while the tie-out uses the UTC month.

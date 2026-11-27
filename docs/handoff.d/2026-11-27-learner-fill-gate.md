- 2026-11-27 health `learning:cost_cells` is judged by its input, not the clock: daily_review refits a (broker,
  session) cell only after `refit_min_fills` (200) eligible fills since its fit, and at ~40 fills a day the 9-day
  silence read as a dead learner (WARN, FAIL 48 h later).
  - `daily_review.pending_trades`/`pending_fills` (one rule for refit and health); `refit_cells` uses it, output unchanged.
  - All cells below 200: ok, "waiting on fills: most-filled cell <broker> <session> n/200 since its fit <age> ago; at 0:
    <brokers>". A cell at 200 whose daily_review ran with no new row: WARN, FAIL 48 h after that run.
  - Counted over `HealthContext.learning_hosts` (enabled exploring accounts), cached 15 min; none or unreadable: clock.
  - `learning:arm_lifecycle_verdicts` stays on the clock: one verdict per settled candidate, no count threshold.

- 2026-11-09 (branch fix/session-budget-admissible; volume diagnosis 2026-11-09 cause 4, fix 2): the exploration
  daily budget's session split (`exploration_budget.session_budgets`) now counts only admissible edge candidates of the
  trailing 10 trading days: candidates of the pass's active arms (every book's retired arms out) whose decision time
  passes the static entry refusals (`entry_refusals.STATIC`: window_0207, f1_pit, learning_icm_only; the HAR refusals
  read a day's job row and are not applied to history). The split is by largest remainder with one fill floor, so the
  three sessions sum to max_per_day (before: rounding could pass it). A session's unspent budget rolls forward to the
  next session of the same UTC day (asia, london, newyork, then the asia after the New York close): `session_entries`
  refuses once the day's fills reach the budgets' sum through the signal's segment, so an early session never spends
  a later one's. `daily_refusal` takes `segment=` (`day_segment`) instead of `session=`; the per-session counts
  (`session:<name>`) are gone from `entries_today`. RiskGate, max_per_day/max_per_hour, loss caps and thin_control_*
  unchanged. Expected: about +10-15 fills a day on icm-demo/icm-probe inside the same day caps; vantage-demo none (no
  supply, cause 2). Tests: `tests/test_exploration_daily_budget.py`.

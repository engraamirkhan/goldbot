- 2026-11-10 (branch fix/telegram-health-cpu; telegram CPU attribution 2026-11-10, R119): the telegram service's
  health pass no longer holds the pump. `_pump` starts it as its own task (`_start_health_pass`), one at a time (a
  pass due while the last still runs is skipped and logged), so proposal cards and outcome edits keep flowing every
  poll; each pass logs `health pass s=<seconds>` and warns over `HEALTH_SLOW_S` (20 s). The pass's two hot spots are
  gone: `arms._registry_rows` parses `state/research_registry.jsonl` once per file version (inode, mtime_ns, size;
  234 reads of a 3,353-row copy 7.3 s -> 0.35 s), and `registry.stale_preregistrations` indexes withdrawals and trial
  reads once (`_Exemptions`; same answer as the old scan, n_trials_effective warm 1.15 s -> 0.12 s on that copy, more
  on the brain's 778-withdrawal registry). No health check decides differently. Not done: the strategy catalogue
  load in pnl_basis (~16 s profiled) and the history_gap duckdb reads (~10 s) are uncached; measure the pass in the
  journal after deploy before a slower cadence for heavy checks (proposal D). Tests: `tests/test_telegram_health_task.py`,
  `tests/test_arms_registry_cache.py`, `tests/test_registry_withdrawal_index.py`.

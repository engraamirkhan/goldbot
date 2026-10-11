- A failed engine start keeps the account's exposure (2026-10-11, trading-safety re-verification LOW-D). A refused
  start (`ops.run.refuse_start`) and a start whose positions read fails (`Engine._load_orders`) used to replace
  engine_<account>.json with a small error payload, so the supervisor summed 0 lots for an account that may still hold
  positions. Both now go through `risk.supervisor.write_startup_error`, which merges `startup_error` and `dq_error`
  into the previous file and keeps `open_lots`, `open_notional`, `equity`, `day_start_equity`, `week_start_equity`,
  `balance_closed_hwm` (finite numbers only), the currency and the absolute daily limit. The supervisor halts entries
  on a file carrying `startup_error` (`engine_startup_refused`, `startup_refused_engines`) while still summing its
  exposure and money; a normal engine write clears it. Rollback order in the RUNBOOK ("Bridge compatibility") and
  docs/deploy/2026-10-batch.md section 5: the brain first, then the MT5 box. The dashboard's entry-status texts
  (goldbot/api/entry_status.py) now word `engine_startup_refused`, `open_risk_unknown` and `same_direction_cap`;
  the guard test that every gate and supervisor reason has owner text read only `throttle_*` from the `out.append`
  helpers and now reads them all, which is how the two open-risk codes were found unworded (`heat_cap` left
  FUTURE). Tests: tests/test_startup_error_exposure.py, tests/test_api_entry_status.py.

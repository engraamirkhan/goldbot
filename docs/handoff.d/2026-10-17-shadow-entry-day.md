- **Shadow-book day clustering on `entry_ts + tf` confirmed and pinned (BACKLOG 78, implementer, 2026-10-17; quant
  and code review pending; not pushed).** ADR 0022 clusters by entry UTC day. A shadow trade's `entry_ts` is its
  signal bar's open, and the trade enters at that bar's close.
  - **Finding.** No code was keying on the open. `attribution.trade_rows` has used `entry_ts + tf` for
    `TradeRow.entry_utc` since the report was first written (490ebe4). That field is the cluster key for every cell
    and for the director's term. E-6 (`pipeline.entry_utc`) uses the same key. The other shadow-book readers
    (`population`, `ruin`, `jobs`, `api/explain`) key on `exit_ts` and do no ADR 0022 clustering. `perf_report`
    and `gates_phase` read `ClosedTrade.entry_utc`, the fill time.
  - **Change.** Tests in `tests/test_attribution.py` cover four things: a 15m 23:45 signal and a 1h 23:00 signal
    cluster on the next UTC day; a signal one bar earlier keeps its own day; the overall se uses that key; and
    attribution agrees with `pipeline.entry_utc`. Changing the key back to the bar open makes all 5 cases fail. The
    `trade_rows` docstring now states the key, and ADR 0022 has a dated note saying this is not a rule change.
  - **Readings.** No CR1 se, t_bar or verdict moved. The repo has no attribution or metrics golden, and no golden
    changed.
  - **Open, out of scope.** `ruin.r_from_shadow_book` and `population.weekly_returns` time exits by `exit_ts` (the exit
    bar's open) rather than by `exit_ts + tf`. The weekly bucket can move by one bar at the Sunday open. This is not
    ADR 0022 and is a quant-reviewer call.

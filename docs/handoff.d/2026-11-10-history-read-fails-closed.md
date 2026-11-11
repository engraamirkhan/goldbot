- 2026-11-10 (branch fix/history-read-fails-closed; backlog 155, R123): a store read of the engine's history fails
  closed. Before, a raising read at warm start crashed the engine service (open trades unmanaged until a restart
  that crashed again), and the key zones kept a failed cache read for good (history_days 52.4). Now the engine starts,
  runs its exits, and refuses every entry while the read fails (`history_read`: RiskGate data_quality_error, specialist
  closes journalled `history_read:<tf>`, engine health FAILs naming it); the store is re-read every 30 min of engine
  clock and lifts it only once its minutes merged, then judges the window for `history_short` (review round 1: a
  recovered read no longer opens entries on a short window). A weekday-gold warm start with fewer than
  `engine.warm_start_min_bars` (28 800, capped at the 90 000 bars kept) 1m bars refuses the same way
  (`history_short:<tf>`) until the window holds them. Below the 400-bar bar-close floor, open trades' time exits now
  run at their close. Each re-read's duration is logged (warning over 2 s). Follow-ups: backlog 157 (re-read off the
  tick thread), 158 (quarantine spans read fails closed), 159 (key-zones warning throttle). Owner step when it fires:
  check the data root syncs (the health reason names the cause). Tests: tests/test_engine_history_read.py.

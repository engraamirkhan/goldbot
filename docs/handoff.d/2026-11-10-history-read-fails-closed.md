- 2026-11-10 (branch fix/history-read-fails-closed; backlog 155, R123): a store read of the engine's history fails
  closed. Before, a raising read at warm start crashed the engine service (open trades unmanaged until a restart
  that crashed again), and the key zones kept a failed cache read for good (history_days 52.4). Now the engine starts,
  runs its exits, and refuses every entry while the read fails (`history_read`: RiskGate data_quality_error, specialist
  closes journalled `history_read:<tf>`, engine health FAILs naming it); the store is re-read every 30 min of engine
  clock and a read that succeeds lifts it. A weekday-gold warm start with fewer than `engine.warm_start_min_bars`
  (28 800) 1m bars refuses the same way (`history_short:<tf>`) until the window holds them. Owner step when it
  fires: check the data root syncs (the health reason names the cause). Tests: tests/test_engine_history_read.py.

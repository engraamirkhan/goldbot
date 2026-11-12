- 2026-11-12 (branch fix/restart-rerun-barrier-shadow; backlogs 163, 164, 165, R129): three restart follow-ups of
  backlog 161. (163) The last processed base close is saved per book with the orders file (`closed_through`) before
  that close's decisions run, and restored at start (journalled `restart_closed_through`): a restart whose first
  quote is stamped before it treats the quote as a late tick and never closes or decides that bar again, so an entry
  RiskGate refused at the original close cannot pass at a later price. The exploration step starts at the restored
  close too; a saved close ahead of the host clock plus one bar is ignored (journalled). A trade without
  `bars_through` keeps counting its dead-feed barrier from its entry bar until this engine processes a close. Old
  files start as before. (164) The dead-feed time barrier counts a bar when any of its minutes is
  expected open (`downtime.open_bars`, the catch-up's rule): the bar the daily break ends inside counts, a CME holiday
  does not. (165) Shadow trades open across a restart are held at start and replayed through the bars missed while
  down once the history covers them: no fill running or unabsorbed and not one expected-open minute missing
  (`engine/shadow_replay.py`, point in time, identical to never restarting); not covered within 6 h, or no resume
  point (`last_bar_ts`, new on each shadow trade): `restart_gap`, now left out of promotion stats, shadow_z,
  recalibration, the throttle's shadow win and the CUSUM model watch (a gap never rolls a champion back); the 12% re-arm
  read keeps gap losses, drops gap wins. Not changed: the exploration 1m/5m minute pass (its own per-minute state) and
  the perf/automode/ruin shadow reads still count `restart_gap` trades (backlog 174). Replay and store goldens unchanged. runner.py LINE_CAP 3083 -> 3073.
  Tests: tests/test_engine_restart_rerun.py, tests/test_engine_no_quote_barrier.py, tests/test_shadow_restart_replay.py.

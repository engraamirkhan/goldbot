- 2026-11-12 (branch fix/downtime-bars-catch-up; backlog 161, R126): base closes missed while the engine is down now
  count toward open trades' holds (engine/downtime.py). Each open trade saves `bars_through`, the last base close in
  its `bars_held`, in the same orders-file record; at the first base close after a restart it is credited the closes
  in between whose base bar had a minute in `SessionTable.expected_open` (the engine's session table less exchange
  holidays, the history gap guard's calendar: weekends, the daily break and holidays never count). Its time exit, and a
  passed exit-policy hard flat (also above the floor), fire at that close; before, the hold ran long by the downtime.
  A close at or before `bars_through` is never counted again (a restart whose first quote is stamped before a counted
  close). Exits only get earlier. Not changed: the first deploy's own downtime is not credited (old files have no
  `bars_through`); closes skipped while running (a failed positions read, a bar no tick fell in) and shadow trades
  are not credited; a stale re-closed bar still runs its decisions, as before (only its count is skipped).
  Replay golden unchanged; store golden: restart.json only (`bars_through: null` on each in-memory open trade).
  Tests: tests/test_engine_downtime_catch_up.py.

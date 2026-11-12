- 2026-11-12 (branch fix/downtime-bars-catch-up; backlog 161, R126): base closes missed while the engine is down now
  count toward open trades' holds (engine/downtime.py). Each open trade saves `bars_through`, the last base close in
  its `bars_held`, in the same orders-file record; at the first base close after a restart it is credited the closes
  in between whose base bar had a minute in `SessionTable.expected_open` (the engine's session table less exchange
  holidays, the history gap guard's calendar: weekends, the daily break and holidays never count). Its time exit fires
  at that close; an exit-policy hard flat fires there (also above the floor) only when its policy timeframe's close at
  or after the deadline fell inside the downtime, else at that
  policy close, as before. Before, the hold ran long by the
  downtime. Credits are logged per trade and kept in `downtime_bars` (orders file and closed-trade record):
  time_exit/hard_flat with downtime_bars>0 exits at the restart close, not the label deadline: excluded from
  label-vs-live exit-price/slippage reconciliation. Closed trades before this deploy that were open during engine
  downtime held past their pre-registered hold and are flagged in performance reads, not pooled silently. The dead-feed
  time barrier now counts from `bars_through`, so a feed dying right after a restart no longer re-counts closes in
  `bars_held` (an early exit). Shadow trades open across a restart are tagged `restart_gap` (backlog 165 replays
  them). Follow-ups: backlog 163 (a re-closed bar re-runs its entries), 164 (no-quote barrier's any-minute rule).
  A close at or before `bars_through` is never counted again (a restart whose first quote is stamped before a counted
  close). Exits only get earlier. Not changed: the first deploy's own downtime is not credited (old files have no
  `bars_through`); closes skipped while running (a failed positions read, a bar no tick fell in) are not credited; a
  stale re-closed bar still runs its decisions, as before (only its count is skipped; backlog 163).
  Replay golden unchanged; store golden: restart.json only (`bars_through: null`, `downtime_bars: 0` on each in-memory
  open trade).
  Tests: tests/test_engine_downtime_catch_up.py.

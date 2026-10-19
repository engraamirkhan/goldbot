- **Engine history continuity** (fix, brain 2026-10-19: restarts joined release bars ending 10-05 to live minutes):
  `goldbot/engine/history.py`. `warm_start` only reads the store: release minutes (`bars_1m`, source dukascopy) before
  one cut-over, the traded broker's own minutes (new table `bars_1m_live`, source = broker; never another broker's)
  from it on. The tail and the holes are filled from the broker's ticks (`copy_ticks` via `pull_day`, the live bar
  builder) on a background thread through its own bridge client (no fill without a bridge), under a per-page hard
  deadline (`backfill_budget_s`, 2000-tick pages, 0.2 s pause); exits never wait for it. Completed minutes are written
  by a background writer (bounded queue, oldest batch dropped with a log). A decision frame whose window misses more
  than `history_gap_max_min` (60) minutes the engine's session table calls open, and no broker pull explains, refuses
  entries for that timeframe (journal `history_gap:<tf>`, no shadow entry; exits, policies, open shadow trades
  unaffected) and is re-filled every 30 min. `engine_<account>.json` gains `history_gap` and `history_backfill`
  (cut-over, fill status, dropped batches). Not yet: a health check and a dashboard tile reading them.

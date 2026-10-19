- **Engine history continuity** (fix, brain 2026-10-19: restarts joined release bars ending 10-05 to live minutes):
  `goldbot/engine/history.py`. `warm_start` backfills the store -> now gap from the broker's ticks (`copy_ticks` via
  `pull_day`, one UTC day at a time, `ticks_to_1m(..., DEFAULT_SESSIONS)` like the live bars; bounded by
  `backfill_max_days`/`backfill_page_ticks`/`backfill_budget_s`, failing soft); completed live minutes and backfilled
  ones are stored in `bars_1m` under source `live-<broker>` at each bar close (warm start prefers them over the
  release's); a decision frame whose window misses more than `history_gap_max_min` (1440) minutes the session table
  calls open refuses entries for that timeframe (journal `history_gap:<tf>`, no shadow entry; exits, policies and open
  shadow trades unaffected). `engine_<account>.json` gains `history_gap` and `history_backfill`. Not yet: a health
  check and a dashboard tile reading them.

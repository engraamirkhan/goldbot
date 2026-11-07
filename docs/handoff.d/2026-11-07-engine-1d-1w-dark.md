- **1d and 1w exploration arms in the engine, shipped dark (WS-4, consolidated plan 2026-11-07; implementer).**
  `goldbot/engine/long_bars.py`: a 1d bar closes at 21:00 UTC Monday-Friday (the long daily history's roll), a 1w bar
  is the W-FRI week of those days and is decided fresh at the first base close after the weekly open (never on
  Friday); a 1d decision at Friday 21:00 is journalled `exploration_skipped:weekend`, never queued. Warm-up from
  `<engine.long_history_dir>/bars/<symbol>_1d.parquet` (else `long/<symbol>_daily_long.parquet`), point in time on
  `visible_at`/`available_utc`; a missing/unreadable file, a gap to the engine's window or too few rows hold the arm
  back with a `history_gap:<tf>` journal row, never a crash. Dark flag: settings `engine.timeframes_live` (default
  1m-4h, no 1d/1w): an arm on any other timeframe is held retired from the start (`exploration_arm_dark`, exits
  managed). Replay identity: `tests/test_engine_replay_identity.py` pins a 7-day replay of 15m/1h/4h arms to a golden
  captured on cb3e52a4 (verified on a clean checkout of it) and shows it unchanged with 1d/1w arms dark and live.
  Weekend cut, rollover, stale and common-filter code are hash-pinned unchanged. Go-live blocker found: the standing
  common filter (rollover 20:45-22:15 UTC, Friday from 19:00) drops every ARM-SPEC rule's 1d candidate at its 21:00
  close and every 1w candidate, so live 1d/1w would trade nothing until that is decided. Not done: the catalogue
  adapter. Review owed: trading-safety-reviewer (exploration step), code-reviewer.

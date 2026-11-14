---
number: 251
rank: 79
title: 1d/1w long-history daily refresh, preflight and history_gap alert (BACKLOG 144 blocker 4)
stage: Engine · BUILD
size: S
status: 'done (2026-11-14, branch feat/engine-backlog-1114): job long_history_refresh (ops/long_history.py), preflight item long_history, health long_history:<account> (NOTIFY_ON_WARN), blocker 4 left GO_LIVE_BLOCKERS_1D_1W; tests tests/test_long_history_refresh.py · reviews: sre, data-engineer'
---
- **251. 1d/1w long-history daily refresh, preflight and history_gap alert (BACKLOG 144 blocker 4).** Value: a live
  1d/1w arm warms up from `<data_root>/<engine.long_history_dir>/bars/<symbol>_1d.parquet`; nothing on the brain keeps
  that file current, so a day after the last manual copy the history no longer reaches the engine's window and the arm
  is held back (`history_gap:<tf>`) for good. Acceptance: (1) a scheduled daily job appends the engine store's complete
  days to the file on the 21:00 UTC roll (`long_bars.daily_from_1m`, point in time, atomic write), with a test that a
  stale file is extended and an up-to-date one left untouched; (2) a preflight item that fails when a live 1d/1w
  timeframe's history does not reach the engine window (`long_bars.joined` is not GAP), skipped while 1d/1w are dark;
  (3) a health check that warns (NOTIFY_ON_WARN) when an arm's `history_gap:<tf>` has lasted more than a day; each with
  a test that fails without it; then the blocker leaves `GO_LIVE_BLOCKERS_1D_1W`.

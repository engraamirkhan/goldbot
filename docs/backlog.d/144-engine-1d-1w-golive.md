---
number: 144
title: 1d/1w engine go-live blockers (WS-4)
stage: Engine · BUILD
size: M
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #393 (6c90577c + review fixes 9f8f5d26; R149, ADR 0081): blockers 1-3, 5 and 6 built dark with tests. Residual tracked elsewhere: 4 -> 251, ratification -> 252, 7 (guard lift) -> 253 · accountable: implementer'
---
- **144. 1d/1w engine go-live blockers (WS-4).** Value: WS-4 lets the engine run 1d and 1w exploration arms
  (`goldbot/engine/long_bars.py`), but it ships dark and the settings validator (`EngineSettings.timeframes_live`,
  `GO_LIVE_BLOCKERS_1D_1W`) refuses 1d/1w until each blocker below is closed; as built a live 1d/1w arm would trade
  nothing or trade at the worst hour. Blockers:
  1. Send 1d entries after 22:15 UTC once the broker has reopened, the bracket from the send price; tests in EET and
     EEST.
  2. Move the 1w decision out of the first 15-60 minutes after the Sunday open.
  3. The 1w missed-week catch-up (a restart across the weekly open), persisted.
  4. A daily refresh of the long-history file, a preflight check that it reaches the engine window (resolved under
     `data_root`: `engine.long_history_dir`), and an alert when `history_gap:<tf>` lasts more than a day.
  5. The rules' standing common filter at the 21:00 UTC close drops every 1d/1w candidate (quant decision).
  6. `_trail_runners` and `score_twins` on `long_bars.is_bar_close`.
  7. A CONFIRM pass and a trading-safety sign-off on the settings diff that lifts the guard.
  Acceptance: each blocker has a test that fails without it; the guard is lifted only in the same change as 7, with
  the replay-identity test (`tests/test_engine_replay_identity.py`) still byte-identical for the existing arms.

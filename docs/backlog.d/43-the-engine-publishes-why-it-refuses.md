---
number: 43
rank: 6
title: The engine publishes why it refuses entries, and writes state while the market is closed
stage: Operational resilience, entry status · RUN
size: M
status: 'in review: the heartbeat part landed as BACKLOG 59 (6287d10, 412fd0d; in PR #79); the `entry_blocks` part is built on lane/entry-blocks (R43), reviews owed: code, trading-safety, ui-ux · accountable: implementer · due 2026-10-21'
---
- **43. The engine publishes why it refuses entries (trading-safety review; from the entry-status review of 66ba8f9,
  ADR 0011).** Value: the dashboard's entry status (docs/ux/entry-status.md) still mirrors in the API which published
  flags block entries, and cannot see rollover, the Friday weekend cut or per-trade refusals; a closed market and a dead
  engine are told apart only by the session table. Acceptance:
  - `RiskGate.account_reasons(st, now)` returns the account-level reasons (everything in `check()` that does not
    depend on the intent: halts, data, clock, market closed, rollover, weekend, spread, positions, caps, throttle per
    agent); `check()` calls it, so the two cannot drift; test: for every flag, `check()` refuses with exactly the
    reasons `account_reasons` lists plus the intent's own;
  - the engine publishes `entry_blocks` (that list, codes only) in `engine_<account>.json` at every state write, and
    the last per-trade refusal per agent (`fx_conversion_unavailable`, `min_lot_exceeds_risk`, `margin_level_floor`,
    `combined_exposure_cap`, `daily_loss_limit_abs`) with its time;
  - the engine writes its state on the host clock while the market is closed too (a heartbeat, at least every 60 s),
    so silence always means a fault;
  - the API then prefers `entry_blocks` (already merged when present) and the flag mapping and the `min_lot` fallback
    in `goldbot/api/entry_status.py` go; exits are never gated by any of it.
  Sweep 2026-10-16 (product owner): accountable `implementer`, due 2026-10-21; trading-safety review. Not started before today: `goldbot/api/entry_status.py` still maps flags. Acceptance as above.

---
number: 258
rank: 85
title: S05 exits at the exchange's early close
stage: 'Research catalogue (follow-up of 237)'
size: S
status: 'done 2026-11-14 (branch fix/data-followups-1114) · accountable: strategy-researcher · reviews: quant-reviewer'
---
- **258. S05 exits at the exchange's early close.** Value: the S05 spec exits at the T-1 close with early closes honoured; early closes are now in the calendar (exchange_holidays.early_closes, BACKLOG 237) but S05 still exits at the daily bar's close. Acceptance: S05 reads `early_closes` point in time and exits at the early close instant on those days; a test with a 13:00 ET half day; a registry note on the event-count change.

  **Done 2026-11-14 (code).** `Events.exit_at` (int64 ns per event; `EB.add(exit_at=...)`) is a time exit inside the
  hold's last bar; `sim.simulate(exit_bars=...)` prices it on the instrument's finer bars (`walk_until`: same stop and
  target rules, the time exit at the close of the last finer bar ending at or before the instant, never later). S05
  reads `early_close_<exchange>` (dispatcher.add_holidays, from `exchange_holidays.early_closes`, point in time): on a
  T-1 early close published by the T-2 decision close the event exits at it. The dispatcher passes gold's 1m bars or
  the 4h CFD bars (`exit_bars`, EXIT_TF) for 1d cells; without finer bars the daily close is kept and counted
  (`exit_at_unpriced`). Tests: tests/test_s05_early_close.py (13:00 ET half day 2015-12-24 exits at 18:00 UTC on 1h
  bars; a release seeded 1 ns after the decision is not read; the dispatcher's early closes). Registry note:
  docs/research/prereg-2026-11-07-catalogue-screen.md 14c (entries and event counts unchanged, 199/149; S05 stays
  unregistered; specs.yaml left as it is because its sha256 pins the registered unit list). Only S05 sets exit_at,
  so no other unit's trades change.

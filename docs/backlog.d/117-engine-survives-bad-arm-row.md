---
number: 117
rank: 5
title: A bad exploration arm row must not stop the engine (exits keep running)
stage: 'build'
size: 'S'
status: 'ready (a hash mismatch no longer aborts the start, PR #341 WS-1; a row the Arm validator refuses still raises in load_arms) · accountable: implementer · reviews: trading-safety-reviewer · due 2026-11-17 (triage 2026-11-13)'
---
- **117. A bad exploration arm row must not stop the engine (exits keep running).** Value: on 2026-10-26 the
  trading-safety review blocked a branch whose G1A/G1C rows used a `window_local` form the `Arm` validator refuses.
  `load_arms` raised inside `config_for` (goldbot/engine/exploration.py) at engine start, so all three demo engines
  would have crash-looped. Entries fail closed in that state, but exits do not: time barriers, the Friday cut and
  reconciliation stop. Acceptance: an invalid arm row disables exploration (or that arm only) with a dq_event and an
  alert, while the engine starts and manages open positions; a test loads a deliberately broken arm file and asserts
  the engine loop turns, positions are reconciled and no exploration candidate is produced; the trading-safety
  reviewer signs off.

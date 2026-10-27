---
number: 117
rank: 47
title: A bad exploration arm row must not stop the engine (exits keep running)
stage: 'build'
size: 'S'
status: proposed
---
- **117. A bad exploration arm row must not stop the engine (exits keep running).** Value: on 2026-10-26 the
  trading-safety review blocked a branch whose G1A/G1C rows used a `window_local` form the `Arm` validator refuses.
  `load_arms` raised inside `config_for` (goldbot/engine/exploration.py) at engine start, so all three demo engines
  would have crash-looped. Entries fail closed in that state, but exits do not: time barriers, the Friday cut and
  reconciliation stop. Acceptance: an invalid arm row disables exploration (or that arm only) with a dq_event and an
  alert, while the engine starts and manages open positions; a test loads a deliberately broken arm file and asserts
  the engine loop turns, positions are reconciled and no exploration candidate is produced; the trading-safety
  reviewer signs off.

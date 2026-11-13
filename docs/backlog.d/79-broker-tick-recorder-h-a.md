---
number: 79
title: Daily broker tick recorder for the H-A holiday diagnostic
stage: Data (broker ticks, research inputs) · LEARN
size: S
status: 'done (triage 2026-11-13): PR #90 (c48c96f4)'
---
- **79. Daily broker tick recorder for the H-A holiday diagnostic.** Value: the H-A holiday diagnostic (docs/research/holiday-list-measurement-2026-10.md) needs the broker's own ticks per day (spread, tick rate by session) which Dukascopy cannot supply; today nothing records the bridge's tick stream to disk. Acceptance: a scheduler job on the brain records XAUUSD ticks from the bridge for each trading day into a daily Parquet under state/ticks/ (bid, ask, time via epoch_ns; no account identifiers), rotated and backed up, with a health WARN when a trading day has fewer than N ticks; a loader for research; test coverage for rotation, the WARN and the loader; disk use bounded (state the daily size).

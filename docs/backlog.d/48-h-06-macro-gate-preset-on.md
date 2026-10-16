---
number: 48
rank: 42.0
title: H-06 macro-gate preset on slow tsmom, with its event count
stage: Idea generation · FIND
size: S
status: 'in review (built 2026-10-11: tsmom preset `slow_macro`, agent `tsmom-g0-fed4baec9f`, and `count_h06` in count-owed-events; quant review of the readings (a)-(e) in `preregistration-2027Q1.md` H-06; count run 2026-10-16 (run 38931951849, main @ `eafc6ce`): lower bound 107 < floor 150, so H-06 is withdrawn by its rule once the quant reviewer confirms `count_h06`; run-input table written 2026-10-16; accountable: strategy-researcher; due 2026-12-04)'
---
- **48. H-06 macro-gate preset on slow tsmom (research director, 2026-10-11).** Value: H-06 is in the Q1 queue but
  has no implementation (the `tsmom` presets hold only `slow`). Do: a preset that keeps H-01's candidates only when
  the 20-day change in DFII10 and in the broad dollar index (from `macro-v1`, `asof_join` on `available_utc`, as known
  at the signal bar's close) both agree with the side; a missing value drops the candidate. Add its events-only count
  (signals by side, the one-at-a-time lower bound) to count-owed-events. Acceptance: tests that the gate reads no
  value after the signal bar; agent id pinned; count pasted into `preregistration-2027Q1.md`. If the lower bound is
  below the daily floor in force, or this is not merged by 2026-12-04, H-06 is withdrawn before the freeze and
  `reserved_trials_quarter` 2027Q1 becomes 8 (rule in the pre-registration). Quant review. Depends on: `macro-v1`.

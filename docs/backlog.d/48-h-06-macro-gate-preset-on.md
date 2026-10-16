---
number: 48
rank: 13
title: H-06 macro-gate preset on slow tsmom, with its event count
stage: Idea generation · FIND
size: S
status: 'done (preset `slow_macro` and `count_h06` on `main`, `d2f18c6`; counted 2026-10-16 by count-owed-events run 38931951849 on `macro-v1`: 1,105 signals, one-at-a-time lower bound 107 < 150; quant reviewer confirmed `count_h06` 2026-10-16, so H-06 is withdrawn by its pre-registered rule; run-input table written 2026-10-16 as the record; withdrawal in the spec + reservation 8 and the lower-bound one-bar fix done 2026-10-16 by the implementer on `lane/h06-withdraw-lowerbound`, quant review pending; count-owed-events re-dispatch after merge) · accountable: strategy-researcher · due 2026-10-23'
---
- **48. H-06 macro-gate preset on slow tsmom (research director, 2026-10-11).** Value: H-06 is in the Q1 queue but
  has no implementation (the `tsmom` presets hold only `slow`). Do: a preset that keeps H-01's candidates only when
  the 20-day change in DFII10 and in the broad dollar index (from `macro-v1`, `asof_join` on `available_utc`, as known
  at the signal bar's close) both agree with the side; a missing value drops the candidate. Add its events-only count
  (signals by side, the one-at-a-time lower bound) to count-owed-events. Acceptance: tests that the gate reads no
  value after the signal bar; agent id pinned; count pasted into `preregistration-2027Q1.md`. If the lower bound is
  below the daily floor in force, or this is not merged by 2026-12-04, H-06 is withdrawn before the freeze and
  `reserved_trials_quarter` 2027Q1 becomes 8 (rule in the pre-registration). Quant review. Depends on: `macro-v1`.
  Sweep 2026-10-16 (product owner): accountable `strategy-researcher`, due 2026-12-04; escalate to the program-director by 2026-11-13 if the count is below 150. Acceptance as above.

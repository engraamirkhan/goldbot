---
number: 48
rank: 13
title: H-06 macro-gate preset on slow tsmom, with its event count
stage: Idea generation · FIND
size: S
status: 'in review (built, d2f18c6 in #75; quant-reviewer asked 2026-10-11; count once count-owed-events runs) · accountable: strategy-researcher · due 2026-12-04'
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

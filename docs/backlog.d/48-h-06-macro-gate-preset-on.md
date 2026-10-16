---
number: 48
title: H-06 macro-gate preset on slow tsmom, with its event count
stage: Idea generation · FIND
size: S
status: 'done 2026-10-16 (preset `slow_macro` + `count_h06` on `main` in d2f18c6; counted by run 38931951849: lower bound 107 < 150, H-06 withdrawn by its rule in fc99e86, reservation 8; lower-bound fix 9754ca0; quant APPROVED the H-06 table at 717baa3; withdrawal ships to main in PR #79) · accountable: strategy-researcher'
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
  **Acceptance scorecard 2026-10-16 (product owner, AGENT_STANDARDS 7 checks):** (1) criteria: yes. `tests/test_tsmom_macro_gate.py` (no value after the signal bar, agent id
  pinned, a missing value drops the candidate) and `tests/test_count_owed_events.py` pass (re-run 2026-10-16 at
  3092923); the count is pasted in `preregistration-2027Q1.md`; the withdrawal is applied by the rule. (2) DoD: yes
  (the run-input table is the researcher's record). (3) S1-S12: yes, no exception recorded. (4) reviewers: quant
  APPROVED the preset and the count (717baa3); quant sign-off of the lower-bound fix 9754ca0 **not on record**.
  (5) CI/merged: the preset yes (on `main`); the withdrawal and the fix **no, in PR #79 (open)**. (6) PR text: yes
  (#79 "H-06 withdrawn"). (7) role and date: yes (strategy-researcher, due 2026-10-23, met). The re-dispatch of
  count-owed-events belongs to BACKLOG 5 (merged recount by 2026-11-13). Accepted, conditional on #79 merging and
  the quant note on 9754ca0.

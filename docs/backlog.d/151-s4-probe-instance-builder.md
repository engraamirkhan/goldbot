---
number: 151
title: S4 uses Lane 3's probe instance builder once one exists
stage: 'Research · EVAL-2 S4'
size: S
status: 'later (triage 2026-11-13): only if Lane 3 adds a probe builder · accountable: test-engineer'
---
- **151. S4 uses Lane 3's probe instance builder once one exists.** Value: S4's S21 OCO side-0 probe calls Lane 3's `probe.first_passage` / `probe.probe_cell`, but the instance builder (one instance per OCO pair, side of the first-filled leg, side 0 when both fill on one bar, twins re-decided and resolved the same way) lives in `scripts/eval2_selftest.probe_units` because Lane 3 has none. Two builders would drift. Acceptance: if Lane 3 adds a probe instance builder, S4 calls it and `oco_instances`/`probe_units` go; a test that both give identical instances on the S21 explore events until the switch; S4's |t| < 2 result re-recorded under the new harness hash.

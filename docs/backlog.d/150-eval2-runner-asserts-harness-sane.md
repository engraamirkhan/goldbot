---
number: 150
title: EVAL-2 L2/L3 and forward runners call assert_harness_sane before any read
stage: 'Research · EVAL-2 L2/L3/forward runner'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #386 (branch fix/eval2-runner-integrity, R138) · accountable: implementer'
---
- **150. EVAL-2 L2/L3 and forward runners call assert_harness_sane before any read.** Value: `scripts/eval2_selftest.assert_harness_sane(harness_hash, forward=False)` (Lane 4) refuses unless the latest full self-test report for the harness hash has S0-S4 and S6 passed (S5 too for forward reads); nothing calls it yet, so an L2/L3 read could run on an untested or edited harness. Acceptance: the Lane 3 L2/L3 runner and the Lane 5A forward reader call it with the current `harness_hash()` before reading any real cell (`forward=True` for forward reads); a refusal test per runner (no report, a BLOCKED test, a report for another hash) shows the read does not start and nothing is written to the registry.

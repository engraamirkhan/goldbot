---
number: 265
rank: 90
title: 'H-81 cost stop: read one cost version per stratum after ADR 0092'
stage: Research integrity · costs
size: S
status: 'proposed 2026-11-20 (data-engineer, ADR 0092) · accountable: quant-reviewer, implementer'
---
- **265. H-81 cost stop: read one cost version per stratum after ADR 0092.** Value: `arm_stops.cost_state` takes the
  95% UB of the recorded `cost` / `stop_loss` over a stratum's fills. After ADR 0092 a stratum's window mixes legacy
  rows ($0.30 slippage prior) with rows charged on `ecr-2026-11-20.1`. Its state then follows the mix, not the arm.
  Acceptance:
  1. a quant ruling chooses one of two rules: the stop reads only rows of the newest `cost_version`, with its own
     30-fill minimum; or every row is repriced on one version;
  2. the rule is built with a test that seeds rows of two versions;
  3. the daily review names the version each cost-stop state rests on.

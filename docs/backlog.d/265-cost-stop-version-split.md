---
number: 265
rank: 90
title: 'H-81 cost stop: reprice every row on the cost version in force (ADR 0092)'
stage: Research integrity · costs
size: S
status: 'done 2026-11-20 (branch fix/true-costs, quant C2) · accountable: data-engineer · reviews: quant-reviewer'
---
- **265. H-81 cost stop: reprice every row on the cost version in force (ADR 0092).** Value: `arm_stops.cost_state` takes the
  95% UB of the recorded `cost` / `stop_loss` over a stratum's fills. After ADR 0092 a stratum's window mixes legacy
  rows ($0.30 slippage prior) with rows charged on `ecr-2026-11-20.1`. Its state then follows the mix, not the arm.
  Acceptance:
  1. quant ruling (C2 on ADR 0092): every row is repriced on the version in force (`arm_stops.cost_per_r`), never
     filtered to new-version rows (that would reset suspended strata to ok);
  2. a test seeds rows of two versions; a suspended state survives the switch;
  3. the daily review names the version each cost-stop state rests on.

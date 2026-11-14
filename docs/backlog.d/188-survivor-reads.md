---
number: 188
title: 'Survivor reads: Z10D, Z9D, Z5P at 300 fills and the H-90 London read'
stage: 'Evaluation · PROVE (profitability-2026-11-12 (b)/(c); prereg-2026-11-12-london-signal-arms.md)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): resolved by the pre-registered 2010-17 kill-or-support read, PR #388 (b7955179; prereg-amendment-2026-11-13-survivor-reads.md). Excess over the matched control: Z10 -0.009 R [-0.016, -0.002], Z9 -0.006 [-0.015, +0.003], Z5 +0.004 [-0.004, +0.011], net -0.05..-0.07 R at the ICM cost of record; all three retired, K kept. H-90 closed before any leg read (design futility, K +0; 62a5d269). Verdict: FAIL, no live candidate · accountable: evaluator'
---
- **188. Survivor reads: Z10D, Z9D, Z5P at 300 fills and the H-90 London read.** Value: PROVE, the only path to a
  live candidate today. The 2026-11-12 profitability read found no live candidate; Z10D, Z9D and Z5P are the only
  arms ahead of their matched controls, and H-90 (London-only signal arms, K +3) is the one pre-registered session
  hypothesis. Each needs one honest read on its own trigger, not a raw-P&L look (GS1 beat its own control by
  -0.43 R while showing +£58). Acceptance:
  1. Fill counts per arm and its matched control (LCZ10D, LCZ9D, Z5P's control) are reported on 2026-11-20 and
     weekly after, from `aux.db`, with evidence exclusions applied.
  2. At 300 fills (or the arm's pre-registered stop), the evaluator writes a verdict against the pre-registered
     rule: excess R over the matched control with a day-clustered CI, net of broker-measured costs, deflated at the
     real K; PASS / FAIL / INCONCLUSIVE with the reason.
  3. H-90 is read at 400 London signal fills or on 2026-12-17, whichever comes first, exactly as section 6 of its
     pre-registration; no discovery fill counts.
  4. A PASS is not a live decision: it lists what the candidate still needs (confirm window 2018-25, cost cells
     with at least 30 fills each) and goes to "Needs the owner" only for the money/live step.
  Dependencies: 150 (harness check) before any EVAL-2 L2/L3 read is used. TRACEABILITY: none (evaluation record).

---
number: 188
rank: 1
title: 'Survivor reads: Z10D, Z9D, Z5P at 300 fills and the H-90 London read'
stage: 'Evaluation · PROVE (profitability-2026-11-12 (b)/(c); prereg-2026-11-12-london-signal-arms.md)'
size: S
status: 'ready (added 2026-11-13, triage) · accountable: evaluator · reviews: quant-reviewer · progress check 2026-11-20; each read on its pre-registered trigger'
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

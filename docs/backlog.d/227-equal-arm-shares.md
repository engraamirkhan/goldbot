---
number: 227
title: Equal demo arm shares instead of the Thompson bandit (H-61 allocator retired)
stage: 'research ops (ML review 2026-11-13, lane ML1)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #390 (feat/ml-cleanup-snapshots, R143) · accountable: implementer'
---
- **227. Equal demo arm shares instead of the Thompson bandit.** Evidence (`docs/research/ml-review-2026-11-13.md` section 2 row 3): over 24 broker-days, bandit-weighted next-day R was +0.085 and equal-weighted +0.086, with a rank correlation of 0.003. Since 11-09 a -0.247 R arm had sat at the 30 % cap. Acceptance:
  - The nightly learner writes equal shares among each style's enterable arms that have a labelled fill.
  - An arm with no fill yet keeps the cold-start floor (quant Q2), so a style never spends more than the bandit would.
  - Shares stay inside the same 5 % floor, 30 % cap and each arm's own ceiling.
  - Retired, blocked and not-configured arms stay at 0.
  - The allocator is a settings switch, `research.exploration.allocator`. The default is `equal`; `thompson` stays selectable.
  - No cap, pot, budget or RiskGate rule changes.
  - Tests: `tests/test_ml_review_cleanup.py`.

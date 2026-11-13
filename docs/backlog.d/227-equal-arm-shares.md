---
number: 227
rank: 75
title: Equal demo arm shares instead of the Thompson bandit (H-61 allocator retired)
stage: 'research ops (ML review 2026-11-13, lane ML1)'
size: S
status: 'done 2026-11-13 (branch feat/ml-cleanup-snapshots; R143) · accountable: implementer · reviews: quant-reviewer, code-reviewer'
---
- **227. Equal demo arm shares instead of the Thompson bandit.** Evidence (`docs/research/ml-review-2026-11-13.md` section 2 row 3): over 24 broker-days, bandit-weighted next-day R was +0.085 and equal-weighted +0.086, with a rank correlation of 0.003. Since 11-09 a -0.247 R arm had sat at the 30 % cap. Acceptance:
  - The nightly learner writes equal shares among each style's enterable arms, cold arms included.
  - Shares stay inside the same 5 % floor, 30 % cap and each arm's own ceiling.
  - Retired, blocked and not-configured arms stay at 0.
  - The allocator is a settings switch, `research.exploration.allocator`. The default is `equal`; `thompson` stays selectable.
  - No cap, pot, budget or RiskGate rule changes.
  - Tests: `tests/test_ml_review_cleanup.py`.

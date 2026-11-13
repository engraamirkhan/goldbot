---
number: 228
rank: 76
title: Retire the H-62 online meta-labeller's retrain (code and data kept)
stage: 'research ops (ML review 2026-11-13, lane ML1)'
size: S
status: 'done 2026-11-13 (branch feat/ml-cleanup-snapshots; R143) · accountable: implementer · reviews: quant-reviewer'
---
- **228. Retire the H-62 online meta-labeller's retrain.** Evidence (ML review section 2 row 4):
  - `exploration_models` and `exploration_priors` held 0 rows.
  - 0 of 19,990 candidates carried a feature snapshot, so the meta-labeller could never train.
  - Its feature family is the one R-20 retired.

  Acceptance:
  - The Sunday retrain runs only while `research.exploration.meta_labeller` is true (default false).
  - The code, the tables and the candidate log with its propensities are kept.
  - The retirement is recorded in `docs/research/hypotheses.md` (H-62 row).
  - Tests: `tests/test_ml_review_cleanup.py`.

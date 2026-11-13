---
number: 229
rank: 77
title: Stop the specialists' Saturday LightGBM retrain (watchers kept)
stage: 'research ops (ML review 2026-11-13, lane ML1)'
size: S
status: 'done 2026-11-13 (branch feat/ml-cleanup-snapshots; R143) · accountable: implementer · reviews: quant-reviewer, trading-safety-reviewer (no order path change)'
---
- **229. Stop the specialists' Saturday LightGBM retrain.** Evidence (ML review section 2 row 1):
  - 145 out-of-sample shadow trades gave AUC 0.44 and Brier 0.206, against 0.197 for the base rate.
  - There are 0 live agents.
  - The retrain had been skipped for 25 days.

  Acceptance:
  - `saturday_retrain` fits no new challenger while `research.specialist_retrain` is false (the default).
  - It still syncs bars, ensures founders, judges the challengers already in shadow and saves the population.
  - `recalibrate`, `drift_watch` and `model_watch` are unchanged.
  - The retirement is recorded in `docs/research/hypotheses.md` (R-07 row).
  - Tests: `tests/test_ml_review_cleanup.py`.

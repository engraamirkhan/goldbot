# 0102: obj-v1 core: regularisation units, offset-form shuffle check, decision-time cost

Status: accepted with conditions for points 1, 2 and 5 (quant review of PR #461, 2026-11-25); points 3, 4 and 6 as
amended by that review, in force from this commit. Research and demo only. A change after the first OMB-1 / H-102
Stage 0 fit or the sr-v3 fit of 2026-12-13 costs K +1 (ADR 0098 amendment).

## Context
ADR 0098 (obj-v1, amendment R-H / R-I) and the ML lifecycle plan of 2026-11-25 (N1, N2, N5, N6, N10) are implemented
once in `goldbot/research/objective.py`, `baselines.py`, `diagnostics.py` and `decision_cost.py`. Some points of the
plan could not be coded as written:

1. **N5 units.** The plan states "penalty as λ0 per effective row (C_fold = 1/(λ0 · n_eff))" and three acceptance
   tests: weights x 10 give an identical fit; P phase copies at 1/P reproduce the real-only fit; an overlap H scales the
   penalty by H. With C_fold = 1/(λ0 · n_eff) the data term and the penalty both scale with n_eff, so H cancels and the
   third test fails; the per-row penalty also never vanishes as n grows.
2. **N6 under the offset form.** "Keep share <= 5 % in every cell whose weighted base rate is below 0.5" was written
   for the p_w >= 0.5 logit. Under the offset form (R-I) a fit to shuffled gross R collapses to its intercept, and
   intercept − cost(x) > 0 is a legitimate cost-only keep, not overfitting.
3. **Decision-time cost.** R-I: cost(x) is "the ub95 cost of record known at decision time, never the realised cost".
   The H-101 replay rows carried only the realised W2 leg r_mid − r = k·RT + k·OV·1[stop] − swap/risk, which holds the
   exit outcome (the overshoot on stop exits, gapped stops included) and the nights actually held. Fed to a keep rule
   it leaks the outcome: a "cost <= x" rule scores S = +0.054 R on the H-101 Stage L rows; within arm, the realised leg
   correlates −0.53 with clipped gross R.

## Decision
1. **Units (signed with conditions).** Fit weights are normalised to sum to n_eff / H (Kish n_eff of the raw weights,
   H the overlap design effect). The ridge penalty λ0 ||β||² (standardised inputs, intercept free) is a prior worth λ0
   effective rows per coefficient. `fit_offset` takes `design_effect` with NO default and needs λ0 > 0. In sklearn
   terms: `sample_weight` = the normalised weights with C = 1 / (2 λ0). Every card states λ0, H, how H is measured
   (default 1 / mean uniqueness) and the implied shrinkage (n_eff/H) / (n_eff/H + λ0) at its smallest fold, >= 0.9
   (`objective.shrinkage`). The plan's fourth N5 check is restated as: the same rows give the same fit in the live and
   the history pipelines (shrinkage differs with n_eff by design, so a ratio of 1 across different n is not a test).
2. **Shuffle check (redefinition signed; metric amended).** N6 is relative to the intercept-only rule. The metric is
   the per-cell false-keep share mean(keep & ~keep0) <= 5 % (or the cell keeps fewer than MIN_KEPT rows), with
   mean(keep XOR keep0) reported. A net excess (keep share − intercept keep share) was rejected: noise keeps cancel
   against noise drops. Folds are alternate session days.
3. **Cost.** Every obj-v1 keep rule and baseline takes cost(x) from decision-time quantities
   (`research/decision_cost.py`):
   cost(x) = k (RT_BP + OVERSHOOT_BP · p_stop[arm]) + E[nights] · k · 1e4 · |swap rate(side)|, k = 1e-4 · entry /
   risk_usd, p_stop per arm from learn rows labelled before the judged block (pooled only for an unseen arm),
   E[nights] = rollover nights over [decision, decision + max_hold_s] x the arm's learn ratio realised / full-hold
   nights, the swap rate from `SwapSpec.research_shipped()` as a fraction of price, absolute. `history_replay.label`
   persists `entry`, `risk_usd` and `barrier_hit` from now on; caches labelled before it back k out of the realised
   leg exactly (stop flag r_mid <= −1, nights held, signed swap rate). RT_BP / OVERSHOOT_BP are the W2 point values; they serve the descriptive H-101 retro and tests only. Before the first OMB-1 / H-102 Stage 0 or sr-v3 fit, cost(x) charges per broker x session x tf max(W2 bps, the engine cost of record's ub95 converted to bps at its window's mean price), the ecr version pinned in the card; a cell below min_fills takes W2. A judged read on W2 point values alone is not a gate read.
4. **S decomposition (four parts).** gross skill q (kept gross − all gross); cost avoidance q (all cost(x) − kept
   cost(x)); path cost q (all e − kept e), e = realised − cost(x); base q · mean net − max(0, mean net). They add to S
   exactly. A read without cost(x) has no split and fails the gate (`decision_cost` leg).
5. **B8 (signed).** The arm x year intercept is in sample on the judged rows; S(B8) >= 0 by construction. It stays on
   the serve bar (R-S); `best_bar` reports the best rung with and without it and a card reports both.
6. **Gate (amended).** Legs: not abstained, the full BOOT draws, S >= 0.05 R with a day-block lower bound > 0, kept
   mean > 0, gross >= 0, kept mean − side-matched control mean >= 0.05 R with a day-block lower bound > 0 (joint draws
   over both day sets), 2x cost S >= 0 (cost from raw gross − raw net, clipped after), the decision-time split.
   Anything missing fails closed. The verdict field is `legs_passed`: K deflation and the two judged reads are the
   registry's.

## Consequences
- sr-v3, zoo-v2, OMB-1 and H-102 call `objective.fit_offset`, `objective.read`, `objective.gate`, `baselines.ladder`,
  `decision_cost.cost_x` and the diagnostics. sr-v2 and zoo-v1 are unchanged. Every module using `fit_offset`,
  `keep_rule` or `gate` is guarded or listed with its reason (`objective.UNGUARDED_USERS`, test).
- `history_replay.py` is part of `history_replay.code_hash`: this change invalidates the H-101 label and feature cache
  keys, so a new history run relabels and recomputes features (H-101 is NULL; nothing frozen depends on them).
- H-101 under obj-v1, descriptive (0 K, seen rows; scripts/research/obj_v1_h101_recompute.py): S between −0.0204 and
  −0.0236 R for all 8 candidates (B5 −0.0207); every learner loses to B0 abstain. With cost(x) the within-arm
  correlation of cost and gross is −0.003 (realised leg −0.53); the leaky B4 rule falls from +0.054 R to −0.0033 R.
  The ladder's "model" is the best of 8 by S on the same rows: an in-sample choice that favours the model, and it
  still loses.

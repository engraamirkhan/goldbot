# 0102: obj-v1 core: regularisation units, offset-form shuffle check, decision-time cost

Status: proposed (implementer, 2026-11-25). The quant-reviewer signs before the first OMB-1 / H-102 Stage 0 fit and
the sr-v3 fit of 2026-12-13 (ADR 0098 amendment: a choice made after a look costs K +1). Research and demo only.

## Context
ADR 0098 (obj-v1, amendment R-H / R-I) and the ML lifecycle plan of 2026-11-25 (N1, N2, N5, N6, N10) are implemented
once in `goldbot/research/objective.py`, `baselines.py` and `diagnostics.py`. Three points of the plan could not be
coded as written:

1. **N5 units.** The plan states "weights normalised to sum to Kish n_eff x overlap design effect; penalty as λ0 per
   effective row (C_fold = 1/(λ0 · n_eff))" and three acceptance tests: weights x 10 give an identical fit; P phase
   copies at 1/P reproduce the real-only fit; an overlap H scales the penalty by H. With C_fold = 1/(λ0 · n_eff) the
   data term and the penalty both scale with n_eff, so H cancels and the third test fails; the per-row penalty also
   never vanishes as n grows (the estimator is not consistent).
2. **N6 under the offset form.** "Keep share <= 5 % in every cell whose weighted base rate is below 0.5" was written
   for the p_w >= 0.5 logit. Under the offset form (R-I, primary) a fit to shuffled gross R collapses to its intercept,
   and intercept − cost(x) > 0 is a legitimate cost-only keep, not overfitting.
3. **Decision-time cost.** R-I: cost(x) is "the ub95 cost of record known at decision time, never the realised
   cost". The H-101 replay rows carry only the realised W2 leg r_mid − r, which charges OVERSHOOT_BP on stop exits
   only. Fed to a keep rule it leaks the outcome: the B4 rule "cost <= x" scores S = +0.054 R on the H-101 Stage L
   rows, against −0.0026 R with a decision-time cost.

## Decision
1. **Units.** Fit weights are normalised to sum to n_eff / H (Kish n_eff of the raw weights, H the overlap design
   effect the caller states). The ridge penalty λ0 ||β||² (standardised inputs, intercept free) is a prior worth λ0
   effective rows per coefficient, fixed in the card before data. All three N5 tests hold
   (tests/research/test_objective.py). In sklearn terms the equivalent is `sample_weight` = the normalised weights
   with C = 1 / (2 λ0), not C = 1/(λ0 · n_eff).
2. **Shuffle check.** N6 bounds the out-of-fold keep share **in excess of the intercept-only rule** at 5 % per cell
   (or the cell keeps fewer than MIN_KEPT rows and would abstain). Folds are alternate session days.
3. **Cost.** Every obj-v1 keep rule and baseline takes cost(x) as a decision-time column. The H-101 retro rebuilds it
   from decision-time quantities (scripts/research/obj_v1_h101_recompute.py): k = entry · 1e-4 / risk backed out per
   row, cost(x) = k (RT_BP + OVERSHOOT_BP · p_stop), p_stop from rows labelled before 2012. The realised leg is the
   cost charged in net R and in the S decomposition, never an input to a keep.
4. **S decomposition.** R-I's "gross skill (kept gross − all gross) and cost avoidance (the rest)" is reported per
   candidate in three additive parts: gross skill q (kept gross − all gross), cost avoidance q (all cost − kept cost),
   and base q · mean net − max(0, mean net). The three add to S exactly.
5. **B8** (arm x year intercept) is computed in sample on the judged rows (an out-of-sample arm x year intercept does
   not exist for an unseen year). It is not tradeable; S(B8) >= 0 by construction; it stays on the bar as R-S rules.

## Consequences
- sr-v3, zoo-v2, OMB-1 and H-102 call `objective.fit_offset`, `objective.read`, `objective.gate`, `baselines.ladder`
  and the diagnostics; sr-v2 and zoo-v1 are unchanged.
- H-101 under obj-v1, descriptive (0 K, seen rows): S between −0.0204 and −0.0236 R for all 8 candidates (best F2
  −0.0204, B5 −0.0207); every gate leg but `gross` and `not_abstained` fails; every zoo learner loses to B0 abstain
  (S 0) and to the best rung on the bar (B8 +0.0004 R).
- The quant may overrule any of the five points before the first fit named above; after it, a change costs K +1.

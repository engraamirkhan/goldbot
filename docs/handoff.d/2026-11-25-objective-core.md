- 2026-11-25 obj-v1 core (lifecycle plan N1, N2, N5, N6, N10; ADR 0098 + amendment R-H / R-I; ADR 0102). One
  implementation of the objective of record: `goldbot/research/objective.py` (±10 R clip, uniqueness weights, Kish
  n_eff, offset-form ridge on clipped gross R with the decision-time cost as a known offset, keep iff pred − cost > 0,
  ABSTAIN from counts first per unit and per arm (100 kept / 30 days), S = mean(net x keep) − max(0, mean net) with a day-block lower bound,
  S = gross skill + cost avoidance + base, 2x cost stress, £/day on unclipped R, the gate legs, `paired_delta`, and an
  AST import guard over `GUARDED_MODULES`); `baselines.py` (B0-B8 on the same rows, `best_bar`, `beats_ladder`);
  `diagnostics.py` (weighted calibration, BSS / MSE skill, within-arm AUC, seed SD, learning curve, shuffled-label
  keep check, oracle canary, score deciles). sr-v2 and zoo-v1 are unchanged. H-101 Stage L recomputes to
  S = −0.0207 R (B5; all 8 between −0.0204 and −0.0236) with `scripts/research/obj_v1_h101_recompute.py`
  (local cache only); every zoo learner loses to B0 abstain. Found: the replay's realised cost leg leaks the outcome
  into any keep rule (B4 +0.054 R on it; within-arm corr with gross −0.53). Review round 1 (quant + code, PR #461):
  `decision_cost.py` (cost(x) from entry / risk_usd, per-arm p_stop and night share, |swap|; within-arm corr −0.003),
  `history_replay.label` persists entry / risk_usd / barrier_hit (invalidates the H-101 cache keys), four-part S split
  on cost(x), control leg with a day-block bound, `design_effect` required, false-keep shuffle metric, B8 reported both
  ways, wider guard (CV searches, `scoring=`, early stopping, relative / dynamic imports, reverse-dependency test).

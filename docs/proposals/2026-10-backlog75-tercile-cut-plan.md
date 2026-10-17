# Plan: BACKLOG 75 (E-2) — discovery trades `p >= the top-tercile cut` of the inner cross-fitted p

Status: plan, architect, 2026-10-17 (recorded by the program director); quant-reviewer plan review before any build.
Due 2027-01-29 (O-4 runs 2027-02-12 and does not run without this merged).

**Requirement.** `docs/research/preregistration-2027Q1.md` "Threshold rule, per row" (~l.583-590): O-4 "uses a
top-tercile cut … the 2/3 quantile of the inner cross-fitted out-of-fold p of the training fold's rows, never
in-sample training p … test proving the cut never sees a test-fold row". O-5, O-6, O-8 and O-10 keep the coded rule
(calibrated p > break-even + 0.02). TRACEABILITY: add an E-2 row. ADR 0022 untouched. Frozen: Q4 trial rows and results,
the `reading_rule` string for the default rule.

**Today.** `pipeline._walk_forward` (pipeline.py:311-313): `p = _cross_fitted(...)`, `threshold =
candidate_threshold(...)`, `taken = p > threshold`. `_cross_fitted` (pipeline.py:158) calibrates fold k only on earlier
folds' out-of-fold p_raw (≥ MIN_CALIBRATION_ROWS = 50). CPCV does the same per path (cpcv.py:229-240, groups as folds).
`discovery.reading_rule` (discovery.py:106) names the rule in prose; `DiscoveryConfig` ~discovery.py:141;
`registry.n_trials_effective` registry.py:160.

**Reading taken (inner folds).** "Inner cross-fitted p of the training side" = the out-of-fold p_raw of the folds
earlier than the test fold — exactly the set the calibrator already uses. Those rows were predicted by models that never
saw them, so no nested CV is needed (≈5× the fits for nothing). The cut is label-free: a quantile of p, not of outcomes.

## Design

1. `pipeline.fold_cuts(p_raw, folds, q=2/3) -> np.ndarray`: one cut per fold, `np.quantile(p_raw[prior], q)` on raw p;
   NaN when `len(prior) < MIN_CALIBRATION_ROWS`, so that fold takes no trade (as the calibrator rule today). The
   calibrator is monotone, so rank order is unchanged and cut and test p are compared in raw space; calibrated p is still
   produced and reported.
2. `pipeline.select(p_raw, p_cal, cuts_or_thresholds, rule)`: rule `break_even_margin` = today's `p_cal > threshold`,
   byte-identical; rule `tercile_inner` = `p_raw_test >= cut_k` (`>=` as pre-registered).
3. **Break-even is not a floor under `tercile_inner`** (a floor would restore the ~0.9% retention that voids the 15m
   power claim; O-4's own test is net mean R > 0 on ≥ 1,000 trades). Report `share_taken_below_breakeven` as a
   diagnostic only. Never tune the quantile or a floor to reach a trade count.
4. Call sites: `_walk_forward` (pipeline.py:311-313) and `run_cpcv` (cpcv.py:229-240, groups before g as `prior`),
   both with a `threshold_rule` argument defaulting to `break_even_margin`.
5. `DiscoveryConfig.threshold_rule: Literal["break_even_margin", "tercile_inner"] = "break_even_margin"`;
   `reading_rule(survivor_unit, threshold_rule)` appends a clause only for tercile, so the default string is unchanged
   (the director compares `cited_rule == pre["reading_rule"]`, director.py:677).

**Registry fields per trial** (in `results.discovery` and the pre-registration plan): `threshold_rule`, `tercile_q`,
`margin` (null for tercile), `fold_cuts`, `retention` (taken/scored per fold and overall),
`share_taken_below_breakeven`. K unchanged: the rule is one fixed pre-registered choice, not a searched axis; reading the
same candidates under the other rule is a second registry trial and pays through `n_trials_effective`.

## Failing tests first (`tests/test_tercile_cut.py`)

`test_the_cut_for_a_fold_is_the_two_thirds_quantile_of_earlier_folds_p_only`;
`test_changing_every_test_fold_row_p_and_label_leaves_that_folds_cut_unchanged` (leakage; also perturb later folds);
`test_cuts_are_identical_across_two_runs_and_row_order_shuffles_of_the_prior_set` (determinism);
`test_a_candidate_equal_to_the_cut_is_taken_and_one_ulp_below_is_not`;
`test_a_fold_with_fewer_than_50_prior_rows_has_nan_cut_and_takes_no_trade` (fail closed);
`test_nan_p_raw_rows_are_never_taken_and_do_not_enter_the_quantile`;
`test_the_cpcv_path_cut_uses_only_groups_before_g_on_that_path`;
`test_default_rule_results_are_unchanged_on_a_golden_frame` (golden captured from current main);
`test_default_reading_rule_string_is_byte_identical_and_tercile_adds_a_clause`;
`test_threshold_rule_and_fold_cuts_are_recorded_on_the_registry_row` (also after a registry reload);
`test_a_second_reading_of_the_same_candidates_under_the_other_rule_is_a_new_trial_in_n_trials_effective`;
`test_no_in_sample_training_p_reaches_the_cut` (spy on the model so a train-row prediction fails it).

**Must not change.** `break_even_margin` behaviour and results; existing registry rows (no retroactive re-reads);
RiskGate and execution; the 40-feature cap; `asof_join` / `epoch_ns` (untouched); the O-5/O-6/O-8/O-10 rule. No secrets.

**Blast radius.** pipeline.py, cpcv.py, discovery.py, registry.py (field recording only); new test file; TRACEABILITY
E-2 row ("Reading taken" above); HANDOFF fragment; pre-registration amendment (O-4/O-5/O-6 name the rule; cut-spec pins
recorded before the candidate count). Two PRs: (a) pipeline/cpcv functions + tests; (b) discovery config,
`reading_rule`, registry fields, docs.

**Risks.** (1) CPCV leakage: prior groups' p_raw on a path can come from splits trained on group g (combinatorial
paths) — already true for the calibrator; the cut is label-free, but the quant-reviewer rules whether a purged variant
(path-prior p only from splits that excluded g) is required. (2) A raw-p cut with no floor can take trades below
break-even (diagnostic reported). (3) Retention fixed at ~33% per fold, so power depends on the candidate count (the
pre-registration conditions on it). (4) Prose drift of `reading_rule` breaks the director's citation check (byte-identical
test).

**Reviews.** quant-reviewer on this plan (risk 1 and the no-floor reading) before the build; then implementer,
test-engineer, quant-reviewer and code-reviewer on the code; security before push. **Owner-only decisions:** none.

## Conditions from the quant plan review (APPROVE-WITH-CONDITIONS, 8753ad6, 2026-10-17)

Rulings: (1) CPCV path leakage through the quantile is second-order (label-free scalar; the calibrator has the larger
exposure; CPCV is a veto only, ADR 0003) — no purged variant; record in the E-2 row. (2) No floor is sound (below-break-even
trades bias the mean down); but live trading keeps break-even + 0.02, so a tercile result does not carry to live. (3)
Earlier folds' out-of-fold p_raw meets "never in-sample training p". (4) K accounting correct; **`q` is part of the
rule — any other `q` is a new trial.** (5) `>=` and the NaN cut are correct but must be guarded explicitly.

Binding on the build:
1. **Prior set = earlier folds' test rows ∩ fold k's `train_idx`** (on 15m the 24-month rolling window would otherwise
   let "prior" reach past the training window and skip the first 24 months) — or amend the pre-registration text to
   "all earlier out-of-fold rows"; either way the text is committed before O-4's candidate count is known.
2. `fold_cuts` raises if fold test sets overlap (specialist overrides allow `step_months < test_months`);
   `test_overlapping_test_folds_raise`.
3. For `tercile_inner`, **replace** the "(cross-fitted, break-even + margin)" clause rather than append; the default
   string stays byte-identical; add "promotion needs a separate trial under the live rule".
4. Pin `np.quantile(..., method="linear")`, recorded as `tercile_method`.
5. Strike "retention fixed at ~33%" (each fold's raw-p scale differs); record actual retention, never read it.
6. Point-in-time truncation test end to end through `_walk_forward`: labels cut at fold k's `test_start` vs the full
   set give identical `fold_cuts[:k+1]`.
7. Add `test_cpcv_cut_ignores_nan_filled_rows` (quantile from `p_raw` BEFORE `nan_to_num(..., nan=0.5)` at
   cpcv.py:239; only finite rows count toward the 50; guard with `np.isfinite(cut)`) and
   `test_threshold_rule_change_after_preregistration_is_refused_by_director`.

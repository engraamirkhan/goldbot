- H-02/H-07 per-fold coverage mask (BACKLOG 82, implementer, 2026-10-17; H-02 amendment 2026-10-17 in
  docs/research/preregistration-2027Q1.md). H-02 can now pre-register
  `DiscoveryConfig.full_coverage_columns: ["macro_spx_dd20"]`; the research-director pins it in the quarter spec
  (not pinned here).
  - Rule: a named column is NaN in both the training and the test rows of any fold whose training rows do not all
    carry a finite value (`pipeline.uncovered_columns`, from the fold's `train_idx` only, never its test rows).
    Fully covered folds are untouched.
  - Applied in the discovery's fold selection (`select_in_fold` sees the masked frame) and in the walk-forward fit and
    predict (`pipeline._walk_forward(full_coverage_columns=)`). `metrics.full_coverage` records the masked columns
    per fold. A named column the frame lacks is refused before any selection (no silent no-op on a misspelling).
  - Default unchanged: an empty field is left out of the dump, so every existing config hash holds; the walk-forward
    and CPCV golden (`tests/data/tercile_default_golden.json`) is byte-identical and the default metrics carry no new
    key. Frozen Q4 readings: no delta (none set the field).
  - Not wired: CPCV (`cpcv.run_cpcv`) and the pooled path, which no H-02 run uses. Watch: `eligible_columns` needs a
    column populated on > 80% of the candidates, so on a long window `macro_spx_dd20` may not enter the pool at all.
  - Tests: `tests/test_full_coverage_mask.py`
    (`test_spx_dd20_is_nan_in_train_and_test_for_folds_without_full_coverage` and 7 more). Not changed: RiskGate,
    execution, live thresholds, the trial budget.

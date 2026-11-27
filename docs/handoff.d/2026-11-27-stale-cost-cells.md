- 2026-11-27 refit cost cells (aux.db `exploration_cost_cells`) expire: a cell fitted more than 14 days before the
  label's slot (`daily_review.CELL_MAX_AGE`; exactly 14 d is still fresh) no longer raises the r1 replay cost.
  - The label falls back to the broker's nightly measured table or the settings prior; `cost_model_version` records
    `+cells-stale:<newest stale day>` (no migration). Vantage (replay-only since PR #505, newest cells 11-06/07) is
    the case this fixes. As-of read unchanged (`fitted_ns <= slot`).
  - The one cross-broker label read, the autodisc discovery priors (`read_demo_obs`), drops stale-flagged labels
    (`exploration_data.drop_stale_cost_labels`). Per-broker learners are unchanged. Labels written before this
    change carry no flag and are not relabelled.

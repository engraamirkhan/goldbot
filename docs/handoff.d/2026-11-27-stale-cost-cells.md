- 2026-11-27 refit cost cells (aux.db `exploration_cost_cells`) expire: a cell whose fills end more than 14 days
  before the label's slot (`slot - through_ns`, `daily_review.CELL_MAX_AGE`; exactly 14 d is still fresh, and a late
  fit of old fills is still stale) no longer raises the r1 replay cost.
  - The label falls back to the broker's nightly measured table or the settings prior. `cost_model_version` records
    `+cells-stale:<newest stale day>` only when a stale cell would have raised the cost actually charged (no
    migration). Vantage (replay-only since PR #505, newest cells 11-06/07) is the case this fixes. As-of read
    unchanged (`fitted_ns <= slot`).
  - Known one-directional bias for per-broker reads: a label whose dearer stale cell was set aside is understated by
    (stale cell - charged) per leg, about $0.1-0.3 a leg, a few hundredths of R. Per-broker learners keep those
    labels; the tag lets a reader drop or adjust them.
  - The one cross-broker label read, the autodisc discovery priors (`read_demo_obs`), drops tagged labels
    (`exploration_data.drop_stale_cost_labels`). Labels written before this change carry no tag and are not
    relabelled. r2 versions inherit the tag, which describes r1's cost. The table's own age: backlog 275.

- 2026-11-27 refit cost cells (aux.db `exploration_cost_cells`) age: a cell whose fills end more than 14 days before
  the label's slot (`slot - through_ns`, `daily_review.CELL_MAX_AGE`; exactly 14 d is still fresh, and a late fit of
  old fills is still stale) is stale.
  - A stale cell keeps charging its broker's last measurement (H-60 "never credited": a host that stopped filling has
    not shown cheaper slippage). When it sets the charge, `cost_model_version` records `+cells-stale:<newest stale
    day>` and `effective_utc` moves to that cell's fitted time (no migration). Vantage (replay-only since PR #505,
    newest cells 11-06/07) is the case this covers.
  - The bias is conservative only: a stale cell can raise a label's cost, never lower it.
  - The one cross-broker label read, the autodisc discovery priors (`read_demo_obs`), drops tagged labels
    (`exploration_data.drop_stale_cost_labels`), so pooled priors use only freshly measured cost bases. Per-broker
    learners keep them. Labels written before this change carry no tag and are not relabelled; r2 versions inherit
    the tag, which describes r1's cost.
  - Point in time: a cell fitted after the slot, or resting on fills after the slot (`through_ns > slot`), is never
    read. Same-day merged rows can still hide an earlier fit: backlog 276 (append-only rows). The table's own age:
    backlog 275.

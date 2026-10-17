- Discovery group-size check before the pre-registration row; context columns counted (BACKLOG 81, implementer,
  2026-10-17; TRACEABILITY M49).
  - `discovery_pass` now builds the decision frame (prices only, no label), maps every frame column to its group,
    context columns included, and runs `check_group_sizes` BEFORE `reg.preregister`. A refused config writes no
    registry row and leaves `n_trials_effective` unchanged. The pool is a subset of the frame's columns, so the
    run's map is the frame map restricted to the pool (same per-column groups as before).
  - O-4's pinned splitting rule is `DiscoveryConfig.context_groups: per_timeframe`: every h1_/h4_/d1_/w1_ column
    goes to `ctx_<tf>` across families. Default `join` keeps the old rule and is left out of the dump, so every
    existing config hash and group map is unchanged. New families (`liquidity`, `activity`) group by family with no
    code change.
  - On `f-079deacc61` at 15m: 14 groups (11 families + `ctx_h1`/`ctx_h4`/`ctx_d1` at 26 columns each), the largest
    `structure` at 38. With `join` the check refuses on `trend` (72) and `volatility` (53).
  - Owed by the research-director: write `context_groups: per_timeframe` into O-4's pinned dump and restate K_eff
    (14 on `f-079deacc61`, about 16 once `liquidity` and `activity` merge). K formula unchanged.

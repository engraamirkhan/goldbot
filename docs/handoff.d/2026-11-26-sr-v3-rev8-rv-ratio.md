- 2026-11-26 sr-v3 card rev 8 (quant ruling, label-blind, before any sr-v3 label read): `rv_ratio` replaces
  `atr_ratio_14_100` at core-12 position 4, at every tf; `atr_ratio_14_100` stays in P30. K 0.
  - Code: `dense_labels.CORE12`, so `COLUMN_SETS` core-6 and core-12. The trainer's `sr_v3.CORE_12` follows in PR #488.
  - Counts re-run once and taken as they come (`config/data/sufficiency.yaml`, docs/data/sufficiency.md): gold learn
    core-12 T2 n_eff 5,233 · 3,913 · 2,854 · 1,852 · 1,331 · – · 209 · 0 · 0 (re-counted at the main merge
    with R-a, which admits rv_ratio's B lookback months). Gold 1d/1w stay NOT STARTED (data).
  - No label value was read.
  - This is the later merge behind `price_action_x6.X6_MERGED_UTC`: the one-line follow-up sets it to this PR's
    merge commit committer time (UTC), the later of it and #484's (quant sequencing on #482).

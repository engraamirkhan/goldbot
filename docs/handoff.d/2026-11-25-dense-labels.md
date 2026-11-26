- 2026-11-26 D-5 dense label factory dl-v1 (`goldbot/research/dense_labels.py`, ADR 0104; sr-v3 card 2.1/2.2; quant
  C5/C6 on #462 and C-1..C-3 on #472).
  - Labels: every fs-v1 row gets 16 heads (both sides × T0-T7). Entry is strictly after `decision_utc`, barriers are
    side-aware, and the gross label is clipped at ±10 R with the raw value kept.
  - Cost: LABEL_COST_COLS are persisted. `net_r` = decision_cost.cost_x plus a per-cell ub95 rt_bp, never the
    realised leg.
  - Weights and n_eff: uniqueness is outcome-blind. Phase rows weigh 1/P. n_eff follows card 2.2.
  - Folds: purge via `feature_store.purge`.
  - Store and reads:
    - Skeleton and label values live in separate files.
    - Reads take only the partition of the data key.
    - `load_labels` needs tiers and a column set, refuses confirm/test_old without a booked read id, and stays sealed
      until ADR 0085 A4 records the tiers sha.
    - The tier check reads the column set's registry lookback, plus the d1 context.
  - Four registry lookbacks were raised to their 1 %-weight EWM horizon (atr 459, moving_averages 461,
    microstructure 63, gaps 63).
  - Counts after tier survival, T2, phase 0, D-1 served tiers (ff653ea7). Gold P30 is 0 because d1_slope_ema20's
    645-day lookback always reaches a C month before 2018.

    | Unit (window) | Column set | 1m | 5m | 15m | 30m | 1h | 4h | 1d | 1w |
    |---|---|---|---|---|---|---|---|---|---|
    | gold 2010-17 | core-6 = core-12 | 5,231 | 3,889 | 2,784 | 1,758 | 1,184 | 169 | 0 | 0 |
    | gold 2010-17 | P30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
    | gold 2003-09 (sealed) | core-6 | 159 | 108 | 65 | 26 | 0 | 0 | 0 | 0 |
    | EURUSD 2010-17 | core-6 = core-12 | 6,997 | 5,254 | 3,932 | 2,640 | 2,021 | 455 | 69 | 0 |
    | EURUSD 2010-17 | P30 | 2,505 | 1,885 | 1,426 | 972 | 770 | 203 | 8 | 0 |

  - Size: 59.7 M heads for gold learn, 43.8 M for gold test_old, 69.3 M for EURUSD.
  - Wall time: gold 39 min, EURUSD 19 min, on 4 workers, including the fs-v1 rebuild. No label value or statistic was
    read.

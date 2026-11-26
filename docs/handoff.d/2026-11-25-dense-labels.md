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
  - Lookbacks: each fs-v1 column declares its own lookback in the registry (`COLUMN_LOOKBACKS`, per tf where it
    differs). A truncation test proves every one (290 cases). The feature-level lookbacks were raised to their EWM
    horizons. Both changes are additive; the engine reads neither.
  - Counts after tier survival, T2, phase 0, D-1 served tiers (ff653ea7):

    | Unit (window) | Column set | 1m | 5m | 15m | 30m | 1h | 4h | 1d | 1w |
    |---|---|---|---|---|---|---|---|---|---|
    | gold 2010-17 | core-6 = core-12 | 5,226 | 3,847 | 2,708 | 1,644 | 1,068 | 123 | 0 | 0 |
    | gold 2010-17 | P30 | 2,316 | 1,743 | 1,317 | 896 | 711 | 123 | 0 | 0 |
    | gold 2003-09 (sealed) | core-6 | 157 | 104 | 56 | 13 | 0 | 0 | 0 | 0 |
    | EURUSD 2010-17 | core-6 = core-12 | 6,996 | 5,239 | 3,907 | 2,607 | 1,968 | 422 | 36 | 0 |
    | EURUSD 2010-17 | P30 | 5,753 | 4,331 | 3,278 | 2,235 | 1,769 | 422 | 36 | 0 |

  - Units with no usable rows are NOT STARTED (data), per card 2.3.
  - No label value or statistic was read.

- 2026-11-26 D-5 dense label factory dl-v1 (`goldbot/research/dense_labels.py`, ADR 0104; sr-v3 card 2.1/2.2; quant
  C5/C6 on #462).
  - Labels: every fs-v1 row gets 16 heads (both sides × T0-T7). Entry is strictly after `decision_utc`. Barriers are
    side-aware, d0 = ATR14 floored at 1.5 × the session-median 1m TR (h_ref = H until D-4), and the gross label is
    clipped at ±10 R with the raw value kept.
  - Cost: LABEL_COST_COLS are persisted. `net_r` = decision_cost.cost_x plus a per-cell ub95 rt_bp, never the
    realised leg.
  - Weights and n_eff: uniqueness is outcome-blind (one event per bar on its horizon). Phase rows weigh 1/P. n_eff
    follows card 2.2.
  - Folds and reads: purge via `feature_store.purge` with embargo max(1 d, H, lookback). `load_labels` refuses the
    holdout, phase != 0 at judge, a read without tiers and a tiers source mismatch. Every store is sealed until ADR
    0085 A4 records the tiers sha (`RECORDED_TIERS_SHA256`).
  - Built 2026-11-26 with 4 workers and the D-1 served tiers (ff653ea7):
    - gold 2010-17: 59.7 M heads. T2 tiered n_eff: 1m 5,234 · 5m 3,932 · 15m 2,956 · 30m 1,990 · 1h 1,531 · 4h 335 ·
      1d 79 · 1w 0.
    - gold 2003-09: 43.8 M heads, sealed, counts only.
    - EURUSD 2010-17: 69.3 M heads. T2 tiered n_eff: 6,998 · 5,267 · 3,983 · 2,709 · 2,130 · 545 · 220 · 61.
    - Wall time: gold 55 min, EURUSD 44 min. The cache is 9.2 GB, local only. No label value or statistic was read.

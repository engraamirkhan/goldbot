# S0 random-control null audit (H-60 matrix, run 40191721046)

Result: no bug. The "+0.037R gross" was a misread of `priors.json`: its `mean_r` is the prior, 0.5 x mean net R
(`scripts/aggregate_arm_priors.py`), so -0.0397 is half of net -0.0793, not net.

OOS 2019-2025 S0 trades (n = 130,387), from the per-cell parquet artifacts:

| Quantity | Value |
|---|---|
| gross R mean | -0.0026 (se 0.0019, so 0 within noise) |
| net R mean (overshoot 0) | -0.0793 |
| round-trip cost | 0.0767 R ($0.23 / $3 stop) |
| 0.5 x net (the stored prior) | -0.0397 |
| long share | 49.85% (gross long -0.0015, short -0.0037) |
| exits | time 59.3%, stop 20.5%, target 20.3% (no tie bias towards the target) |

All 208,456 trades: gross -0.0014 (se 0.0014); per 2-year block -0.0058 to +0.0010. Side balance and the seeded hash
show no long-bias x uptrend effect; stop and target hit rates are symmetric. The control is null before cost.

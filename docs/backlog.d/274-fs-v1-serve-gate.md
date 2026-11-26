---
number: 274
rank: 97
title: 'fs-v1 serve gate: promotion of fs-v1-trained models calls serve_ready'
stage: 'Learning (promotion) · ADR 0103'
size: S
status: 'proposed (owner: implementer; due before the first sr-v3 promotion)'
---
- **274. fs-v1 serve gate: promotion of fs-v1-trained models calls serve_ready.** Value: a model trained on fs-v1 history rows at 4h/1d/1w, or reading d1_* columns at any tf, would be served inputs that differ from its training rows (deployed 90k-minute memory: d1_atr_ratio_14_100 and d1_rv_ratio NaN, d1_slope_ema20 short-window; at 4h also tsmom_score, atr_ratio_14_100, vol_tercile). ADR 0103 (quant C4) bars promotion until the deployed path matches. Acceptance: promotion of any model whose training rows come from fs-v1 calls `feature_store.serve_ready(tf, columns, parity_json)` and refuses on False; test: a 4h fs-v1 model with no parity report is not promoted. Owner: implementer. Due: before the first sr-v3 promotion.

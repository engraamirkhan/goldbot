---
number: 153
rank: 57
title: Seven-night rollover counting for crypto books
stage: 'engine + research costs (follow-up of fix/swap-mode-5-6, quant review)'
size: S
status: 'done (implemented 2026-11-12, R130; the broker reconciliation after the first weekend BTCUSD hold is BACKLOG 175) · accountable: implementer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **153. Seven-night rollover counting for crypto books.** Value: a crypto CFD (Vantage BTCUSD) trades and is charged swap every night, weekends included, while `rollover_nights` (goldbot/labels/triple_barrier.py) counts five weekday rollovers with the triple day standing in for the weekend, so the RiskGate's daily projection and the research CostImputer (goldbot/ops/cost_imputation.py) can understate a weekend hold. Acceptance, tests first: a SwapSpec (or instrument) flag for a seven-night schedule; `rollover_nights` counts every server-midnight rollover for it (no triple day) and is unchanged for every other book; the CostImputer uses the same count; after the first weekend BTCUSD hold on vantage-demo, the broker's actual swap charges are reconciled against projection x nights and the result recorded (finance-auditor), and the schedule kept or corrected from that evidence.

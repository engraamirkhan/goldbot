---
number: 36
rank: 9
title: Round-trip costs in the daily £ projection (review LOW-6)
stage: Capital and drawdown management · RUN
size: S
status: 'built (implementer, 2026-10-16); needs trading-safety + quant review · accountable: implementer · due 2026-10-30'
---
- **36. Costs in the daily projection (trading-safety review LOW-6).** Value: a full stop-out also pays the
  spread, commission and (overnight) swap, about £0.25 on a 0.01 lot, so the projection understates a full loss by
  ~3% at £1,000 (RUN). Acceptance: the projected risk adds the round-trip cost in account currency from the cost
  table (`_cost_atr` x ATR x lots x contract x fx); boundary test. Trading-safety review.
  Sweep 2026-10-16 (product owner): accountable `implementer`, due 2026-10-30; trading-safety review. Acceptance as above.

---
number: 36
title: Round-trip costs in the daily £ projection (review LOW-6)
stage: Capital and drawdown management · RUN
size: S
status: 'merged on the integration branch 2026-10-16 (cca7219 + trading-safety MEDIUM fix 0bfacbf; quant, code and trading-safety reviews done per HANDOFF); not in PR #79, ships in the next PR · accountable: release-manager · due 2026-10-23'
---
- **36. Costs in the daily projection (trading-safety review LOW-6).** Value: a full stop-out also pays the
  spread, commission and (overnight) swap, about £0.25 on a 0.01 lot, so the projection understates a full loss by
  ~3% at £1,000 (RUN). Acceptance: the projected risk adds the round-trip cost in account currency from the cost
  table (`_cost_atr` x ATR x lots x contract x fx); boundary test. Trading-safety review.
  Sweep 2026-10-16 (product owner): accountable `implementer`, due 2026-10-30; trading-safety review. Acceptance as above.

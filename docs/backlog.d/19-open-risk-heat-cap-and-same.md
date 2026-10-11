---
number: 19
rank: 22
title: Open-risk (heat) cap and same-direction stacking (G-4)
stage: Capital and drawdown management · RUN
size: S
status: 'proposed (owner: value)'
---
- **19 (G-4).** Sum of open risk in R per account and combined ≤ the configured cap. Same-side positions of
  different agents count as one bet. Gate test on each side of the cap.
  **Built 2026-10-11, off by default (TRACEABILITY R38):** `risk.heat: {enabled: false, max_open_risk: 0.02,
  max_same_direction: 2}`, per account: `heat_cap` when the open positions' remaining stop risk (account currency) plus
  this trade's exceeds `max_open_risk` x equity; `same_direction_cap` past N same-side positions on the account,
  whichever agent opened them; a position without SL or conversion refuses (`open_risk_unknown`). Not done: the
  combined (cross-account) sum and count, which needs the supervisor to publish them. Owner: set the values and
  enable. Needs trading-safety review.

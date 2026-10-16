---
number: 35
title: Open positions' remaining stop risk in the daily £ projection (review MEDIUM-4)
stage: Capital and drawdown management · RUN
size: S
status: 'built, off by default (PR #75, main a52b389; fefb759); parked: owner adopts or not'
---
- **35. Open positions' remaining stop risk in the daily projection (trading-safety review MEDIUM-4).** Value: the
  absolute daily limit (29) projects the loss so far (equity, so open P&L included) plus the NEW trade's stop risk,
  but not what the positions already open could still lose down to their stops, so two open trades can each pass
  and together carry the day past £50 (Capital management · RUN). Acceptance: the projection adds, per open
  position, max(0, loss from the current price to its stop) x lots x contract x fx; a test with one open position
  whose remaining stop risk plus the new trade's exceeds the limit is refused; exits never consult it. Relates to
  19 (heat cap); trading-safety review.
  **Built 2026-10-11, off by default (TRACEABILITY R38):** `risk.project_open_risk: false`; when true and the
  account has `daily_loss_limit_abs`, the projection is loss so far + open risk + this trade's risk; unknown open risk
  refuses (`open_risk_unknown`). The engine publishes `open_risk` and `open_risk_positions` in engine_<account>.json
  (dashboard budget: ui-ux follow-up). Owner: adopt or not. Needs trading-safety review.
  Sweep 2026-10-16 (product owner): Built, off by default (PR #75, main a52b389; fefb759); parked: owner adopts or not.

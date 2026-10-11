---
number: 35
rank: 34
title: Open positions' remaining stop risk in the daily £ projection (review MEDIUM-4)
stage: Capital and drawdown management · RUN
size: S
status: proposed (after 29; trading-safety review)
---
- **35. Open positions' remaining stop risk in the daily projection (trading-safety review MEDIUM-4).** Value: the
  absolute daily limit (29) projects the loss so far (equity, so open P&L included) plus the NEW trade's stop risk,
  but not what the positions already open could still lose down to their stops, so two open trades can each pass
  and together carry the day past £50 (Capital management · RUN). Acceptance: the projection adds, per open
  position, max(0, loss from the current price to its stop) x lots x contract x fx; a test with one open position
  whose remaining stop risk plus the new trade's exceeds the limit is refused; exits never consult it. Relates to
  19 (heat cap); trading-safety review.

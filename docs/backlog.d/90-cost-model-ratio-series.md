---
number: 90
rank: 26
title: 20-day measured cost/model series for the adaptive limits
stage: Risk (limits) · PROTECT
size: S
status: 'proposed (raised by the ADR 0031 lane B implementer, 2026-10-18) · accountable: implementer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **90. 20-day measured cost/model series for the adaptive limits.** Value: ADR 0031's frozen tighten rule "20-day measured cost/model > 1.25 → floors, 1.10–1.25 → one step down" has no input today: no series of measured round-trip cost against the cost the research model charged exists, so `goldbot/ops/limits_job.py` passes `cost_ratio_20d = None` and the rule cannot fire (the drawdown, loss-streak, stage and statistic rules still tighten). Acceptance: a per-account daily measure over the last 20 risk days of demo fills (spread at the fill, slippage against the requested price, commission and swap, USD per oz) divided by what the research labels charge for the same trades (bar spread plus the cost table's extra cost), None below a pre-registered minimum of fills; `limits_job` passes it; a test shows a ratio of 1.3 gives the floors and 1.2 one step down through the job; quant-reviewer signs off the definition before it is wired (it is an input to a frozen rule, so its definition is fixed before any replay).
- Until then the RUNBOOK says the cost rule is inert, and the nightly job's result row carries no cost ratio.

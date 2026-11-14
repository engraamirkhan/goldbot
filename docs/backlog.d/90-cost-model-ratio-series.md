---
number: 90
rank: 10
title: 20-day measured cost/model series for the adaptive limits
stage: Risk (limits) · PROTECT
size: S
status: 'done 2026-11-14 (R150): quant-reviewer APPROVED the definition 2026-11-14 (only tightens; 20 UTC weekdays by exit, cut at 00:00 UTC of the decision day; USD/oz both sides; label bars over 9 days old unmeasured; over 20% unmeasured -> None); its non-blocking suggestion, a conservative bound pricing each unmeasured fill at the window''s worst fill, is logged and in the job row, never a limit input (test_the_conservative_bound_is_reported_but_never_moves_a_limit); replay follow-up BACKLOG 255 done'
---
- **90. 20-day measured cost/model series for the adaptive limits.** Value: ADR 0031's frozen tighten rule "20-day measured cost/model > 1.25 → floors, 1.10–1.25 → one step down" has no input today: no series of measured round-trip cost against the cost the research model charged exists, so `goldbot/ops/limits_job.py` passes `cost_ratio_20d = None` and the rule cannot fire (the drawdown, loss-streak, stage and statistic rules still tighten). Acceptance: a per-account daily measure over the last 20 risk days of demo fills (spread at the fill, slippage against the requested price, commission and swap, USD per oz) divided by what the research labels charge for the same trades (bar spread plus the cost table's extra cost), None below a pre-registered minimum of fills; `limits_job` passes it; a test shows a ratio of 1.3 gives the floors and 1.2 one step down through the job; quant-reviewer signs off the definition before it is wired (it is an input to a frozen rule, so its definition is fixed before any replay).

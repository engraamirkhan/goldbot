---
number: 26
title: Milestone ladder and monthly north-star / % / R report (ADR 0004)
stage: Performance review, capital growth · RUN
size: S–M
status: 'backend done (PR #71, main 423d2b1; d893f60); card, Telegram line and capture KPI parked: no trades until 1'
---
- **26. Milestone ladder and monthly north-star report.** Value: reports progress the way ADR 0004 decided:
  monthly averages and distributions, never a daily quota (Performance review · RUN). Acceptance:
  - A monthly report (job + dashboard card + one Telegram line) with time-weighted % return net of deposits and
    withdrawals, net R, R/trade with trade count and 90% interval, max drawdown %, all by timeframe; golden test on a
    fixture with a mid-month deposit (the deposit is not counted as return).
  - The milestone ladder (equity at which the minimum lot fits 15m/1h/4h/1d at the 1% cap and at 0.5%) is computed
    from the sizing-feasibility report (item 22) at the current price and measured ATR, not hard-coded; test that a
    50% higher ATR raises every threshold by 50%.
  - North-star KPIs (ADR 0004 section 6): monthly average £/day and %, the daily P&L distribution (count of £50+
    days, best-day histogram in R and £), % of opportunity days captured (classifier-flagged days and days with
    range ≥ 1.5 × ATR(1d) on which the book made ≥ 1 R), max drawdown % and days lost to the daily cap. Golden test.
  - No per-day target, countdown or "behind target" wording in the report, card or digest (test on the rendered
    text): a missed £50 day never prompts more risk.
  - Reviews: code, ui-ux (screen and Telegram), quant (the R interval). Read-only: no trading-safety review.
  Depends on: 22.
  Sweep 2026-10-16 (product owner): Backend done (PR #71, main 423d2b1; d893f60); card, Telegram line and capture KPI parked: no trades until 1.

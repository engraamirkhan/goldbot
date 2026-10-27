---
number: 116
rank: 39
title: Higher-timeframe context features joined point-in-time to intraday models
stage: Features · research
size: M
status: 'proposed (ADR 0046), after 105 · accountable: ml-researcher (features) with data-engineer · reviews: quant-reviewer, code-reviewer · due 2026-11-20'
---
- **116. Higher-timeframe context features joined point-in-time to intraday models.** Value: lets 15m/1h/4h models know where they stand in the bigger picture (position in the 52-week range, distance from the 200-day mean, 1-year realised-vol percentile, monthly trend sign, month-of-year) and lets intraday findings be checked against long-history regimes via daily proxies (ADR 0046). Acceptance: features built from item 105 only and joined with `store.asof_join` on `available_utc` (a daily value is never visible before its bar closes; a monthly average never before its publication date), with a leakage test that shifts the source and shows no change before availability; features count against the 40-feature cap; causal filters only (`docs/research/indicator-survey.md:181`); each use in a research slice (B, C, H) is a pre-registered registry trial counted in K; any daily-proxy regime check is reported as supporting evidence, never as an intraday verdict.

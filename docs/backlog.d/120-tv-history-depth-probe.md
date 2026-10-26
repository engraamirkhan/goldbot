---
number: 120
rank: 40
title: TradingView history depth per symbol and timeframe (local only)
stage: Data · research input
size: S
status: 'proposed · accountable: data-engineer · reviews: security-reviewer (cookie path)'
---
- **120. TradingView history depth per symbol and timeframe (local only).** Value: answers how far TradingView goes per symbol and timeframe within our plan's bar limit (settings tv_pull: 5,000 bars a request, max_pages_per_tf 5; ~11k bars measured on 15m/1h/4h). Held on the brain today (OANDA:XAUUSD, 2026-10-26): 1D from 2006-03-19 (5,336 bars, start of OANDA's series), 4h from 2020-01-01, 1h from 2025-01-01, 15m from 2026-04-30. Target: a read-only `tv-pull probe-depth` that loads one series per symbol/timeframe (TVC:GOLD, FX_IDC:XAUUSD, COMEX:GC1!, OANDA:XAUUSD; 1D, 1W, 1M, 4h, 1h) paging until history ends, records only first/last bar and count (no new store rows), within min_interval_s and max_requests_per_day; tests first; run once after review. Daily TVC/FX_IDC may reach the 1970s and feed item 105. Licence: TradingView terms (owner accepted the ToS risk for personal use, 2026-10-18): local only, never a release. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.

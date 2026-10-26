---
number: 105
rank: 38
title: Long-history daily/weekly/monthly gold store with splice checks
stage: 'Data · research'
size: M
status: 'proposed (ADR 0046) · accountable: data-engineer · reviews: quant-reviewer, code-reviewer · due 2026-11-09'
---
- **105. Long-history daily/weekly/monthly gold store with splice checks.** Value: gives research slices B (cycles), C (fib/wavelet) and H (combinations) about five decades of daily/monthly context and about 660 independent 30-day windows, without inventing intraday data (ADR 0046). Acceptance: one daily, weekly and monthly series each, with a per-row `source` column and recorded splice dates; day bars rebuilt to the 17:00 New York boundary from our 1m where we hold it (Dukascopy 2003+, TradingView OANDA 2006-03+); every splice passes the ADR 0046 overlap check (at least 1 year, daily log-return correlation at least 0.99, median absolute close difference within 10 bp) with the measured figures written to the data-coverage doc; futures only as roll-adjusted returns, never levels; monthly averages and fixes kept as their own series, never spliced into closes; pre-1971 excluded from return and vol statistics; `available_utc` on every row (bar close plus lag; publication date for monthly averages); only PDDL or confirmed CC BY data published to a release; the store refuses any frame marked `synthetic=True` outside `tests/` (unit test).

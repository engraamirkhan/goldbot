---
number: 108
rank: 41
title: Run broker-history-depth on icm-demo and vantage-demo
stage: 'Data · ops'
size: S
status: 'proposed · accountable: sre (runs it) · reviews: trading-safety (load on the live bridge)'
---
- **108. Run broker-history-depth on icm-demo and vantage-demo.** Value: the brokers' own history depth (icm-demo, vantage-demo) per timeframe for XAUUSD. The read-only CLI `goldbot run broker-history-depth <account> [--tf 1m,1h,1d]` (goldbot/ops/broker_depth.py, tests) calls only get_bars once per timeframe, capped at 500,000 bars. Acceptance: deployed with the next release; run in a quiet window (weekend) per account; table of account x timeframe -> first bar, with the terminal's "Max bars in chart" setting recorded (copy_rates_from_pos is bounded by it). Local data only. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.

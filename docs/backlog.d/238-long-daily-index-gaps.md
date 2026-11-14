---
number: 238
rank: 82
title: Fill or quarantine the US500/UK100 long daily holes 2011-2013
stage: 'Research data · long daily history'
size: S
status: 'done 2026-11-14 (branch fix/data-backlog-1114) · accountable: data-engineer · reviews: quant-reviewer'
---
- **238. Fill or quarantine the US500/UK100 long daily holes 2011-2013.** Value: found while measuring R145. `raw/daily/long/us500_daily_long.parquet` and `uk100_daily_long.parquet` have no rows from 2011-09-23 to 2012-01-18. From 2013-02-26 to 2013-05-22 they keep only a few rows (source `dukascopy_h1:*` around the holes). The old holiday inference read about 150 of these weekdays as holidays. The holes also starve every `long_US500`/`long_UK100` aux reader in the explore window. Acceptance: there is a coverage check per long file (weekdays without a row outside the published exchange calendar, as a dq_events warning per run). The holes are filled from a free source (Stooq or Yahoo index history, source column kept) or listed as known gaps. A test shows a hole is reported and not read as a holiday.
  **Done 2026-11-14.** `long_history.stitch` fills every hole of >= 10 missing weekdays from the next-ranked source (source kept, a `hole` overlap in the manifest); `coverage_gaps` lists weekday runs without a row that the exchange calendar does not publish as closures, and `scripts/long_daily_history.py` writes runs of >= 3 as dq_events warnings each run (tests/test_long_daily_holes.py: a hole is reported, Good Friday inside it is not). Rebuilt offline from the cache: US500, UK100, USTEC and JP225 had the same two dukascopy_h1 holes; filled with 140/140/140/145 Yahoo rows (^GSPC, ^FTSE, ^NDX, ^N225), every existing row unchanged; no gap of >= 10 weekdays left. Residual: US500 16 missing weekdays in 13 runs (one warned, 2013-10-14..16), UK100 7 in 6. Quant: the filled rows are cash-index closes, not the CFD, inside two windows.

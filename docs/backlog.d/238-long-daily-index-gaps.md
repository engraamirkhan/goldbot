---
number: 238
rank: 82
title: Fill or quarantine the US500/UK100 long daily holes 2011-2013
stage: 'Research data · long daily history'
size: S
status: 'proposed (follow-up of R145) · accountable: data-engineer · reviews: quant-reviewer'
---
- **238. Fill or quarantine the US500/UK100 long daily holes 2011-2013.** Value: found while measuring R145. `raw/daily/long/us500_daily_long.parquet` and `uk100_daily_long.parquet` have no rows from 2011-09-23 to 2012-01-18. From 2013-02-26 to 2013-05-22 they keep only a few rows (source `dukascopy_h1:*` around the holes). The old holiday inference read about 150 of these weekdays as holidays. The holes also starve every `long_US500`/`long_UK100` aux reader in the explore window. Acceptance: there is a coverage check per long file (weekdays without a row outside the published exchange calendar, as a dq_events warning per run). The holes are filled from a free source (Stooq or Yahoo index history, source column kept) or listed as known gaps. A test shows a hole is reported and not read as a holiday.

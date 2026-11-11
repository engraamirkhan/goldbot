---
number: 162
rank: 65
title: Deterministic row order for rows sharing a ts_utc in Store.read
stage: 'data (found while doing backlog 156, 2026-11-11)'
size: S
status: 'proposed · accountable: data-engineer · reviews: code-reviewer, quant-reviewer'
---
- **162. Deterministic row order for rows sharing a ts_utc in Store.read.** Value: `Store.read` and the store's `ORDER BY ts_utc` sort on ts_utc only and duckdb scans in parallel, so rows that share a ts_utc come back in a different order run to run on either duckdb major (measured 2026-11-11 on the Mac's local store with 1.5.6 twice: 25-29 % of `positioning` rows and 37 % of `dq_events` rows change position; same rows and dtypes). A consumer that takes `.iloc[-1]`, `drop_duplicates(keep="last")` or a first match per ts (several series per COT date, several dq events per minute) can differ between two replays of the same data. Bar tables tie only when a read spans more than one source or symbol (`source=None` or `symbol=None`). Acceptance, tests first: `Store.read` orders by ts_utc then the table's key columns (`KEY_COLUMNS`, else every column), a test reads a table with ties ten times and gets one order, the change is checked for read time on a full year of 1m bars (no worse than 10 %), and the first/last-per-key picks after a sort on `available_utc` use a stable sort (`kind="stable"`) at goldbot/data/macro.py:187 and goldbot/features/positioning.py:441 (quant, 2026-11-11). Fix before any replay-equality or golden check that reads dq_events, positioning or multi-source bars.

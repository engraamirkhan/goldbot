- 2026-11-19 fix (trading safety, incident 2026-11-18/19 on a multi-book demo account): `books.QuoteFeed.refresh`
  took `since` from the OLDEST latest tick of every book, so an index closed 21:59:59..01:00:01 UTC held it for three
  hours; the bridge's `ticks_many` (copy_ticks_from, 2,000 rows) then served gold's oldest ticks after the reopen and
  the engine re-read a gold quote up to 105 min old (software stops/trails/scale-outs ran on it; at 01:00 the catch-up
  replayed backdated ticks). Now `since` is never further back than the newest known tick (capped at the host clock)
  less `risk.stale_tick_seconds`, and a batch that is a backlog (>= `QUOTE_BACKLOG_ROWS` = half the bridge cap, newest
  tick older than the stale limit, or older than the held quote) is replaced by the terminal's newest quote
  (`last_tick`), so exits always run on the latest quote and are never gated. Brain-only: no bridge change, protocol
  v11 unchanged. New health check `quote_age:<account>` (NOTIFY_ON_WARN: told on Telegram): gold's book or a book
  holding a position with a quote older than the stale limit in its open session. Tests:
  `tests/test_quotefeed_since.py`, `tests/test_health_quote_age.py`.

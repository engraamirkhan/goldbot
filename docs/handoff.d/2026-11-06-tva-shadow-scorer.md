- TradingView author ideas shadow scorer (prereg-2026-10-28-tv-author-ideas.md section 6, amendment T-1; branch
  `feat/tv-ideas-shadow-scorer`): `goldbot/research/tv_ideas.py`, scheduler job `tva_scores` daily 23:35 UTC (after
  feed_reconcile and nightly_costs). It opens brain `state/tv_ideas.db` read-only (`mode=ro`) and scores each live
  gold idea's first-seen snapshot on the canonical broker's own 1m minutes (`account_bars_1m`) into aux.db
  `tva_scores` (migration 23): entry at the first minute at or after `available_utc` and R = ATR14 as of the prior
  server-day close, both through `asof_join`; the direction read (1.3(b)) for BOTH sides net of spread,
  `research_costs` extra and swap (drift and random-direction baselines at the author's timestamps), the 1.5x stress,
  a descriptive publish + 60 s capture read, and the levels read (1.2-1.3(a)) only where an admitted extractor gives
  entry, stop and T1 on spot. Holdout ideas are refused, back-catalogue ideas counted (`backfilled`), non-gold
  counted, futures direction-only. Edited = the text changed after first sight (versions), not the collector's
  `edited` flag (which counts "target reached" notes and would drop winners); edited live ideas stay
  decision-eligible (T-2 (7)). A final row is never rescored.
  `ADMITTED_EXTRACTORS` is EMPTY: T-1's one direction audit failed (2026-10-31), so every idea is `unlabelled` and
  counted; no LLM, no collector-parsed direction or levels. A's bias-template extractor needs a dated amendment and
  its outcome-blind audit first (research-director). The job reports counts and forward-read progress only (>= 100
  resolved ideas and >= 60 publish days per author), no running skill statistic. No order, RiskGate path, arm or
  feature. Smoke on the 11-06 brain dump: 17 live ideas, 8 holdout and 6 non-gold refused, the long/short reads equal
  the 11-06 scratch read on 10 of 12; the 2 that differ are Sunday ideas, whose 1-day horizon now runs from the fill
  (Monday open), not from the post.
  Quant review amendment T-2 (prereg section 8, adopted PD+PO 2026-11-06) is applied: a hole over 15 min in the hold
  (outside the daily break and weekly close, timed on NY+7 because the Athens broker clock is an hour off gold's 17:00
  New York break in the US/EU clock-change weeks; a hole on one listed CME holiday is exempt) -> `no_bars`, rescored
  nightly, final `no_bars_final` 5 weekdays after the horizon; R = mean TR of the 14 most recent whole server weekdays
  (>= 80% of the median minutes) among the last 20, else `no_bars`; decision-eligible only from the dated admission of the author's extractor
  (`Extractor.admitted_utc`, in its sha); seconds charts bucket I; no holdout minute read; minutes fetched only from
  the earliest still-scoreable idea. Operational note: the brain's ICM minutes start 2026-10-25, so live ideas before
  about 2026-11-13 stay `no_bars` (no 15 whole ATR days); none of them can be decision-eligible under T-2 (6) anyway.

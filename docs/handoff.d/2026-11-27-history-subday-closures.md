- 2026-11-27 history_gap: sub-day broker closures (vantage-demo 1h/4h refused since 10-26 by a 61-open-minute hole
  20:58-23:02 UTC, the DST-gap-week daily break, older than backfill_max_days so never re-pulled).
  - A clean broker-bar pull with no minute in a run of >= 15 minutes (an open one among them) covers that run, also
    inside a day that has minutes (`BrokerRates.closures`); a run the pull does not reach past (the live tail) never.
  - A closure one pull found is memory-only (trading-safety LOW: the terminal's local 1m history may hold an
    un-resynced disconnect hole). It is re-pulled 1 h later (or once it ended a day ago) and recorded in aux.db
    `history_closures` (migration aux 46, loaded at warm start) only when that second clean pull finds the same
    empty run; a second pull that finds minutes there un-marks it.
  - A hole wholly older than the fill floor that no pull covered, with <= `history_stale_gap_max_min` (240) open
    minutes, is a closure for the refusal, logged once; off when either setting is 0. Quant hardening: the old
    holes cleared inside one decision frame may not sum past that cap, or the frame is refused. Holes in the fill window and
    longer old holes still refuse. Needs trading-safety + quant review.
- 2026-11-27 session split reporting: the engine log and the `entry_silence:<account>` health reason show replay-only
  control candidates beside the edge ones ("session split from 0 edge of 0; 62 replay-only control"), so an account
  with no edge candidate no longer reads as dead. `exploration_budget.SplitCounts` / `split_counts`; the split itself
  is unchanged (edge candidates only).

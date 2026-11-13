- **1d/1w go-live blockers closed in code, still dark (BACKLOG 144, R149, ADR 0081; implementer).**
  `goldbot/engine/long_bars.py`: a Monday-Thursday 1d decision is sent at the first 15m close after 22:15 UTC and 15
  min after the broker's reopen (22:30 UTC EEST, 23:30 UTC EET); a 1w decision an hour after the Sunday open (23:15
  UTC EEST, 00:15 UTC EET); both on the bars through their decision close, the signal stamped at the send close
  (bracket from the send price), `decision_utc` the decision close. A decision up to 2 h (1d) / 24 h (1w) late is
  caught up once (a restart across the weekly open); later, or with no reopen, it is journalled
  `exploration_missed:<tf>`. aux.db migration 32 `long_tf_decisions` persists the last decision per arm before
  queueing. The rules' common filter is judged at a 1d/1w bar's entry (`RuleContext.filter_ts`; `common_filter_ok`
  byte-unchanged). `_trail_runners`/`score_twins` use `is_bar_close`. A 1d/1w Intent carries the swap of its whole
  hold (`Engine._swap_usd_per_oz(..., until)`); an unknown swap is refused by the gate. The guard still lists
  blockers 4 (BACKLOG 251), 5 ratification (252) and 7 (253); research parity of the filter is BACKLOG 254. Reviews
  owed: trading-safety-reviewer (send timing, catch-up, swap projection), quant-reviewer (ADR 0081), code-reviewer.

- 2026-11-13 (branch feat/news-orderflow-features; R139; plan item 8): two opt-in feature families for the
  feature-discovery screen, registered but in no default or live set (pipeline.OPT_IN_FEATURES; no trial run, K
  unchanged). `news_llm` (17 columns, goldbot/data/news_features.py + goldbot/features/news_llm.py): the collector's
  scored headlines as gold-signed sentiment, surprise, event types (fed/usd/geo/etf/cb) and counts over 1h/4h/24h with
  30-day z-scores, on a 15-minute availability grid; no new LLM call or spend. It is collect-only: no trial reads it
  until 12 months of forward data exist (quant review). `orderflow` (7 columns, goldbot/data/orderflow.py +
  goldbot/features/orderflow.py): quote-update-weighted return scores (`of_vw_retz_15m/60m`, `of_vw_run`: Dukascopy's
  tick_count is bid quote updates, not trades), spread dynamics and tick rate from 1m bars; a survivor must beat the
  plain return/momentum features. Research passes them with `research_pass.feature_ctx(news=..., orderflow_bars=...)`,
  which stamps `available_utc` so the pipeline's `lookahead_check` cuts them at the same point the features do.
- Point in time: the collector now stamps `written_utc` (its clock just before the append, after scoring) on every
  news row, and that is a headline's availability; older rows take the next poll's received_utc (the loop is
  sequential, so that poll started after the write), the last poll waits. Never the publication time; items first seen
  > 2 h after publication (backfills) and non-`rss` rows never count; a window with > 20 % of prefiltered headlines
  unscored by the budget, or 12 h with nothing available, reads NaN, and both masks apply before the z-scores.
  Order-flow values are stamped at each 1m bar's visible_at; a data gap > 5 min reads NaN. Tests seed future headlines
  and later minutes and require every earlier bar bit-identical, plus the pipeline's own lookahead_check.
- Quant review fixes (same branch): fill slippage dropped (pooled every account and symbol, endogenous, NaN before
  October 2026 = a date marker); of_imb/of_run renamed; write-time availability replaced the fixed 5-minute lag.
- Limits: the Mac holds no `news` table, so the history length must be read on the brain (`news_features.coverage`,
  BACKLOG 212). Dukascopy's ask-side volume is dropped at ingest (BACKLOG 213); MFE/MAE wait for lane F's fields
  (BACKLOG 214). Order flow costs ~2.3 s per million 1m bars per frame build. Follow-ups: BACKLOG 211-214.

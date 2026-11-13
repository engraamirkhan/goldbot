- 2026-11-13 (branch feat/news-orderflow-features; R139; plan item 8): two opt-in feature families for the
  feature-discovery screen, registered but in no default or live set (pipeline.OPT_IN_FEATURES; no trial run, K
  unchanged). `news_llm` (17 columns, goldbot/data/news_features.py + goldbot/features/news_llm.py): the collector's
  scored headlines as gold-signed sentiment, surprise, event types (fed/usd/geo/etf/cb) and counts over 1h/4h/24h with
  30-day z-scores, on a 15-minute availability grid; no new LLM call or spend. `orderflow` (8 columns,
  goldbot/data/orderflow.py + goldbot/features/orderflow.py): bulk-volume-classified tick-count imbalance, signed
  same-direction runs, spread dynamics and tick rate from 1m bars, plus mean fill slippage. Research passes them with
  `research_pass.feature_ctx(news=..., orderflow_bars=...)`, which stamps `available_utc` so the pipeline's
  `lookahead_check` cuts them at the same point the features do.
- Point in time: a headline's availability is the collector's received_utc + 5 min (the scoring poll), never the
  publication time; items first seen > 2 h after publication (backfills) and non-`rss` rows never count; 12 h with
  nothing received reads NaN (outage), not "no news". Order-flow values are stamped at each 1m bar's visible_at; a
  data gap > 5 min reads NaN. Bars read both at their close (visible_at) through `asof_join`. Tests seed future
  headlines and later minutes and require every earlier bar bit-identical, plus the pipeline's own lookahead_check.
- Limits: the news columns are forward-only (collector history only; NaN for every research year before it), and the
  Mac holds no `news` table, so the history length must be read on the brain (`news_features.coverage`, BACKLOG 212).
  Dukascopy's ask-side volume is dropped at ingest (BACKLOG 213); MFE/MAE skipped until lane F's fields reach the
  fills (BACKLOG 214). Order flow costs ~2.3 s per million 1m bars per frame build. Follow-ups: BACKLOG 211-214.

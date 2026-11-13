- 2026-11-13 (branch feat/news-rule-scorer; R144; BACKLOG 231-234; ADR 0079): the news collector no longer leaves
  headlines unscored without an API key. A free deterministic scorer, `rules-v1` (goldbot/data/news_rules.py: gold
  driver lexicon, negation, hedges, source weights), scores every prefiltered headline into the LLM's schema; it fills
  the score (`scorer` = rules-v1) whenever the LLM did not score the row, and its own `rule_*` columns always. LLM
  scores take precedence, both kept. A rule shock (confidence >= `news.rule_shock_min_confidence`, 0.8) fires the
  existing shock blackout: entries refused 30 min, exits untouched; during an LLM API error a rule shock is stored at
  once. The `news` health check fails when headlines arrive but none is scored in 24 h (`state/news_scoring.json`).
  News features read rule rows flagged (`rule_scored`, `news_rules_share_24h`; news_llm v3). Not done: the 392 stored
  rows stay unscored (no rewrite after written_utc); live precision unmeasured until BACKLOG 234.
- Review round 1 (same branch): rule shocks only for new events (ongoing/market-reaction framing penalised; repeat
  reports across feeds within 6 h unflagged, `rule_repeat_of`); rule-shock blackout capped at 120 min per UTC day
  (`news.rule_shock_cap_min_per_day`, LLM shocks uncapped) with a `news_blackout_cap` warning and a per-day log in
  `state/news_scoring.json`; `news` fails at once on a rules error; budget-fallback rule rows count as unscored in
  the features' budget mask.
- BACKLOG 234 (real headlines): Measured 2026-11-13 on the 392 headlines the brain stored 2026-10-16..11-13 (export, received order, poll by poll with repeats): pre-tune rules flagged 4 shocks (10-21 2 = 60 min, 10-28 30 min, 11-02 30 min; 3 were multi-headline wraps flagged from their summaries); after the tune 1 shock (10-21, 30 min); the 120-min cap never bound. Hand-labelled sample of 150 (tests/fixtures/news_rules/real_labelled.yaml, blind labels, tuned on the tune half only: title-only shocks, title-weighted relevance, foreign-economy gate, PPI, Fed weight 0.6): held-out relevance agreement 0.933 (precision 0.857, recall 0.9); held-out shocks 0 flagged, 1 labelled (missed: 'Romanian F-16s shoot down two drones'), tune half 1 false shock ('US intel says ... drone attack'). Shock precision/recall cannot be measured: the window held about one real shock. The held half is not perfectly blind (its pre-tune misses were seen once through an output-filter slip). BACKLOG 234 stays open.

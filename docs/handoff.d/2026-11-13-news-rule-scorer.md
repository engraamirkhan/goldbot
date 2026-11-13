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
  the features' budget mask. BACKLOG 234 (real-headline precision) is still open: reading the brain's 392 headlines
  was refused in the agent sandbox.

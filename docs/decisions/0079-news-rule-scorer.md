# 0079: Free rules-v1 headline scorer drives the shock blackout when no LLM client exists

Status: proposed (implementer, 2026-11-13; ML review lane ML2). Reviews: trading-safety-reviewer (a new input to the
entry blackout), quant-reviewer (a second scorer in the news features).

## Context
The design blocks entries for `shock_blackout_min` after a high-relevance unscheduled news shock. The shock flag came
only from the LLM scorer, which needs an Anthropic API key. The brain holds none, so the collector scored 0 of 392
headlines ("no client"): the live news-shock blackout could never fire and the `news_llm` features (R139) had no
scores. The owner wants nothing pending on him and no paid service.

## Decision
- A deterministic lexicon scorer, `rules-v1` (goldbot/data/news_rules.py), scores every prefiltered headline into the
  LLM's fixed schema plus a shock confidence: gold drivers (Fed/rates, US data, USD, geopolitics/war/sanctions,
  central-bank gold buying, ETF flows, tariffs), direction votes with negation (a negation within 4 words flips a
  direction and cancels a shock), hedge penalty (could/threatens/fears/questions/anniversaries), source weights.
- A shock is flagged only for phrases naming an unscheduled event and only at `news.rule_shock_min_confidence`
  (0.8); a scheduled release is never a shock. A flagged rule shock feeds the existing `shock_window` unchanged.
- Rule scores are kept in `rule_*` columns for every prefiltered row; they fill the main score fields (`scorer` =
  rules-v1) only when the LLM did not score the row. An LLM score takes precedence; both are kept. During an LLM API
  error a rule-shock item is stored at once on its rule score (the others retry next round as before).
- Only new events are shocks (safety review 2026-11-13, HIGH): ongoing-conflict and market-reaction framing
  ("kill 12", "amid", "continue", "shrug off", "steady") takes 0.35 off the confidence, and a later report of an
  event already reported within `news.rule_shock_dedupe_hours` (6) by any feed is stored unflagged with
  `rule_repeat_of`. Rule-shock blackout minutes are capped at `news.rule_shock_cap_min_per_day` (120) per UTC day,
  computed from the store in `shock_window` (stateless, the same in the engine and the smoke test); LLM shocks stay
  uncapped. The collector logs uncapped rule blackout minutes per day; `news_blackout_cap` warns (told at once) at
  the cap.
- Health: the `news` check fails when prefiltered headlines arrived in the last 24 h but none was scored, and at
  once on a rules-v1 error.
- Features: a rule score written while an LLM client was active (`llm_active`) counts as unscored for the budget
  mask, so a time-of-day switch of scorer never enters a series.

## Consequences
- The blackout can now fire without spend. It only tightens: it refuses entries for 30 minutes; exits never read it
  (`_blackout_close` skips `news_shock`, RiskGate is entries only). A false shock costs 30 minutes of entries; the
  fixture set (48 hand-labelled headlines) bounds gross errors but does not measure live precision: BACKLOG 234
  labels the brain's real headlines and reports precision and blackout minutes per week.
- The news features read rule rows flagged (`rule_scored`, `news_rules_share_24h`; `news_llm` version 3). Mixing
  scorers changes the series' meaning when a key arrives; research separates them by `scorer`. The 392 rows already
  stored stay unscored: re-scoring them would rewrite rows after their `written_utc`.
- What would change this: a measured rule-shock precision below 0.8 on real headlines (raise the bar or narrow the
  lexicon), or an API key on the brain (LLM scores take over; rules stay as the fallback).

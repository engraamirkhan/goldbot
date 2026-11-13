---
number: 232
rank: 76
title: Rule shocks fire the news-shock entry blackout, never an exit
stage: 'build (ML review 2026-11-13, lane ML2)'
size: S
status: 'done 2026-11-13 (branch feat/news-rule-scorer, R144, ADR 0079) · accountable: implementer · reviews: trading-safety-reviewer'
---
- **232. Rule shocks fire the news-shock entry blackout, never an exit.** Value: the design's shock blackout could not fire without the LLM. Acceptance, tests first: a rules-v1 shock at or above `news.rule_shock_min_confidence` puts the engine in a `news_shock` blackout that RiskGate refuses entries on; no force-close and the time exit still runs; during an LLM API error a rule-shock item is stored at once.

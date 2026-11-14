---
number: 232
title: Rule shocks fire the news-shock entry blackout, never an exit
stage: 'build (ML review 2026-11-13, lane ML2)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #388 (R144, ADR 0079) · accountable: implementer'
---
- **232. Rule shocks fire the news-shock entry blackout, never an exit.** Value: the design's shock blackout could not fire without the LLM. Acceptance, tests first: a rules-v1 shock at or above `news.rule_shock_min_confidence` puts the engine in a `news_shock` blackout that RiskGate refuses entries on; no force-close and the time exit still runs; during an LLM API error a rule-shock item is stored at once.

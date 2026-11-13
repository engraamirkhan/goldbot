---
number: 231
rank: 75
title: Free rules-v1 headline scorer when no LLM client is configured
stage: 'build (ML review 2026-11-13, lane ML2)'
size: M
status: 'done 2026-11-13 (branch feat/news-rule-scorer, R144, ADR 0079) · accountable: implementer · reviews: quant-reviewer, code-reviewer'
---
- **231. Free rules-v1 headline scorer when no LLM client is configured.** Value: the brain has no API key, so 0 of 392 headlines were scored. Acceptance, tests first: every prefiltered headline gets a deterministic rules-v1 score (gold driver lexicon, negation, hedges, source weights) marked `scorer` = rules-v1; an LLM score takes precedence and the rule score is kept in `rule_*` columns; rows carry written_utc; on the labelled fixture shock precision >= 0.9.

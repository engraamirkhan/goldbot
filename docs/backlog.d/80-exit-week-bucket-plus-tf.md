---
number: 80
rank: 17
title: Week bucketing keys on the real exit (exit_ts + tf) through one helper
stage: Research (population tournament scoring) · LEARN
size: S
status: 'built (needs quant-reviewer); was low priority (quant ruling 2026-10-17: no gate or stop rule affected) · accountable: implementer · reviews: quant-reviewer · due 2026-11-27'
---
- **80. Week bucketing keys on the real exit (exit_ts + tf) through one helper.** Value: `population.weekly_returns` (population.py:~114) buckets by `exit_ts` without `+ tf`, so a Sunday 23:45 15m exit lands in the previous week and slightly mis-scores the tournament's correlation penalty (promotion/weighting); `ruin` only orders/counts by exit so it is unaffected. Acceptance: one shared helper for the real exit time (`exit_ts + tf`) used by population and any other weekly bucketing; test: a Sunday 23:45 15m exit falls in Monday's week; tournament scores for frozen rows documented if any changes.

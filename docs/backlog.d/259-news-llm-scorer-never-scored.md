---
number: 259
rank: 86
title: 'News collector: 0 of 393 headlines LLM-scored before 2026-11-13'
stage: 'data / news (follow-up of 212, 2026-11-14)'
size: S
status: 'done 2026-11-14 (branch fix/data-followups-1114); 3-day readout due 2026-11-16 · accountable: data-engineer · reviews: sre'
---
- **259. News collector: 0 of 393 headlines LLM-scored before 2026-11-13.** Value: the brain's news table holds 399 headlines from 2026-10-16; 393 have `scored` False and no relevance, and only 6 (all on 2026-11-13, scorer rules-v1) are scored: the LLM scorer never scored a headline, so every news_grid window is masked (MAX_UNSCORED) and the forward history the news features need has not been accumulating. Acceptance: root cause (budget, client, key or config) with evidence from the collector's logs; scored share >= 80% of prefiltered headlines over 3 days after the fix, shown by the news_coverage job's readout.
  **Done 2026-11-14 (root cause, readout).** Root cause, from the brain's collector journal and its `news` table (read
  2026-11-14): no Anthropic key is in the brain's keyring, so every poll before rules-v1 logged `unscored_reason: no
  client` and stored its headlines unscored (the LLM scorer never had a client); rules-v1 went live 2026-11-13 12:27 UTC,
  and since then every prefiltered new headline has been scored (6 of 6 rows; the 7th row is not prefiltered, so never
  scored by design). Not the budget (spend 0, `rule_blackout_min_today` 0) and not the filters. The readout's 5 of 352
  (1.4 %) divides by every kept row, including the 86 that are not prefiltered and the 261 prefiltered rows stored
  before rules-v1. Fix: `news_coverage.readout` adds `prefiltered`, `prefiltered_scored_share` and `scored_share_3d`
  (prefiltered rows received in the trailing 3 days); on the brain's data at 2026-11-14 09:00 it reads 5/266 overall
  and 5/36 over 3 days, all 36 being pre-rules rows except the 5 (tests/test_news_coverage_job.py). The 3-day share
  reaches 1.0 once the window holds only post-rules polls (2026-11-16 12:27 UTC). Not done, by decision: no rules-v1
  backfill of the old rows. A score computed now is not knowable at their `written_utc` (news_features' availability),
  the first-release (unscored) rows must stay reproducible, and the rules were tuned on these same headlines, so a
  backfilled history would be in-sample.

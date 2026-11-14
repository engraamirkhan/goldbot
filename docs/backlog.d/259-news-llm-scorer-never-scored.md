---
number: 259
rank: 86
title: 'News collector: 0 of 393 headlines LLM-scored before 2026-11-13'
stage: 'data / news (follow-up of 212, 2026-11-14)'
size: S
status: 'proposed · accountable: data-engineer · reviews: sre'
---
- **259. News collector: 0 of 393 headlines LLM-scored before 2026-11-13.** Value: the brain's news table holds 399 headlines from 2026-10-16; 393 have `scored` False and no relevance, and only 6 (all on 2026-11-13, scorer rules-v1) are scored: the LLM scorer never scored a headline, so every news_grid window is masked (MAX_UNSCORED) and the forward history the news features need has not been accumulating. Acceptance: root cause (budget, client, key or config) with evidence from the collector's logs; scored share >= 80% of prefiltered headlines over 3 days after the fix, shown by the news_coverage job's readout.

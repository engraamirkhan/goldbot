---
number: 233
rank: 77
title: Health alert when headlines arrive but none is scored in 24 h
stage: 'build (ML review 2026-11-13, lane ML2)'
size: S
status: 'done 2026-11-13 (branch feat/news-rule-scorer, R144) · accountable: implementer · reviews: sre'
---
- **233. Health alert when headlines arrive but none is scored in 24 h.** Value: the 0-of-392 gap went unnoticed. Acceptance, tests first: the collector records last arrival and last score in `state/news_scoring.json`; the `news` check fails when prefiltered headlines arrived in the last 24 h, none was scored in it and the record spans 24 h; no record = not judged.

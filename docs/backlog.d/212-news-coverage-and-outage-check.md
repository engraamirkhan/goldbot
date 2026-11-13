---
number: 212
rank: 74
title: News collector coverage readout and outage check on the brain
stage: 'data (follow-up of R139, 2026-11-13)'
size: S
status: 'proposed · accountable: data-engineer · reviews: sre'
---
- **212. News collector coverage readout and outage check on the brain.** Value: the news columns are forward-only, so their usable history is exactly what the brain's collector has stored; the Mac holds no `news` table, so R139 could not measure it. Acceptance, tests first: a daily job (or health check) runs `news_features.coverage` on the brain store and records first/last received, days, kept rows, backfilled rows dropped and scored share; any gap of more than `news_features.OUTAGE` between received stamps raises a dq_event (warning) so a research window can exclude it; a local-only export of the table (sha256-verified, never a release) reaches the Mac research store for item 211.

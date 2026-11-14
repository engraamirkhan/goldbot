---
number: 212
rank: 74
title: News collector coverage readout and outage check on the brain
stage: 'data (follow-up of R139, 2026-11-13)'
size: S
status: 'done 2026-11-14 (branch fix/data-backlog-1114) · accountable: data-engineer · reviews: sre'
---
- **212. News collector coverage readout and outage check on the brain.** Value: the news columns are forward-only, so their usable history is exactly what the brain's collector has stored; the Mac holds no `news` table, so R139 could not measure it. Acceptance, tests first: a daily job (or health check) runs `news_features.coverage` on the brain store and records first/last received, days, kept rows, backfilled rows dropped and scored share; any gap of more than `news_features.OUTAGE` between received stamps raises a dq_event (warning) so a research window can exclude it; a local-only export of the table (sha256-verified, never a release) reaches the Mac research store for item 211.
  **Done 2026-11-14.** `goldbot/data/news_coverage.py` + daily job `news_coverage` (06:05 UTC): the readout (coverage + scored share) and one `news_outage` dq_event (warning) per gap with more than OUTAGE of OPEN gold time between polls (weekends are not outages), written once (tests/test_news_coverage_job.py). On the brain's real table (read only): 399 rows, 352 kept, 47 backfills dropped, 28.1 days (2026-10-16 20:56 .. 11-13 23:09 UTC), **5 scored (1.4%)**, 0 outages (every > 12 h gap is a weekend). Local-only export: `scripts/news_local.sh pull` -> raw/news/news_2026-11-14.parquet, sha256 64f7e42f... matched on both sides. The scored share is the real finding: BACKLOG 259.

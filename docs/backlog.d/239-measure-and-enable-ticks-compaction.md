---
number: 239
rank: 79
title: Measure tick part counts and compaction cost on the brain, then enable ticks
stage: 'data (follow-up of backlog 179, R146, 2026-11-13)'
size: S
status: 'open · accountable: data-engineer · reviews: sre · due 2026-11-20'
---
- **239. Measure tick part counts and compaction cost on the brain, then enable ticks.** Value: R146 ships the compaction of `ticks` but leaves it out of `compact.tables` because a month of one broker's ticks is about 10^7 rows and nothing was measured on the 4-core brain. Acceptance: run `python scripts/store_parts_report.py --data-root <data_root>` on the brain before (part counts, rows, MB, the largest closed month, read seconds, peak RSS per folder); run `compact_table(store, "ticks", before=now, ...)` once by hand on the largest closed month with the default budget (1 GB, 2 threads, 262,144 rows per group) and record wall time, peak RSS and the spill size; if a month fits inside `compact.time_box_s` with RSS < 1.5 GB, add `ticks` to `compact.tables` in config/settings.yaml; rerun the report after the first night and paste both into the PR.

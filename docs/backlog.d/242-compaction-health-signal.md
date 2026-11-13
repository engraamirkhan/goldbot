---
number: 242
rank: 82
title: Health warns when compaction keeps deferring or leaves a spill directory
stage: 'ops (follow-up of backlog 179, R146, 2026-11-13)'
size: S
status: 'open · accountable: sre · reviews: data-engineer'
---
- **242. Health warns when compaction keeps deferring or leaves a spill directory.** Value: R146 time-boxes `journal_compact`: partitions it cannot reach are `deferred` to the next night, which is not a job failure, so a store whose backlog grows faster than one night drains would stay silent. A killed run can also leave `<data_root>/.compact_tmp` (DuckDB spill files) until the next run. Acceptance, tests first: health WARNs when `journal_compact` reported `deferred > 0` on 3 consecutive runs, or when `.compact_tmp` exists outside a running job and is older than 6 h; the check names the table and the deferred count.

---
number: 179
title: Compact the other append-only store tables (ticks, observe_ticks, fills, dq_events)
stage: 'data (follow-up of backlog 170, R131, 2026-11-12)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #393 (R146; review LOWs 4b5cb7fd); enabling ticks compaction is 239 · accountable: data-engineer'
---
- **179. Compact the other append-only store tables (ticks, observe_ticks, fills, dq_events).** Value: backlog 170 compacts only the `decisions` journal; the engine also appends `ticks` (runner tick flush), `fills` (order_send), `observe_ticks` (ADR 0063) and `dq_events` (crossfeed, quarantine, limits_job, positioning_local) with dedupe=False, one part per flush, so the same many-small-parts read cost grows there (ticks the largest by bytes). Acceptance, tests first: measure part counts and read times per table on the brain first; `journal_compact` (or a sibling job) runs `goldbot/data/compact.py::compact_table` for each table whose closed months hold more than N parts, with the same guarantees (lock, manifest, verified row count + hash, open month untouched); for `ticks` the job is time-boxed and paced (month partitions of 10^7 rows: a streaming rewrite by row group, memory bounded, measured on the 4-core brain before enabling); a test per table proves Store.read gives the same frame before and after.

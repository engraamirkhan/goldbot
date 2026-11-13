---
number: 241
rank: 81
title: Ticks and observe_ticks carry their own written_utc column
stage: 'data (follow-up of backlog 179, R146, 2026-11-13)'
size: S
status: 'open · accountable: data-engineer · reviews: code-reviewer, quant-reviewer'
---
- **241. Ticks and observe_ticks carry their own written_utc column.** Value: a tick row holds only ts_utc, bid and ask; when it became knowable lives only in its part file (mtime, and since R146 the `goldbot.lineage` metadata), which a copy without mtimes, an external tool or a dedupe rewrite can lose. The R131 compaction of `decisions` before R146 sorted rows and kept no lineage, so those months have no per-row write time left (not recoverable). Acceptance, tests first: `Engine.flush_ticks` and the observe collector add `written_utc` (the flush's wall clock, UTC) to every row from a set date; `Store.read` of older rows gives it as null (union_by_name), never backfilled; the tz quarantine selector prefers the column when present; `asof`-style consumers that need knowability read it. Volume: +8 bytes a row before compression (about +80 MB a month a broker, a few MB after zstd on a monotone clock).

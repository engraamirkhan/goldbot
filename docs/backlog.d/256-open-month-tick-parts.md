---
number: 256
rank: 83
title: Compact the open month's tick parts (321,590 parts on the brain)
stage: 'data (follow-up of 239, 2026-11-14)'
size: S
status: 'done 2026-11-14 (branch fix/data-followups-1114); brain read time measured after deploy · accountable: data-engineer · reviews: sre'
---
- **256. Compact the open month's tick parts (321,590 parts on the brain).** Value: the brain holds 321,590 tick parts in the open month (39 folders) against 17,360 in closed months: a Store.read of one tick folder takes 139-174 s and 4.9 GB peak RSS whatever its rows, and the nightly compaction never touches the open month. Acceptance: options measured and one chosen with sre: compact closed UTC days of the open month (the engines only append to today), or a longer `tick_flush_s`; read time and RSS of one folder before and after; no change to row content or lineage (written_utc kept).

  **Done 2026-11-14 (code).** Option chosen: compact the closed UTC days of the open month (not a longer
  `tick_flush_s`, which would widen the window of ticks lost in an engine crash and still grow a month's parts without
  bound). `compact_table(..., closed_days=True)` takes, in the open month, only the parts whose rows all fall before
  the run's UTC day (`ts_utc` max < midnight); the engines append only today's ticks with dedupe=False, so those parts
  are settled, and a part reaching into today or later (a flush across midnight, a server-clock error) is left. Same
  build, content-hash verification, lock, manifest and lineage (`written_utc` per source part) as a closed month.
  Settings `compact.closed_days_tables: [ticks, observe_ticks]`; the nightly `journal_compact` passes it. Each night
  the month's compacted part and the previous day's parts become one part, so a tick folder holds 1 part plus at most
  a day of flushes (about 590 on the brain: 321,590 parts / 39 folders / 14 days) instead of up to a month (about
  8,250). Tests: tests/test_compact_open_month_days.py (closed days to one part, today untouched, the next night folds
  the previous day in, a part crossing midnight left, the job and its setting); the job test now counts the open
  month's closed days. Cost: the first night reads about 8,250 parts per folder; the brain's largest closed month
  (5,736 parts) compacted in 10 s at 1.1 GB peak RSS, so about 39 x 15 s inside the 30-min time box (the rest
  deferred). Mac proxy (one synthetic folder, 14 days x 150 parts, the Mac under load 60): 2,100 parts -> 151, Store.read
  1.39 s -> 0.23 s, 42,000 rows before and after, compaction 11.6 s, peak RSS 182 MB. Brain read time and RSS before
  and after: measured by the sre after the deploy (the brain's venv is not readable over the read-only login).

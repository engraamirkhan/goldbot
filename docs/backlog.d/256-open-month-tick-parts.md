---
number: 256
rank: 83
title: Compact the open month's tick parts (321,590 parts on the brain)
stage: 'data (follow-up of 239, 2026-11-14)'
size: S
status: 'proposed · accountable: data-engineer · reviews: sre'
---
- **256. Compact the open month's tick parts (321,590 parts on the brain).** Value: the brain holds 321,590 tick parts in the open month (39 folders) against 17,360 in closed months: a Store.read of one tick folder takes 139-174 s and 4.9 GB peak RSS whatever its rows, and the nightly compaction never touches the open month. Acceptance: options measured and one chosen with sre: compact closed UTC days of the open month (the engines only append to today), or a longer `tick_flush_s`; read time and RSS of one folder before and after; no change to row content or lineage (written_utc kept).

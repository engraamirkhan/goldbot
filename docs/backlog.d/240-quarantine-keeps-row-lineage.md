---
number: 240
rank: 80
title: Quarantine move keeps each row's write time
stage: 'data (follow-up of backlog 179, R146, 2026-11-13)'
size: S
status: 'done 2026-11-14 (branch fix/data-backlog-1114) · accountable: data-engineer · reviews: code-reviewer'
---
- **240. Quarantine move keeps each row's write time.** Value: `quarantine.move_rows` rewrites the rows it keeps as a new part (`_write_atomic`), whose mtime is the rewrite time, so the per-row write time that R146 now preserves through compaction (`compact.written_utc`) is lost there, and a second selector run would judge kept rows against the rewrite time. Acceptance, tests first: the kept rows are written with the `goldbot.lineage` metadata of their source parts (rows in file order), `written_utc` of the rewritten part equals the kept rows' original write times, and the 2026-10-26 selector selects nothing more on a rerun.
  **Done 2026-11-14.** `quarantine.move_rows` writes the kept rows in part write order and file row order with the `goldbot.lineage` metadata of their source parts (`Store._write_atomic(metadata=)`); a rerun of the 2026-10-26 selector on per-row write times selects nothing, even after the file is touched (tests/test_store_quarantine.py).

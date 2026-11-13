- 2026-11-13 (branch feat/compact-other-tables; backlog 179, R146): `journal_compact` now compacts every table in
  settings `compact.tables` (default decisions, fills, dq_events, observe_ticks; `ticks` is supported but off until
  measured on the brain, BACKLOG 239) with the R131 guarantees: closed months only, manifest + partition lock, the
  build outside the lock, row count + sha256 of the Store.read-ordered read, no dedupe (fills keep every row).
  Changed for all tables, decisions included: the new part holds the old parts in write order (mtime), streamed in
  row groups of `row_group_rows` (memory bounded), with per-row write times in Parquet metadata `goldbot.lineage`
  (`compact.written_utc(path)`); rows are no longer sorted on disk (reads still are). Why: a tick's part mtime is
  its only knowability evidence (the 2026-10-26 tz quarantine selector used it; it now reads `written_utc`). The
  verifying read streams from DuckDB under `memory_limit`/`threads` with spill in `<data_root>/.compact_tmp`
  (removed after the run). The job shares one time box (`time_box_s` 1800; the rest is `deferred`, not an error),
  sleeps `pause_s` after each partition, and reports totals plus `tables`. New read-only
  `scripts/store_parts_report.py` (part counts, rows, MB, read s, peak RSS per folder) for the brain measurement.
  Cost: no Actions minutes, no new data; one nightly rewrite per closed account-month; the first night rewrites
  every closed month of fills/dq_events/observe_ticks once. Rollback: as R131 (run the job once so no
  `.compact.json` is left, then revert); compacted parts are plain parts. Not done: brain measurement and enabling
  ticks (239), quarantine rewrite keeping lineage (240), a written_utc column on ticks (241; decisions months
  compacted under R131 read written_utc NaT, recoverable from restic snapshot d7c7a6d1, see 241), a health signal for repeated deferral (242).
  Review of 80339ff7 (LOWs, same branch): pre-lineage compacted parts (no pandas metadata) record written_ns null;
  `quarantine.move_rows` finishes an interrupted compaction swap (`_recover`) before its rewrite; a column Arrow
  cannot unify across parts (int vs text) takes DuckDB's union_by_name type and is cast by DuckDB.

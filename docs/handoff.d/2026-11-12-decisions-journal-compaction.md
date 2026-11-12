- 2026-11-12 (branch feat/decisions-journal-compaction; backlog 170, R131): the decisions journal's part files are
  compacted nightly. New scheduler job `journal_compact` (daily 03:40 UTC, max 20 h late, runs on first sight)
  calls `goldbot/data/compact.py::compact_table(store, "decisions", before=slot)`: every closed month partition
  (before the slot's UTC month) of every source=/symbol= folder holding more than one part becomes one part, under
  the partition's exclusive writer lock (an engine flush to that month waits; the open month is never touched).
  No dedupe; rows on disk in the store order; the new part is published only when its row count and the sha256 of
  the Store.read-ordered frame equal the old parts' (a mismatch drops it and keeps the old parts; the job then
  fails and health shows job:journal_compact). Crash safety: a fsynced manifest `.compact-<id>.json` is written
  before the rename; `Store.read`, `Store.sql` and `_read_partition` skip parts a manifest replaced while its new
  part exists, and the next run finishes the unlinks first. Late rows of a closed month land as a new part and are
  folded in the next night. Nanosecond timestamps are kept (pyarrow rewrite, not a DuckDB round trip).
  Measured locally: 2,900 one-flush parts of one month (17,400 rows) compact in 3.6 s; the account read drops
  0.52 -> 0.03 s. Expected on the brain: the 9,447 parts of 2026-11-10 fall to the open month's parts plus one per
  closed account-month after the first night; each month turn then drops the month's ~2-3k parts per account to one.
  Rollback: remove `journal_compact` from JOBS/settings (or revert the branch); compacted data stays readable by the
  old code (plain parts; leftover manifests are dot-files no reader scans, but only a crash mid-run leaves one, and
  the old code would then read the replaced rows twice until the next run of this code or a manual unlink).
  Follow-ups not done here: the same compaction for the other dedupe=False tables (ticks, fills, exit_fills).

- 2026-11-12 (branch feat/decisions-journal-compaction; backlog 170, R131): the decisions journal's part files are
  compacted nightly. New scheduler job `journal_compact` (daily 03:40 UTC, max 20 h late, runs on first sight)
  calls `goldbot/data/compact.py::compact_table(store, "decisions", before=slot)`: every closed month partition
  (before the slot's UTC month) of every source=/symbol= folder holding more than one part becomes one part; the
  open month is never touched. No dedupe; rows on disk in the store order. The new part is built and verified
  outside the partition lock (row count and sha256 of the Store.read-ordered frame equal to the old parts'; a
  mismatch drops it, keeps the old parts, and the job fails: health shows job:journal_compact); only the swap
  (manifest, rename, unlinks: milliseconds) holds the exclusive lock, and a partition whose listed parts changed
  meanwhile is skipped until the next night. Crash safety: a fsynced manifest `.compact.json` is written before the
  rename; `Store.read`, `Store.sql`, `_read_partition` and the backup's partition staging skip parts a manifest
  replaced while its new part exists, and the next run finishes the unlinks first (and sweeps stale `.part-*.tmp`).
  Result carries duration_s. Nanosecond timestamps are kept (pyarrow rewrite, not a DuckDB round trip).
  Review of 9997629f (code + SRE), same branch: readers now lock (and look for a manifest in) only the partitions
  they can return rows from (source/symbol folder, months of [start, end]) instead of walking the table; every
  shared lock wait is bounded (`Store(lock_timeout_s=30)`), so a stuck writer raises `StoreLockTimeout` naming the
  partition (health shows the check failing with that reason, not a stale pass); `Store.sql` creates views only for
  the tables the query names and reads them under the same shared locks. Engine appends still wait unbounded.
  Measured locally: 2,900 one-flush parts of one month (17,400 rows) compact in 3.6 s; the account read drops
  0.52 -> 0.03 s. Expected on the brain: the 9,447 parts of 2026-11-10 fall to the open month's parts plus one per
  closed account-month after the first night; each month turn then drops the month's ~2-3k parts per account to one.
  Rollback: BEFORE reverting, run the job once (`journal_compact`) and check no `.compact.json` is left under
  data/decisions (older code ignores manifests and would read replaced rows twice after a crash mid-swap); then
  remove the job or revert the branch. Compacted parts are plain parts, readable by the old code.
  Follow-up not done here: backlog 179, the same compaction for the other dedupe=False tables.

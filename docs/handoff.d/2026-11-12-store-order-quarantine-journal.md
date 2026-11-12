- 2026-11-12 (branch fix/store-order-quarantine-journal; backlog 158, 162, R128): three data-layer fixes.
  (1) Quarantine spans fail closed: a failing read of dq_events or a quarantine table raises
  `quarantine.QuarantineReadError` instead of "no spans". Callers: D12 `reconcile_account` raises and the
  feed_reconcile job reports it (no report graded without the spans); `record_span` writes nothing; the nightly
  learner returns `evidence_exclusions:QuarantineReadError` and does not run; the nightly cost job keeps the slippage
  prior (note in the table); the daily review records an `unreadable` incident; the labs n holds every candidate of
  the account out (n understated, H-61 stop unchanged); scripts/evidence_exclusions.py exits with the error. A missing
  table or a schema without the span columns is still no spans.
  (2) `Store.read` orders by ts_utc, then the table's KEY_COLUMNS, then every column except year/month: ties
  (positioning series on one date, dq_events in one minute, multi-source bars) read in one content order, run after
  run, on duckdb 1.5.6 and 2.0.0. Column names are quoted (`check` failed before). macro.first_release,
  positioning.merge_releases and positioning_local's GLD check use stable sorts. Read time, a year of 1m bars
  (377k rows): 2.0.0 +5 % (0.129 -> 0.135 s), 1.5.6 +9 % (0.140 -> 0.152 s), medians of 25 on a loaded Mac.
  (3) The health pass's decisions-journal read (history_gap / order_failed) scans only the account's own
  source=/symbol= folder from the read's month (`Store.read(folder_only=True)`), with the four columns it uses cast
  explicitly, so a folder whose parts are all-null in a column types it as the whole table does. Synthetic journal,
  9,447 parts over 3 accounts: 0.58 -> 0.11 s on 2.0.0, 0.52 -> 0.11 s on 1.5.6, frames equal. A cast column no part
  of the folder has falls back to the whole-table read. Journal compaction is filed as backlog 170 (not done here).
  Behaviour a reader could notice: any consumer that relied on the old tie order now sees the content order; a
  corrupt part in another account's journal no longer breaks this account's health read.

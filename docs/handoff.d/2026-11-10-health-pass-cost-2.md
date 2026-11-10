- Health pass cost, second step (R121, after #360): the Telegram service's 60 s health pass no longer re-parses config
  YAML or re-reads an unchanged decisions journal. `goldbot.config.safe_load_cached` parses a file once per version
  ((inode, mtime_ns, size), as `_registry_rows`) and hands each caller its own copy; `load_arms`, `load_accounts` and
  the strategy catalogue use it. `history_gap:<account>` re-reads the journal only when a part file was added and
  otherwise filters its last read to the window: same verdict as a fresh read, a new refusal is seen on the next pass.
  The `session_local` DST-gap refusal is memoised per (zone, minute). Local warm pass (repo state, no engine files)
  4.58 s -> 0.28 s; a synthetic 4,500-part journal for three accounts reads in 0.93 s cold, 5 ms unchanged. Still to
  measure on the brain after deploy: pass < 5 s in the journal, service < 5 % of a core over 1 h. Not addressed:
  the journal read after each flush still costs the full DuckDB scan (thousands of one-flush part files; compacting
  `decisions` is a data-engineer item), and `read_learning_closes` (pnl_basis) rebuilds the trade ledger every pass.

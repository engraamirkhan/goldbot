- Broker tick record for the H-A holiday diagnostic (BACKLOG 79, data-engineer, 2026-10-17).
  - New daily scheduler job `tick_record` (22:40 UTC). For each enabled account it pulls the broker's XAUUSD ticks
    of every closed server date with `copy_ticks`, read-only, through the scheduler's existing broker connection
    (`_job_broker`, the one feed_reconcile and broker_ledger use). Pages are 10k rows, so the single-threaded bridge
    is never held for long. Each day goes to `state/ticks/<account>/<YYYY-MM-DD>.parquet`.
  - File contents: `time_ns` (int64, through `epoch_ns`), `bid`, `ask`, `flags`. Written atomically, mode 0600, in
    a 0700 directory. `index.json` records rows, London/NY ticks, median spread, bytes and sha256 per day.
  - Re-runs are idempotent. Missed days within `ticks.backfill_days` (7) are back-filled. Days older than
    `ticks.retain_days` are pruned.
  - Health `ticks:<account>` (WARN only) when the newest day has fewer than `ticks.min_london_ny_ticks` (1000)
    London/NY ticks, or has not advanced in 4 days.
  - Loader: `goldbot.data.ticks.load_day(account, day, state_dir=...)`, which verifies the sha256.
  - Size: about 12 B/tick, so 50k ticks is about 0.6 MB a day.
  - Backup: the files are backed up (the whole state dir is). The terminal's tick history is finite, so a lost day
    cannot be pulled again.
  - Deviation from the lane brief: no recorder in the engine and no change to runner.py.
    - The engine already stores its polled `last_tick` sample in `data/ticks/source=<account>`.
    - A polled sample misses ticks between reads, so it biases H-A's ticks-per-minute.
    - The pre-registration (docs/research/holiday-list-measurement-2026-10.md) fixes "copy_ticks after each server
      day closes, one Parquet per server date".
  - `retain_days` defaults to 240, not 90: H-A reads the 2026-11-26 file after 2027-04-30.
  - Not done:
    - the 90% uptime rule (terminal connection history);
    - compaction of the engine's per-minute `data/ticks` part files (about 1,400 files a day, existing debt).

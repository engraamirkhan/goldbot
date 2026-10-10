# Operational state in SQLite (WAL), with off-host backups

Decision document for the owner. Date: 2026-10-10. Status: step 0 (foundation) and step 1 (auth and audit) implemented
2026-10-10 as package `goldbot/db/` (not `goldbot/state/`); steps 2-8 not started. Step 0 leftovers: `run.py migrate` /
`import-state` are `python -m goldbot.db migrate|import-state` for now, no `state_db` health check yet, and the API
migrates aux.db on open until `run.py migrate` exists (concurrent migrators are safe: each step re-reads the version
under the write lock). Versions are rows of a `schema_version` table, mirrored in `PRAGMA user_version`.
Owner request: "the trading system uses full stack, and make use of databases as well to get it done robustly, reliably".

**Design basis.** `docs/DESIGN.md` already says "Parquet + DuckDB plus SQLite for live state, no database server"
(Stack) and "Nightly `restic` snapshots of SQLite ... with a scripted weekly restore test". The SQLite half was never
built. **Reading taken:** backups go to Oracle Object Storage instead of Backblaze B2 (free, same tenancy); the
online design is edited first.

## 1. Why change, and why SQLite rather than Postgres

Defects found while reading: `Engine._save_orders` (goldbot/engine/runner.py:779), `_save_risk_state`, `_write_state`,
`ApprovalBus._write_atomic` (goldbot/telegram/bus.py:47), `Supervisor.evaluate` and `drift_watch` rename without
**fsync**, so a power cut can leave an empty `orders_<account>.json` and X7 fails on restart instead of reconciling.
The O_EXCL decision file is filled after creation; a crash between leaves an empty "decided" file that loses the
approval. Auth sessions live in memory, so an API restart logs everyone out.

**SQLite (WAL, stdlib `sqlite3`)** gives transactions, fsync on commit and atomic compare-and-insert with no new
process, port, credential or dependency, on the Linux brain and the Windows VPS alike. Every writer runs on one host
as user `goldbot` (goldbot/ops/linux/systemd); the bridge VM holds no state; load is tens of writes a minute.

**Postgres** adds row locks, LISTEN/NOTIFY and multi-host writers, none needed now, at the cost of a daemon to patch
and back up, password auth, connections for 7+ processes and a non-stdlib driver. **Revisit when** a second host
must write state, writes pass ~100/s, or health logs repeated `SQLITE_BUSY` timeouts. The schema stays portable
(TEXT JSON, INTEGER timestamps) so that move is a port.

## 2. What moves and what stays

`synchronous` is a per-connection setting, not a per-table one, so there are **two database files**:

| File | `synchronous` | Contents | Reason |
| --- | --- | --- | --- |
| `state/core.db` | FULL | approvals, decisions, control, orders, open trades, risk state, engine heartbeats, supervisor state, drift halts | everything that gates or records an entry; a commit must survive power loss |
| `state/aux.db` | NORMAL | users, invites, sessions, audit, trials, model registry, population, shadow books, agent runs and spend, hypotheses, scheduler, outbox, health dedupe, news feeds, recalibrations, deploys | a power cut may lose the last second, never consistency; busy writers here never hold the order-path lock |

No transaction spans both files (ATTACHed WAL transactions are not atomic across files).

**Stays as files:**

| File | Reason |
| --- | --- |
| Parquet (bars, features, ticks, `decisions` journal) | analytical, append-only, joined with `asof_join` |
| `models/**.pkl` | blobs; `models` table keeps path + SHA-256, `ModelRegistry.load` refuses a mismatch |
| `costs_*`, `broker_terms_*`, `config/costs_measured.json` | one nightly writer, read off-host by research; add `.sha256` sidecar and fsync |
| `classifier_*.json` | one writer; unreadable already means `unknown` (no entries) |
| `phase_state.json` | named in the live-gate non-negotiable; moving it is a separate decision |
| `research_plan.json` | recomputed each run |
| `.secrets.json`, keyring | secrets never go in a database |

## 3. Schema

New package `goldbot/state/`: `db.py` (`connect(kind)`, pragmas, typed errors), `migrations/{core,aux}/NNNN_*.sql`,
and one repository module per area replacing the file I/O inside today's classes, whose public APIs stay unchanged.

**Record mapping.** Keys and filter fields become real columns. The whole pydantic record goes in
`body TEXT NOT NULL CHECK (json_valid(body))`, written with `model_dump_json()` and read with
`model_validate_json()`, so `extra="forbid"` still rejects drift. Indexed timestamps are `INTEGER` nanoseconds from
`timeutil.epoch_ns`; inside `body` they stay ISO strings (`UtcTimestamp`). Tables are `STRICT` (SQLite >= 3.37,
checked at connect).

core.db:

- `proposals(proposal_id PK, account_id, created_ns, expires_ns, status CHECK IN ('pending','done'), outcome, body)`, index `(status, expires_ns)`.
- `decisions(proposal_id PK REFERENCES proposals, approve, reason_code, by_user, ts_ns)`.
- `control(id PK CHECK (id=1), halted, by_user, ts_ns, reason, rearm_id, rearm_by, rearm_ts_ns)` plus an append-only `control_log`.
- `orders(account_id, client_order_id, ts_ns, agent_id, side, magic, lots, max_bars, sl, tp, status CHECK IN ('sending','filled','rejected','unfilled'), position_id, PRIMARY KEY (account_id, client_order_id))`, index `(account_id, status)`.
- `open_trades(account_id, position_id, body, PRIMARY KEY (account_id, position_id))`.
- `risk_state(account_id PK, updated_ns, body)`, `engine_state(account_id PK, ts_ns, body)`, `supervisor_state(id=1, ts_ns, halt, body)`, `drift_state(id=1, ts_ns, system_halt, body)`, `drift_review`.

aux.db:

- `users(email PK, role, enabled, body)`, `invites(token_sha256 PK, ...)`, `sessions(token_sha256 PK, email, expires_ns)`: token hashes only.
- `audit(id INTEGER PK AUTOINCREMENT, ts_ns, event, actor, body)`; triggers abort UPDATE and DELETE.
- `trials(id PK, ts, agent_id, config_hash, feature_version, family, quarter, status, lease_until_ns, body, UNIQUE (ts, agent_id, config_hash, feature_version))`. The unique key is `registry_sync._key`. View `trials_numbered` computes `trial` with `ROW_NUMBER() OVER (ORDER BY ts, agent_id)`, which matches `merge_rows`.
- `models(version PK, agent_id, family, status, sha256, path, body)` with `UNIQUE INDEX one_champion ON models(agent_id) WHERE status='champion'`, plus `recalibrations`.
- `population` (`agents.json` becomes a query), `shadow_books`, `agent_runs`, `agent_spend(month PK)`, `hypotheses`, `scheduler_jobs`, `outbox_cursor`, `health_last`, `news_feeds`, `deploys`, `backups`, `imports`.

**Versioning.** Each file stores its schema version in `PRAGMA user_version`. `run.py migrate` takes a backup, then
applies pending migrations, each in one `BEGIN IMMEDIATE` transaction that also sets `user_version`; forward-only,
never edited after merge. Services never migrate: one refuses to start on a version other than its own, which fails
closed and stops seven services racing to migrate.

## 4. Concurrency and reliability

- **Pragmas at connect:** `journal_mode=WAL`, `synchronous=FULL|NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`
  (2000 on the engine's order write), `trusted_schema=OFF`. Use `isolation_level=None` and start every write with
  `BEGIN IMMEDIATE`, so the write lock is taken up front. A deferred transaction that upgrades to a write can fail
  with `SQLITE_BUSY` without busy_timeout retrying it.
- **One writer per row family:** `engine@<acc>` writes its own orders, open trades, risk, heartbeat and proposals;
  the supervisor `supervisor_state`; API and Telegram `decisions` and `control`; the scheduler `drift_state` and
  most of aux. No transaction is held across compute, a broker call or network I/O.
- **First decision wins:** `submit` runs `BEGIN IMMEDIATE`, reads the proposal (must be pending and not expired),
  then `INSERT INTO decisions`. A primary-key conflict means "already decided", and `COMMIT` makes the decision
  durable. This replaces O_EXCL, and the empty-file case cannot happen.
- **fsync semantics:** WAL + FULL fsyncs the WAL on every commit, surviving a kill and a power cut. NORMAL syncs at
  checkpoint: a kill loses nothing, a power cut may roll back the last commits, never corrupts. The scheduler runs
  `wal_checkpoint(TRUNCATE)` in the daily break; health warns on a `-wal` over 64 MB. Never on a network filesystem.
- **Locked or corrupt, on the order path (fail closed):** if the order row cannot be committed (BUSY after the
  timeout, IOERR, FULL, CORRUPT), `OrderPersistError` is raised, the entry is not sent, `persist_failed` is
  journalled and the engine blocks entries until a write succeeds. A failed read of `control` or `drift_state`
  counts as halted. A missing or stale `supervisor_state` halts, as today (S4). Exits are never gated: a close goes
  to the broker first, its DB write is retried in the background, and X8 reconciliation repairs it.
- **At startup** each service runs `PRAGMA quick_check`. A failing core.db puts engines in exits-only mode and
  alerts by Telegram and email; a corrupt aux.db only degrades the dashboard and research.

## 5. Safety invariants

| Invariant | Mechanism | Test |
| --- | --- | --- |
| Client id durable before `order_send`; reconciled on restart (X7) | `orders` row `sending` committed (FULL) in the same transaction as the `open_trades` snapshot, then send; `_load_orders` keeps today's logic over `SELECT ... WHERE status='sending'`; `sending` rows are never pruned | kill tests, §8 |
| First approval wins | `BEGIN IMMEDIATE` + PK on `decisions` | 8-process race |
| Halts fail closed | any DB error reading control, drift, supervisor or risk = halted; corrupt core.db = exits only | corruption test |
| Trial count never lost or double-counted | UNIQUE merge key; `trial` computed, never stored; sync = pull release JSONL → `INSERT OR IGNORE` → export JSONL → push. Budget check and a `reserved` row (with lease) commit together under `BEGIN IMMEDIATE`; the trial later updates that row. An expired lease becomes `aborted` and still counts (it looked at the data). This replaces the hours-long lockfile | race for the last budget slot; double-sync idempotence |
| Checksummed artefacts | `models.sha256` checked at load; promotion (challenger → champion, champion → previous) is one transaction, and `one_champion` forbids two champions | registry tests |

The GitHub research workflow keeps writing JSONL. The release stays the exchange format, and the DB lives only on the brain.

## 6. Migration plan (PR-sized, ordered by risk)

**Dual-read** (steps 1–7): read the DB row; if it is missing, read the JSON file, upsert it, and count a
`state_fallback` hit (shown by health). Steps 5–7 also **dual-write** the JSON after the DB commit for one release,
so rolling back the code is safe. **`run.py import-state [--dry-run]`** validates every file with its existing
pydantic model, upserts by natural key (idempotent), records file SHA-256s in `imports`, leaves the files in place,
and stops loudly on an unreadable orders or risk file.

| # | Step | Files touched | Effort |
| --- | --- | --- | --- |
| 0 | Foundation: `goldbot/state/` (connect, pragmas, migrator, errors), `run.py migrate`/`import-state`, `state_db` health check, no callers | new package, ops/run.py, ops/health.py | M |
| 1 | Auth: users, invites, sessions (now survive restarts), append-only audit | api/auth.py, api/app.py (wiring) | S |
| 2 | Backups and restore drill (§7). Covers the JSON files too, so it lands before any core step | new ops/backup.py, ops/jobs.py, ops/health.py (`backup_age`), ops/accounts.py (key prompts), RUNBOOK | M |
| 3 | Aux bookkeeping: agent runs, spend, hypotheses, outbox cursor (id instead of line offset), health dedupe, scheduler heartbeat, news feeds, recalibrations | agents/runner.py, agents/tools.py, telegram/outbox.py, ops/scheduler.py, ops/health.py (`HealthWatch`), data/news_collector.py | M |
| 4 | Research: trials + reservations + sync, model registry, population, shadow books | research/registry.py, registry_sync.py, model_registry.py, population.py, director.py, engine/shadow.py, agents/tools.py, ops/jobs.py | L |
| 5 | Halt inputs: engine heartbeat, supervisor, drift | risk/supervisor.py, engine/runner.py (`_write_state`, `_drift`), ops/jobs.py (`drift_watch`), ops/health.py | M |
| 6 | Approvals bus + control | telegram/bus.py, telegram/bot.py, api/app.py | M |
| 7 | Orders, open trades, risk state (X7) | engine/runner.py (`_orders_*`, `_*_risk_state`) | L |
| 8 | Remove dual-read and dual-write after 30 days with zero fallback hits; delete imported files | the above, docs | S |

**Parallel work (after step 0):** step 1 has disjoint files from 2, 3 and 4; step 6 from 3 and 4. Steps 2–5 share
`ops/health.py` or `ops/jobs.py` (3 and 4 also `agents/tools.py`), 1 and 6 share `api/app.py`, and 5 and 7 share
`engine/runner.py`: those pairs merge in sequence. Steps 6 and 7 **cut over at the weekend market close**: stop the
engines, `import-state`, start the new code, so X7's tested restart reconciliation runs on the imported rows.

## 7. Backups and restore

- **Nightly at 22:15 server time:** `sqlite3.Connection.backup()` (online API, consistent under WAL) copies
  core.db and aux.db to `/var/lib/goldbot/backup/`. `PRAGMA integrity_check` runs on the copies. `restic backup`
  then saves the copies, the costs/classifier/phase files, model artefacts and the brain's own Parquet. That
  Parquet is the journal and the live broker ticks and bars, which cannot be downloaded again; Dukascopy history is
  already on release `data-v1`. The destination is **Oracle Object Storage** through its S3-compatible endpoint.
- **Credentials:** the OCI Customer Secret Key and restic password go in through `goldbot.ops.accounts` prompts
  (keyring, or the 0600 fallback on the headless brain), never chat or the repo. restic encrypts client-side (aux.db
  holds password hashes and TOTP secrets). The restic password is shown once on the console for the owner's password
  manager: a key kept only on the host it protects cannot restore that host.
- **Never to GitHub** (public repo): a CI test fails if any workflow uploads `*.db`.
- **Retention:** `restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune`. Health warns when the
  repository is over 15 GB of the 20 GB free tier.
- **Weekly restore drill (design):** restore the latest snapshot to a temp directory; check `integrity_check`,
  `user_version`, row counts against `backups`, readable `sending` orders, and trial count against the release;
  then `restic check --read-data-subset=5%`.
- **Health `backup_age`:** warn after 26 h without a verified backup, fail after 36 h or on a failed drill, and email
  the owner.

## 8. Testing strategy (write first)

- Schema: `tests/test_state_db.py::test_wal_and_synchronous_are_set_per_file`, `::test_newer_schema_refuses_to_start`,
  `::test_migrations_from_every_version_match_a_fresh_schema`, `::test_a_killed_migration_leaves_the_old_version`.
- Multi-process (spawn): `tests/test_state_concurrency.py::test_eight_deciders_one_decision_wins`,
  `::test_only_one_process_takes_the_last_trial_slot`, `::test_busy_timeout_on_the_order_write_blocks_the_entry`.
- Kill tests (engine subprocess, paper broker, fault hooks that SIGKILL after commit, after send, mid-transaction):
  `tests/test_order_path_crash.py::test_kill_after_commit_before_send_marks_unfilled_and_never_resends`,
  `::test_kill_after_send_adopts_the_fill_on_restart`; `tests/test_state_failclosed.py::test_corrupt_core_db_means_exits_only`.
- Migration, on golden fixtures written by today's code: `tests/test_import_state.py::test_import_round_trips_every_record`,
  `::test_import_twice_is_idempotent`, `::test_dual_read_falls_back_to_json_and_counts_it`; integration
  `tests/test_backup.py::test_backup_restores_to_identical_rows` against a local restic repository.
- Not testable in CI: a real power cut; the pragmas are asserted instead.

## 9. Risks

| Risk | Mitigation |
| --- | --- |
| A writer stalls an engine on the core lock | split files; no compute in transactions; 2 s timeout fails closed |
| WAL growth (checkpoint starvation) | short reads, nightly TRUNCATE, health on `-wal` size |
| Cutover loses an in-flight approval or order | weekend cutover, idempotent import, dual-write rollback, X7 |
| Reservations change the trial count | aborted leases count; merge key unchanged |
| SQLite older than 3.37 on a host | checked at connect; service refuses to start |

**Docs per step:** online design first (backup target); TRACEABILITY X7, S4, U6 plus a new state-store row with
the readings taken; RUNBOOK (migrate, backup keys, restore); HANDOFF.

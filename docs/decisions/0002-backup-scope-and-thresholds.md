# 0002 Backup scope, retention and alert thresholds

Date: 2026-10-10. Owner of the decision: principal SRE. Implements step 2 of docs/proposals/2026-10-state-store.md.

## Context
The brain holds state that a fresh install cannot rebuild: approvals, orders and risk state, the trial registry,
model artefacts, auth (aux.db), and the engine's own Parquet (ticks, fills, trades, decisions). The proposal (section
7) asks for nightly restic snapshots to Oracle Object Storage, a weekly restore drill, and health that warns at 26 h
and fails at 36 h. The brief for this change gave 72 h as the fail threshold and suggested retention of 7/4/6.

## Decision
- **Scope:** all `state/*.db` files are copied with the SQLite online backup API, in one step under one read
  transaction, and `integrity_check` runs before upload. A corrupt live database fails the backup and nothing is
  uploaded, so "latest" stays the last good snapshot. The snapshot also holds the rest of `state/`, the trial
  registry, `models/`, and every Parquet `source=` except the release sources (`dukascopy`, `fred`) and the derived
  tables (`features`, `labels`).
- **Left out:** `.secrets.json`, heartbeats and temp files. With secrets in the backup, the restic password alone
  would unlock every broker and bot credential. After a rebuild the owner enters the secrets again (RUNBOOK 0.6).
- **Torn files:** Parquet parts are written in place, not renamed into place. A part without its closing `PAR1`
  footer is skipped and listed in the record, and the next night's snapshot picks it up.
- **Retention:** 14 daily, 8 weekly, 12 monthly snapshots, as the proposal says. The design doc is the source of
  truth and the brief said "e.g.". The data is small and deduplicated, and health warns at 15 GB of the 20 GB free
  tier.
- **Who may delete (security review, 2026-10-10):** the brain only appends. It never runs `forget` or `prune`, and
  its Oracle key is denied `OBJECT_DELETE` where the policy allows (restic's lock removal then fails, logged as a
  warning). Retention runs monthly from the owner's Mac with a second key (`scripts/backup_retention.sh`, which
  refuses on a server), and uploads a marker snapshot (`goldbot-retention`). The brain records the marker's time, and
  health `backup_prune` warns after 45 days (or 45 days after the first backup when none exists). The bucket has
  Object Versioning with a lifecycle rule that deletes previous versions only after 30 days or more, AND the brain's
  key has no delete right (mandatory). Versioning alone is not a fallback: in OCI the OBJECT_DELETE right that deletes
  an object also deletes its previous versions (security review). If the deny condition breaks restic, the fallback
  is an append-only restic rest-server, not a key with delete rights.
  Rejected: a bucket retention rule, which would also block restic's lock removal and the Mac's prune.
- **Staging hygiene:** the backup and restore jobs run under umask 077; the work dir, staging dir and restore target
  are 0700, re-applied when they already exist. JSONL copies end at the last newline. A month partition is staged
  whole (a dedupe rewrite seen mid-copy is retried, then listed in `skipped_partitions`). `verify_restore` refuses
  symlinks and manifest paths that leave the restore directory.
- **Thresholds:** `backup_age` warns after 26 h and fails after 72 h, as the brief says. The warning is in
  `NOTIFY_ON_WARN`, so the owner hears about the first missed night by Telegram. A failure at 36 h would add a second
  alert for the same incident and would fire on any weekend outage. `restore_drill` fails when the drill fails and
  warns after 8 days.
- **No in-place restore:** `run.py restore` writes only into an empty directory. The owner stops the services and
  copies the files into place, deleting stale `-wal` files first.

## Consequences
- Recovery point: up to 24 h of order-path state can be lost. On restart the engines rebuild it from the broker
  (X7/X8 reconciliation), and the trial registry is also on release `research-v1`.
- Recovery time: the target is 1 hour. It has not been measured on the VM yet; the first drill on the brain gives
  the number.
- The drill compares against the snapshot's own manifest, not against a `backups` table in aux.db. Adding that table
  belongs to state-store step 3. The proposal's email alert is not built; Telegram is the only channel.
- What would change these choices: a repository approaching 15 GB would mean revisiting the Parquet scope or
  retention. An owner who wants credentials recoverable from the backup is an owner decision; the default keeps
  them out.

## Update 2026-10-16: restic locks under the no-delete key
Incident: [docs/incidents/2026-10-16-restic-lock-no-delete-key.md](../incidents/2026-10-16-restic-lock-no-delete-key.md).

**Context.** The first restore drill on the brain failed: `restic check exited 1: unable to create lock in backend:
repository is already locked ... lock was created 52 s ago`. Every restic command that locks writes a file under
`locks/` and deletes it when it ends. The brain's key has no `OBJECT_DELETE`, so each of those files stays. `backup`,
`restore`, `stats` and `snapshots` take shared locks, which never block one another. `check` (and `prune`) needs an
exclusive lock, which any other lock blocks. restic does not skip a stale lock when it takes a new one; only
`restic unlock` (which needs delete rights) removes stale locks. So after the first backup, a locking `check` from the
brain can never succeed again.

**Options weighed.**
| Option | Verdict |
| --- | --- |
| Read-only commands with restic's global `--no-lock` (`restore`, `check`, `snapshots`, `stats`, ...) | **Chosen.** Adds no lock and waits on none. Supported by both restic versions the brain can get from apt (0.12.1 on Ubuntu 22.04, 0.16.4 on 24.04), and `check` honours it too. |
| Run `check` before `restore` | Rejected. The backup's leftover shared lock still blocks the exclusive lock, and `check` would then leave an exclusive lock that blocks every later backup. That is worse than the incident. |
| An acceptance window for stale locks | Rejected. restic has no such option. Building one means deleting locks, which needs the delete right we refuse to give. |
| A key with delete rights, or dropping the no-delete condition | **Never.** See "Who may delete" above. |

Evidence, gathered 2026-10-16 with real restic release binaries (checksums matched against the upstream
SHA256SUMS):
- restic 0.12.1, on a local repository whose `locks/` folder denies delete (macOS ACL).
- restic 0.16.4, against a REST backend that answers every DELETE with 403, the way the OCI key without
  OBJECT_DELETE does. The ACL setup cannot be used for 0.16, because 0.16 writes local files by rename.

Results with both versions:
- `backup` over a leftover lock exited 0. It left one lock and logged that the cleanup failed. On 0.16 it took
  about 70 s longer, because restic keeps retrying the refused DELETE.
- `--no-lock` `stats`, `snapshots`, `restore` and `check --read-data-subset=5%` exited 0 and added no lock.
- A locking `check` reproduced the incident's error.
- The real-restic integration test (`test_real_restic_round_trip_and_drill`) passes with both versions.

**Decision.**
- `Restic.run` adds `--no-lock` to every read-only subcommand (`READ_ONLY_COMMANDS` in goldbot/ops/backup.py). Only
  `backup` (shared lock) and `init` (no lock) run without it.
- The brain no longer runs `restic unlock`. It cannot succeed without the delete right. Each try only retried and
  logged a warning.
- `--no-lock` gives up restic's guard that a `check` never reads while a backup writes. A host-local lock replaces
  it: `host_lock`, an fcntl lock on `<work_dir>/.backup.lock`, held by both `backup_job` and `drill_job`. The
  kernel releases it when the process ends, so it can never go stale. A second run waits up to 30 min, then fails
  with "another backup or restore drill is running". The scheduler already runs jobs one at a time, so the lock only
  matters for a run started by hand.
- Leftover locks: one shared lock per nightly backup, plus one more for each 5-minute lock refresh during a long
  backup. The monthly `scripts/backup_retention.sh` runs `restic unlock` first. That clears every lock older than
  30 min, and its `forget --prune` then gets its exclusive lock.

**Consequences.**
- The weekly drill (Sunday 10:00) and the nightly backup (22:15) cannot collide on restic locks: the drill takes
  none, and the backup's shared lock ignores the shared locks earlier backups left.
- A drill or restore that runs while the Mac's prune deletes packs can fail. It fails loudly, because restic
  verifies every blob, so nothing is silently wrong. Run retention outside 22:00-23:30 UTC and Sunday 09:45-11:00 UTC
  (RUNBOOK 0.6).
- Locks pile up under `locks/` between monthly retention runs (about 30-60 small files). Each backup reads them, so
  a skipped retention makes backups a little slower. `backup_prune` already warns after 45 days.
- Measured risk: restic refreshes a long backup's lock every 5 min by writing a new lock file and deleting the old
  one. From 0.16, a refresh whose delete is refused counts as failed. The lock goes stale after 30 min, so restic
  cancels the backup once it cannot refresh in time (about 22 min in). Against the DELETE-refusing backend, restic
  0.16.4 logged `unable to refresh lock: ... 403` every 5 min. A 30-minute backup ended with
  `Fatal: unable to save snapshot: context canceled` (exit 1). Nightly backups are incremental and take seconds to a
  few minutes, plus about 70 s of refused-delete retries. Health `backup_age` now warns when a good backup took 10
  min or more (`BACKUP_SLOW_WARN_S`), well before the cancel point. If that warning fires, the fix is the fallback
  below, never a delete right. A backup that is cancelled shows as a failed backup in `backup_age` and alerts the
  owner as usual.
- What would change this: a restic release that drops `--no-lock` on `check`, or a backup that grows past about 15
  min. Then the fallback is the one named above: an append-only restic rest-server, never a delete right.

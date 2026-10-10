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
  Object Versioning with a lifecycle rule that deletes previous versions only after 30 days or more. If the deny
  condition breaks restic, versioning is the fallback: a deleting key still cannot remove history for 30 days.
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

- 2026-11-14 (branch fix/outage-db-private; incident 17:08 UTC): `state/health_outages.db` (the bridge-outage record)
  was created with the default umask (0644) on the first bridge outage, so preflight's `db_files_private` failed and
  an icm-demo cut-over rolled back. It now goes through `goldbot.db.connection.create_private` (renamed from
  `_create_private`, the helper `connect` already used): created 0600 and tightened back to 0600, with its -wal/-shm,
  on every open. The sweep found the same trap in the discovery ledger's database (`state/discovery_*.db`: the
  search ledger, the stage journal and the arm-registration journal); fixed the same way. Every other direct
  `sqlite3.connect` under state/ is read-only (`mode=ro`) or on an existing file. A box that already has a 0644 file
  is fixed by the next health pass or nightly run (or `sudo chmod 600 state/*.db`). Nothing on the order path changed.

"""`python -m goldbot.db migrate|import-state [--state-dir DIR] [--dry-run]`.

scheduler-status: print the scheduler's status document from aux.db as JSON (exit 1 when there is none yet);
  goldbot/ops/vps_update.ps1 reads it to avoid stopping a job mid-run.
migrate: bring core.db, aux.db (and tv.db, tv_auth.db when present) up to this code's schema (forward-only, one transaction per migration).
import-state: copy the JSON state files into the databases (idempotent; files are left in place).
"""
from __future__ import annotations

import argparse
import json

from goldbot.db.bookkeeping import scheduler_status
from goldbot.db.connection import Database, DbKind, db_path
from goldbot.db.import_state import import_state
from goldbot.db.migrations import current_version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.db")
    ap.add_argument("command", choices=["migrate", "import-state", "scheduler-status"])
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--dry-run", action="store_true", help="import-state: validate and count, write nothing")
    a = ap.parse_args(argv)
    if a.command == "scheduler-status":
        doc = scheduler_status(a.state_dir)
        if doc is None:
            return 1
        print(json.dumps(doc))
        return 0
    if a.command == "migrate":
        kinds: list[DbKind] = ["core", "aux"]
        # the TradingView files only where TradingView is in use (their services migrate them on open anyway)
        kinds += [k for k in ("tv", "tv_auth") if db_path(a.state_dir, k).exists()]
        for kind in kinds:
            db = Database.open(a.state_dir, kind)
            with db.connection() as conn:
                print(f"{db.path}: schema version {current_version(conn)}")
        return 0
    for r in import_state(a.state_dir, dry_run=a.dry_run):
        print(f"{r.source} -> {r.kind}.db: {r.status} ({r.rows} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

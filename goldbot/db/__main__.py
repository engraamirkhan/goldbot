"""`python -m goldbot.db migrate|import-state [--state-dir DIR] [--dry-run]`.

migrate: bring core.db and aux.db up to this code's schema (forward-only, one transaction per migration).
import-state: copy the JSON state files into the databases (idempotent; files are left in place).
"""
from __future__ import annotations

import argparse

from goldbot.db.connection import Database, DbKind
from goldbot.db.import_state import import_state
from goldbot.db.migrations import current_version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.db")
    ap.add_argument("command", choices=["migrate", "import-state"])
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--dry-run", action="store_true", help="import-state: validate and count, write nothing")
    a = ap.parse_args(argv)
    if a.command == "migrate":
        kinds: tuple[DbKind, ...] = ("core", "aux")
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

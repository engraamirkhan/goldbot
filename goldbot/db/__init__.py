"""Operational state in SQLite (WAL): core.db (synchronous FULL) and aux.db (synchronous NORMAL).
See docs/proposals/2026-10-state-store.md. Standard library `sqlite3` only."""
from goldbot.db.connection import (
    SYNCHRONOUS,
    Database,
    DbKind,
    SchemaVersionError,
    SqliteTooOld,
    StateDbError,
    connect,
    db_path,
    now_ns,
    write,
)
from goldbot.db.migrations import (
    MIGRATIONS,
    Migration,
    check_version,
    current_version,
    latest_version,
    migrate,
)
from goldbot.db.repo import RecordRepo

__all__ = ["SYNCHRONOUS", "Database", "DbKind", "SchemaVersionError", "SqliteTooOld", "StateDbError", "connect",
           "db_path", "now_ns", "write", "MIGRATIONS", "Migration", "check_version", "current_version",
           "latest_version", "migrate", "RecordRepo"]

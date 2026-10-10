"""Forward-only schema migrations for core.db and aux.db (docs/proposals/2026-10-state-store.md, section 3).

"Each migration runs in one BEGIN IMMEDIATE transaction that also records its version; forward-only, never edited
after merge." Applied versions are rows of `schema_version` (version, name, applied_ns), mirrored in
`PRAGMA user_version` so a backup's version can be read without knowing the table. A file whose version is newer
than this code refuses to open (fail closed): old code never writes a schema it does not understand.

Concurrent migrators are safe: each step re-reads the version after taking the write lock, so two processes opening
the same file apply every migration exactly once.

To change the schema, append a Migration with the next version. Never edit or reorder a merged one.
"""
from __future__ import annotations

import sqlite3

from goldbot.base import FrozenRecord
from goldbot.db.connection import DbKind, SchemaVersionError, now_ns, write


class Migration(FrozenRecord):
    version: int
    name: str
    statements: tuple[str, ...]


_BODY = "body TEXT NOT NULL CHECK (json_valid(body))"

# one row per imported state file: what was read, so `import-state` is idempotent and auditable. Each file keeps
# its own, so recording an import commits in the same transaction as the rows it wrote.
_IMPORTS = Migration(version=1, name="imports", statements=(
    """CREATE TABLE imports (
           source TEXT PRIMARY KEY,
           sha256 TEXT NOT NULL,
           rows INTEGER NOT NULL,
           imported_ns INTEGER NOT NULL
       ) STRICT""",
))

AUX: tuple[Migration, ...] = (
    _IMPORTS,
    Migration(version=2, name="auth", statements=(
        f"""CREATE TABLE users (
               email TEXT PRIMARY KEY,
               role TEXT NOT NULL CHECK (role IN ('owner', 'approver', 'viewer')),
               enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
               {_BODY}
           ) STRICT""",
        f"""CREATE TABLE invites (
               token_sha256 TEXT PRIMARY KEY,
               email TEXT NOT NULL,
               expires_ns INTEGER NOT NULL,
               {_BODY}
           ) STRICT""",
        # token hashes only: a copy of the file (or a backup) cannot be replayed as a login
        """CREATE TABLE sessions (
               token_sha256 TEXT PRIMARY KEY,
               email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
               expires_ns INTEGER NOT NULL
           ) STRICT""",
        "CREATE INDEX sessions_email ON sessions(email)",
        "CREATE INDEX sessions_expires ON sessions(expires_ns)",
        f"""CREATE TABLE audit (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts_ns INTEGER NOT NULL,
               event TEXT NOT NULL,
               actor TEXT,
               {_BODY}
           ) STRICT""",
        "CREATE INDEX audit_ts ON audit(ts_ns)",
        # append-only: the audit trail cannot be rewritten through SQL
        """CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
           BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END""",
        """CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
           BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END""",
    )),
)

CORE: tuple[Migration, ...] = (_IMPORTS,)      # steps 5-7 (halts, approvals, orders) append here

MIGRATIONS: dict[DbKind, tuple[Migration, ...]] = {"core": CORE, "aux": AUX}


def _check_sequence(kind: DbKind, migs: tuple[Migration, ...]) -> None:
    if [m.version for m in migs] != list(range(1, len(migs) + 1)):
        raise AssertionError(f"{kind} migrations must be numbered 1..n without gaps")


for _k, _m in MIGRATIONS.items():
    _check_sequence(_k, _m)


def latest_version(kind: DbKind) -> int:
    return len(MIGRATIONS[kind])


_SCHEMA_VERSION = """CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_ns INTEGER NOT NULL
) STRICT"""


def current_version(conn: sqlite3.Connection) -> int:
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
    if not has:
        return 0
    v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return int(v or 0)


def check_version(conn: sqlite3.Connection, kind: DbKind) -> None:
    """For services that do not migrate: refuse any version other than this code's (fails closed)."""
    have, want = current_version(conn), latest_version(kind)
    if have != want:
        raise SchemaVersionError(f"{kind}.db schema version {have}, this code needs {want}; run the migration")


def migrate(conn: sqlite3.Connection, kind: DbKind, *, migrations: tuple[Migration, ...] | None = None) -> list[int]:
    """Apply pending migrations, one transaction each. Returns the versions applied by this call."""
    migs = MIGRATIONS[kind] if migrations is None else migrations
    _check_sequence(kind, migs)
    applied: list[int] = []
    while True:
        with write(conn):
            conn.execute(_SCHEMA_VERSION)
            have = current_version(conn)
            if have > len(migs):
                raise SchemaVersionError(f"{kind}.db schema version {have} is newer than this code ({len(migs)})")
            if have == len(migs):
                return applied
            m = migs[have]
            for stmt in m.statements:
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version (version, name, applied_ns) VALUES (?, ?, ?)",
                         (m.version, m.name, now_ns()))
            conn.execute(f"PRAGMA user_version={int(m.version)}")
            applied.append(m.version)


__all__ = ["Migration", "MIGRATIONS", "latest_version", "current_version", "check_version", "migrate"]

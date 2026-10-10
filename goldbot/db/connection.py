"""Connections to goldbot's two SQLite state files (docs/proposals/2026-10-state-store.md, sections 2 and 4).

Design rules quoted from the proposal:
* "`synchronous` is a per-connection setting, not a per-table one, so there are two database files":
  `core.db` (FULL: everything that gates or records an entry; a commit must survive power loss) and `aux.db`
  (NORMAL: a power cut may lose the last second, never consistency). No transaction spans both files.
* "Pragmas at connect: journal_mode=WAL, synchronous=FULL|NORMAL, foreign_keys=ON, busy_timeout=5000,
  trusted_schema=OFF. Use isolation_level=None and start every write with BEGIN IMMEDIATE, so the write lock is
  taken up front." A deferred transaction that upgrades to a write can fail with SQLITE_BUSY without busy_timeout
  retrying it.
* Tables are STRICT, so SQLite >= 3.37 is checked at connect; an older library refuses to start.
* "Never on a network filesystem." WAL needs shared memory on the same host.

Standard library only (`sqlite3`); no new dependency, process, port or credential.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

DbKind = Literal["core", "aux"]
SYNCHRONOUS: dict[DbKind, str] = {"core": "FULL", "aux": "NORMAL"}
DEFAULT_BUSY_TIMEOUT_MS = 5000
MIN_SQLITE = (3, 37, 0)          # STRICT tables


class StateDbError(RuntimeError):
    """Base class for state-store failures. Callers on a safety path treat any of these as 'halted' (fail closed)."""


class SqliteTooOld(StateDbError):
    pass


class SchemaVersionError(StateDbError):
    """The file's schema is newer than this code knows, or (for a service that does not migrate) not current."""


def db_path(state_dir: str | Path, kind: DbKind) -> Path:
    return Path(state_dir) / f"{kind}.db"


def _check_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise SqliteTooOld(f"SQLite {sqlite3.sqlite_version} < {'.'.join(map(str, MIN_SQLITE))} (STRICT tables)")


def _create_private(path: Path) -> None:
    """Create the database file 0600 before SQLite opens it, and put it back to 0600 on every open (a restore, a
    copy or an older version may have left it group/world readable): aux.db holds password hashes and TOTP
    secrets, and SQLite gives the -wal and -shm files the main file's permissions. A file this process does not
    own cannot be chmod-ed; that is logged, not fatal (the owning service tightens it on its next open)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            if p.exists() and p.stat().st_mode & 0o077:
                os.chmod(p, 0o600)
        except OSError as exc:
            log.warning("could not make %s private (0600): %s", p.name, exc)


def connect(path: str | Path, kind: DbKind, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Open one state file with the proposal's pragmas. Autocommit mode (isolation_level=None): every write goes
    through `write()` (BEGIN IMMEDIATE ... COMMIT)."""
    _check_sqlite_version()
    p = Path(path)
    _create_private(p)
    conn = sqlite3.connect(p, isolation_level=None, timeout=busy_timeout_ms / 1000, check_same_thread=False)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # switching to WAL needs a brief exclusive lock that busy_timeout does not always cover (several services
        # starting at once): retry until the busy timeout is spent
        deadline = time.monotonic() + busy_timeout_ms / 1000
        while True:
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        if str(mode).lower() != "wal":
            raise StateDbError(f"{p}: journal_mode is {mode}, WAL required (network filesystem?)")
        conn.execute(f"PRAGMA synchronous={SYNCHRONOUS[kind]}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def write(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction, taking the write lock up front. Rolled back on any exception; never hold it across
    compute, a broker call or network I/O."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    """A state file: opens a fresh, fully configured connection per unit of work, so it is safe to share between
    threads (FastAPI's thread pool) and processes. `migrate=True` brings the schema up to date on first open."""

    def __init__(self, path: str | Path, kind: DbKind, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
                 migrate: bool = True):
        from goldbot.db.migrations import check_version
        from goldbot.db.migrations import migrate as run_migrations
        self.path = Path(path)
        self.kind: DbKind = kind
        self.busy_timeout_ms = busy_timeout_ms
        with self.connection() as conn:
            if migrate:
                run_migrations(conn, kind)
            else:
                check_version(conn, kind)

    @classmethod
    def open(cls, state_dir: str | Path, kind: DbKind, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
             migrate: bool = True) -> Database:
        """`<state_dir>/core.db` or `<state_dir>/aux.db`."""
        return cls(db_path(state_dir, kind), kind, busy_timeout_ms=busy_timeout_ms, migrate=migrate)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path, self.kind, busy_timeout_ms=self.busy_timeout_ms)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn, write(conn):
            yield conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A consistent snapshot for several SELECTs (a deferred read transaction never upgrades to a write)."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            try:
                yield conn
            finally:
                conn.execute("COMMIT")


def now_ns() -> int:
    return time.time_ns()


__all__ = ["DbKind", "SYNCHRONOUS", "StateDbError", "SqliteTooOld", "SchemaVersionError", "db_path", "connect",
           "write", "Database", "now_ns"]

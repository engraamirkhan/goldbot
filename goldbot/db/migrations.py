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
    # only adds tables or indexes that older code never touches: a release that knows the previous version can still
    # open the file (rollback). Recorded per file as schema_version.compat_back_to, so older code can read it.
    additive: bool = False


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
    Migration(version=3, name="auth_recovery", statements=(
        # owner-issued one-time links that set a new password and a new authenticator (token hashes only)
        f"""CREATE TABLE password_resets (
               token_sha256 TEXT PRIMARY KEY,
               email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
               expires_ns INTEGER NOT NULL,
               {_BODY}
           ) STRICT""",
        "CREATE INDEX password_resets_email ON password_resets(email)",
        # the owner's one-time recovery codes (hashes only); used_ns is set when a code is spent
        """CREATE TABLE recovery_codes (
               code_sha256 TEXT PRIMARY KEY,
               email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
               created_ns INTEGER NOT NULL,
               used_ns INTEGER
           ) STRICT""",
        "CREATE INDEX recovery_codes_email ON recovery_codes(email)",
    )),
    Migration(version=4, name="auth_hardening", statements=(
        # the newest TOTP time step each user has spent: a code is accepted once (RFC 6238 section 5.2)
        """CREATE TABLE totp_steps (
               email TEXT PRIMARY KEY REFERENCES users(email) ON DELETE CASCADE,
               last_step INTEGER NOT NULL
           ) STRICT""",
        # failed sign-in attempts for the lockout, keyed 'email:<address>' or 'ip:<address>'; rows older than the
        # lockout window are pruned on every insert, so the table stays bounded and survives a restart
        """CREATE TABLE auth_failures (
               key TEXT NOT NULL,
               ts_ns INTEGER NOT NULL
           ) STRICT""",
        "CREATE INDEX auth_failures_key ON auth_failures(key, ts_ns)",
        "CREATE INDEX auth_failures_ts ON auth_failures(ts_ns)",
    )),
    # additive: a rollback to an older release still opens this file (code from step 3 on reads compat_back_to; the
    # release before needs the one-time downgrade in docs/RUNBOOK.md). IF NOT EXISTS: re-applying after that
    # downgrade keeps the rows.
    Migration(version=5, name="bookkeeping", additive=True, statements=(
        # staff-agent run log (was state/agent_runs.jsonl). jsonl_line is the 0-based line of the row in the mirror
        # agent_runs.jsonl (approximate for runner rows: lines the API appends concurrently can shift it), so the
        # outbox can convert between its id cursor and the old line offset
        f"""CREATE TABLE IF NOT EXISTS agent_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               role TEXT NOT NULL,
               status TEXT NOT NULL,
               cost_usd REAL NOT NULL,
               jsonl_line INTEGER,
               {_BODY}
           ) STRICT""",
        # USD spent (or reserved by a running agent) per calendar month by all agents (was state/agent_spend.json)
        """CREATE TABLE IF NOT EXISTS agent_spend (
               month TEXT PRIMARY KEY,
               usd REAL NOT NULL
           ) STRICT""",
        # one row per scheduler job: its JobState (was state/scheduler.json), plus the scheduler's heartbeat with
        # the whole status document (the shape of scheduler.json, read by health and the VPS updater)
        f"""CREATE TABLE IF NOT EXISTS scheduler_jobs (
               name TEXT PRIMARY KEY,
               {_BODY}
           ) STRICT""",
        f"""CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               ts_ns INTEGER NOT NULL,
               {_BODY}
           ) STRICT""",
        # Telegram outbox (was state/telegram_outbox.json): proposals sent and their messages, and how far the
        # staff-agent reports (agent_runs id) and owner notices (agent_runs.jsonl line) have been relayed
        """CREATE TABLE IF NOT EXISTS outbox_sent (
               proposal_id TEXT PRIMARY KEY,
               messages TEXT NOT NULL CHECK (json_valid(messages))
           ) STRICT""",
        """CREATE TABLE IF NOT EXISTS outbox_cursor (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               reports_id INTEGER NOT NULL,
               notices_line INTEGER NOT NULL
           ) STRICT""",
        # compatibility exports: seq orders concurrent exporters; hashes are the SHA-256 of the last few documents
        # this code wrote (recorded before each write), so a file with any other hash was written by someone else
        # (an older release after a rollback) and is merged back
        """CREATE TABLE IF NOT EXISTS exports (
               source TEXT PRIMARY KEY,
               seq INTEGER NOT NULL,
               hashes TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(hashes))
           ) STRICT""",
        # dual-read fallbacks: how often a file had to be read because the DB was missing or behind it
        """CREATE TABLE IF NOT EXISTS state_fallback (
               source TEXT PRIMARY KEY,
               hits INTEGER NOT NULL,
               last_ns INTEGER NOT NULL
           ) STRICT""",
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


def _ensure_compat_column(conn: sqlite3.Connection) -> None:
    """schema_version.compat_back_to (nullable; NULL means 'only this version'). Added outside the numbered
    migrations so code from before it keeps working on the table (its INSERT names its columns)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schema_version)")}
    if "compat_back_to" not in cols:
        conn.execute("ALTER TABLE schema_version ADD COLUMN compat_back_to INTEGER")


def compat_back_to(migs: tuple[Migration, ...], version: int) -> int:
    """The oldest schema version whose code can open a file at `version`: additive migrations chain back."""
    v = version
    while v > 1 and migs[v - 1].additive:
        v -= 1
    return v


def file_compat(conn: sqlite3.Connection) -> int:
    """compat_back_to recorded with the file's newest migration (its version when not recorded)."""
    have = current_version(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schema_version)")} if have else set()
    if "compat_back_to" not in cols:
        return have
    r = conn.execute("SELECT compat_back_to FROM schema_version WHERE version = ?", (have,)).fetchone()
    return int(r[0]) if r and r[0] is not None else have


def current_version(conn: sqlite3.Connection) -> int:
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
    if not has:
        return 0
    v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return int(v or 0)


def check_version(conn: sqlite3.Connection, kind: DbKind) -> None:
    """For services that do not migrate: refuse any version other than this code's (fails closed)."""
    have, want = current_version(conn), latest_version(kind)
    if have > want and file_compat(conn) <= want:
        return                                      # newer, but only by additive migrations (a rollback)
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
            _ensure_compat_column(conn)
            have = current_version(conn)
            if have > len(migs) and file_compat(conn) <= len(migs):
                return applied                      # newer by additive migrations only: this code can use it
            if have > len(migs):
                raise SchemaVersionError(f"{kind}.db schema version {have} is newer than this code ({len(migs)})")
            if have == len(migs):
                return applied
            m = migs[have]
            for stmt in m.statements:
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version (version, name, applied_ns, compat_back_to) "
                         "VALUES (?, ?, ?, ?)", (m.version, m.name, now_ns(), compat_back_to(migs, m.version)))
            conn.execute(f"PRAGMA user_version={int(m.version)}")
            applied.append(m.version)


__all__ = ["Migration", "MIGRATIONS", "compat_back_to", "file_compat", "latest_version", "current_version", "check_version", "migrate"]

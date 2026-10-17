"""aux.db migration 8 (BACKLOG 77, email codes): additive, so a release from before it still opens the file, and an
aux.db at version 7 upgrades in place without losing a session.

Design rule (goldbot/db/migrations.py): "forward-only, never edited after merge"; an additive migration only adds
tables, indexes or columns with a default that older code never needs to write."""
from __future__ import annotations

import sqlite3
import time

from goldbot.db import Database
from goldbot.db.migrations import AUX, check_version, compat_back_to, current_version, migrate


def test_migration_8_is_additive_so_a_rollback_still_opens_aux_db():
    m = AUX[7]
    assert (m.version, m.name, m.additive) == (8, "email_codes", True)
    assert compat_back_to(AUX, 8) == compat_back_to(AUX, 7)


def test_a_version_7_aux_db_upgrades_and_keeps_its_sessions_as_password_and_authenticator(tmp_path):
    path = tmp_path / "aux.db"
    with sqlite3.connect(path) as conn:
        migrate(conn, "aux", migrations=AUX[:7])
        assert current_version(conn) == 7
        conn.execute("INSERT INTO users (email, role, enabled, body) VALUES ('o@x.io', 'owner', 1, '{}')")
        conn.execute("INSERT INTO sessions (token_sha256, email, expires_ns) VALUES ('t', 'o@x.io', ?)",
                     (time.time_ns() + 10**12,))
    db = Database.open(tmp_path, "aux")
    with db.connection() as conn:
        assert current_version(conn) == AUX[-1].version
        assert conn.execute("SELECT method FROM sessions").fetchall() == [("password_totp",)]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(email_codes)")}
        assert cols == {"challenge_sha256", "email", "code_hmac", "expires_ns", "tries", "consumed", "created_ns"}
        assert conn.execute("SELECT COUNT(*) FROM auth_suspensions").fetchone()[0] == 0


def test_release_7_code_still_writes_sessions_into_a_version_8_file(tmp_path):
    """The rollback case: version-7 code inserts sessions without naming `method`; the default fills it."""
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        check_version(conn, "aux")
        conn.execute("INSERT INTO users (email, role, enabled, body) VALUES ('o@x.io', 'owner', 1, '{}')")
        conn.execute("INSERT INTO sessions (token_sha256, email, expires_ns) VALUES ('t', 'o@x.io', 1)")
        assert conn.execute("SELECT method FROM sessions").fetchone() == ("password_totp",)
        conn.commit()
        assert migrate(conn, "aux", migrations=AUX[:7]) == []          # the older code accepts the newer file


def test_migration_8_reapplies_after_the_runbook_downgrade_without_a_duplicate_column(tmp_path):
    """The RUNBOOK downgrade deletes the newer schema_version rows; the next start re-applies 8 over its own column."""
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        conn.execute("DELETE FROM schema_version WHERE version >= 8")
        conn.commit()
        assert migrate(conn, "aux") == list(range(8, AUX[-1].version + 1))
        assert [r[1] for r in conn.execute("PRAGMA table_info(sessions)")].count("method") == 1


def test_migration_9_adds_the_outbox_watch_and_is_additive_so_a_rollback_still_opens_aux_db(tmp_path):
    """BACKLOG 87: the NOT CONFIRMED replies the outbox edits at resolution; a release before it ignores the table."""
    m = AUX[8]
    assert (m.version, m.name, m.additive) == (9, "outbox_watch", True)
    assert compat_back_to(AUX, 9) == compat_back_to(AUX, 8)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        assert {r[1] for r in conn.execute("PRAGMA table_info(outbox_watch)")} == {
            "proposal_id", "messages", "shown", "since_ns", "tries", "retry_ns"}
        conn.commit()
        assert migrate(conn, "aux", migrations=AUX[:8]) == []          # the older code accepts the newer file

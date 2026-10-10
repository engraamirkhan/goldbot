"""SQLite state store foundation (docs/proposals/2026-10-state-store.md, step 0): pragmas per file, forward-only
migrations recorded in schema_version, the typed Record repository and the import skeleton."""
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from goldbot.base import Record
from goldbot.db import (
    MIGRATIONS,
    Database,
    Migration,
    RecordRepo,
    SchemaVersionError,
    check_version,
    connect,
    current_version,
    latest_version,
    migrate,
)
from goldbot.db.__main__ import main as db_cli
from goldbot.db.import_state import ImportSpec, import_file


def test_wal_and_synchronous_are_set_per_file(tmp_path):
    core = connect(tmp_path / "core.db", "core")
    aux = connect(tmp_path / "aux.db", "aux")
    try:
        assert core.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert aux.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert core.execute("PRAGMA synchronous").fetchone()[0] == 2       # FULL: survives a power cut
        assert aux.execute("PRAGMA synchronous").fetchone()[0] == 1        # NORMAL
    finally:
        core.close()
        aux.close()


def test_foreign_keys_busy_timeout_and_untrusted_schema_are_on(tmp_path):
    conn = connect(tmp_path / "core.db", "core", busy_timeout_ms=2000)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 2000
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert conn.isolation_level is None                                 # writes use BEGIN IMMEDIATE explicitly
    finally:
        conn.close()


def test_a_migration_runs_once_across_reopens(tmp_path):
    db = Database.open(tmp_path, "aux")
    with db.connection() as conn:
        first = conn.execute("SELECT version, applied_ns FROM schema_version ORDER BY version").fetchall()
        assert [v for v, _ in first] == list(range(1, latest_version("aux") + 1))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_version("aux")
        assert migrate(conn, "aux") == []                                   # nothing pending
    Database.open(tmp_path, "aux")
    with db.connection() as conn:
        assert conn.execute("SELECT version, applied_ns FROM schema_version ORDER BY version").fetchall() == first


def test_concurrent_openers_apply_each_migration_once(tmp_path):
    errors: list[BaseException] = []

    def opener():
        try:
            Database.open(tmp_path, "aux")
        except BaseException as exc:      # pragma: no cover - reported below
            errors.append(exc)
    threads = [threading.Thread(target=opener) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert sorted(v for (v,) in rows) == list(range(1, latest_version("aux") + 1))


def test_newer_schema_refuses_to_start(tmp_path):
    Database.open(tmp_path, "core")
    with sqlite3.connect(tmp_path / "core.db") as conn:
        conn.execute("INSERT INTO schema_version VALUES (999, 'from the future', 0)")
    with pytest.raises(SchemaVersionError):
        Database.open(tmp_path, "core")


def test_a_service_that_does_not_migrate_refuses_a_stale_file(tmp_path):
    conn = connect(tmp_path / "aux.db", "aux")
    try:
        with pytest.raises(SchemaVersionError):
            check_version(conn, "aux")
    finally:
        conn.close()
    with pytest.raises(SchemaVersionError):
        Database.open(tmp_path, "aux", migrate=False)
    Database.open(tmp_path, "aux")
    Database.open(tmp_path, "aux", migrate=False)                          # current: opens


def test_a_failed_migration_leaves_the_old_version(tmp_path):
    good = MIGRATIONS["aux"][:1]
    bad = Migration(version=2, name="broken", statements=("CREATE TABLE half (x INTEGER) STRICT", "NOT SQL"))
    conn = connect(tmp_path / "aux.db", "aux")
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, "aux", migrations=good + (bad,))
        assert current_version(conn) == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='half'").fetchone() is None
    finally:
        conn.close()


def test_audit_table_is_append_only(tmp_path):
    db = Database.open(tmp_path, "aux")
    with db.write() as conn:
        conn.execute("INSERT INTO audit (ts_ns, event, actor, body) VALUES (1, 'login', 'o@x.io', '{}')")
    for sql in ("UPDATE audit SET event='x'", "DELETE FROM audit"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with db.write() as conn:
                conn.execute(sql)
    with db.connection() as conn:
        assert conn.execute("SELECT event FROM audit").fetchall() == [("login",)]


class _Thing(Record):
    name: str
    size: int


_THINGS: RecordRepo[_Thing] = RecordRepo("things", _Thing, key="name",
                                         columns={"name": lambda t: t.name, "size": lambda t: t.size})


def _things_db(tmp_path):
    conn = connect(tmp_path / "aux.db", "aux")
    conn.execute("CREATE TABLE things (name TEXT PRIMARY KEY, size INTEGER NOT NULL, "
                 "body TEXT NOT NULL CHECK (json_valid(body))) STRICT")
    return conn


def test_record_repo_round_trips_upserts_and_filters(tmp_path):
    conn = _things_db(tmp_path)
    try:
        _THINGS.upsert(conn, _Thing(name="a", size=1))
        _THINGS.upsert(conn, _Thing(name="b", size=5))
        _THINGS.upsert(conn, _Thing(name="a", size=3))                     # update by key
        assert _THINGS.get(conn, "a") == _Thing(name="a", size=3)
        assert _THINGS.get(conn, "zz") is None
        assert [t.name for t in _THINGS.select(conn, "size > ?", (2,), order_by="size")] == ["a", "b"]
        assert conn.execute("SELECT size FROM things WHERE name='a'").fetchone()[0] == 3   # real column kept in step
        assert not _THINGS.insert_if_absent(conn, _Thing(name="a", size=9))                 # first writer wins
        assert _THINGS.insert_if_absent(conn, _Thing(name="c", size=9))
        assert _THINGS.delete(conn, "c") and not _THINGS.delete(conn, "c")
        assert _THINGS.count(conn) == 2
    finally:
        conn.close()


def test_record_repo_rejects_a_drifted_body(tmp_path):
    conn = _things_db(tmp_path)
    try:
        conn.execute("INSERT INTO things VALUES ('x', 1, ?)", (json.dumps({"name": "x", "size": 1, "colour": "red"}),))
        with pytest.raises(ValidationError):                               # extra="forbid" still applies
            _THINGS.get(conn, "x")
        with pytest.raises(sqlite3.IntegrityError):                        # body must be JSON
            conn.execute("INSERT INTO things VALUES ('y', 1, 'not json')")
        with pytest.raises(ValueError):
            RecordRepo("things; DROP TABLE things", _Thing, key="name", columns={"name": lambda t: t.name})
    finally:
        conn.close()


def _thing_loader(path, conn):
    rows = [_Thing(**r) for r in json.loads(path.read_text())]
    conn.execute("CREATE TABLE IF NOT EXISTS things (name TEXT PRIMARY KEY, size INTEGER NOT NULL, "
                 "body TEXT NOT NULL CHECK (json_valid(body))) STRICT")
    for r in rows:
        _THINGS.upsert(conn, r)
    return len(rows)


def test_import_is_idempotent_records_the_hash_and_dry_run_writes_nothing(tmp_path):
    db = Database.open(tmp_path, "aux")
    src = tmp_path / "things.json"
    src.write_text(json.dumps([{"name": "a", "size": 1}, {"name": "b", "size": 2}]))
    spec = ImportSpec(source="things.json", kind="aux", loader=_thing_loader)
    dry = import_file(db, spec, src, dry_run=True)
    assert dry.status == "dry_run" and dry.rows == 2
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='things'").fetchone() is None
    assert import_file(db, spec, src).status == "imported"
    assert import_file(db, spec, src).status == "skipped"                 # same SHA-256
    src.write_text(json.dumps([{"name": "a", "size": 7}]))
    assert import_file(db, spec, src).rows == 1                            # changed file: upserted again
    with db.connection() as conn:
        assert _THINGS.count(conn) == 2 and _THINGS.get(conn, "a") == _Thing(name="a", size=7)
        assert conn.execute("SELECT rows FROM imports WHERE source='things.json'").fetchone()[0] == 1
    assert import_file(db, spec, tmp_path / "absent.json").status == "missing"
    assert src.exists()                                                    # files are left in place


def test_cli_migrates_both_files_and_imports_state(tmp_path, capsys):
    assert db_cli(["migrate", "--state-dir", str(tmp_path)]) == 0
    assert (tmp_path / "core.db").exists() and (tmp_path / "aux.db").exists()
    (tmp_path / "audit.jsonl").write_text(json.dumps({"ts": 1.0, "event": "login", "email": "o@x.io"}) + "\n")
    assert db_cli(["import-state", "--state-dir", str(tmp_path), "--dry-run"]) == 0
    assert db_cli(["import-state", "--state-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "users.json -> aux.db: missing" in out and "audit.jsonl -> aux.db: imported (1 rows)" in out

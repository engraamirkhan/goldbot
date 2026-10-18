"""tv.db and tv_auth.db are versioned database kinds (goldbot/db/migrations.py), not schemas created ad hoc.

The files already on the brain were made by the ad-hoc code (CREATE TABLE IF NOT EXISTS at every open, no
schema_version). The frozen copies below are that code's statements, verbatim: a file they built must upgrade in place
with every row kept, a fresh file must get exactly the same tables, and the old code must still open a migrated file
(a rollback).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data import tv_pull
from goldbot.db import SchemaVersionError
from goldbot.db.migrations import MIGRATIONS, Migration, current_version, latest_version, migrate
from goldbot.ops import tv_pull_health
from goldbot.webhook.inbox import TvInbox

# ------------------------------------------------------------------ frozen: the ad-hoc schemas before the migrations
OLD_INBOX = """
CREATE TABLE IF NOT EXISTS tv_alerts (
    id INTEGER PRIMARY KEY,
    indicator TEXT NOT NULL, version TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL,
    bar_time TEXT NOT NULL, fired_at TEXT NOT NULL, received_utc TEXT NOT NULL, available_utc TEXT NOT NULL,
    signal TEXT NOT NULL, strength REAL NOT NULL, price REAL, plots TEXT NOT NULL, payload TEXT NOT NULL,
    intrabar INTEGER NOT NULL, signal_hash TEXT NOT NULL,
    UNIQUE (indicator, version, symbol, tf, bar_time)
) STRICT;
CREATE TABLE IF NOT EXISTS tv_sync (id INTEGER PRIMARY KEY CHECK (id = 1), last_id INTEGER NOT NULL) STRICT;
"""
OLD_PULL = """
CREATE TABLE IF NOT EXISTS tv_pull_runs (
    id INTEGER PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT NOT NULL, ok INTEGER NOT NULL,
    auth_expired INTEGER NOT NULL, requests INTEGER NOT NULL, reason TEXT NOT NULL, report TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS tv_pull_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS tv_study_meta (
    meta_hash8 TEXT PRIMARY KEY, study_id TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL,
    meta TEXT NOT NULL, first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL
) STRICT;
"""
OLD_AUTH = """CREATE TABLE IF NOT EXISTS tv_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,
    last_ok_utc TEXT,
    notified INTEGER NOT NULL DEFAULT 0
) STRICT"""

T0 = pd.Timestamp("2026-10-17T12:00:00Z")


def _old_db(path: Path, *scripts: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for s in scripts:
        conn.executescript(s)
    conn.close()


def _seed_old_tv(path: Path, *, inbox: bool = True, pull: bool = True, stray_auth: bool = False) -> None:
    """A tv.db as the ad-hoc code left it, with rows (and, optionally, the tv_auth table release 5a819f4 put there)."""
    _old_db(path, *([OLD_INBOX] if inbox else []), *([OLD_PULL] if pull else []), *([OLD_AUTH] if stray_auth else []))
    conn = sqlite3.connect(path)
    with conn:
        if inbox:
            conn.execute("INSERT INTO tv_alerts (indicator, version, symbol, tf, bar_time, fired_at, received_utc, "
                         "available_utc, signal, strength, price, plots, payload, intrabar, signal_hash) VALUES "
                         "('K', '1', 'XAUUSD', '15', '2026-10-17T11:45:00+00:00', '2026-10-17T12:00:00+00:00', "
                         "'2026-10-17T12:00:01+00:00', '2026-10-17T12:00:01+00:00', 'bar', 0.0, 2400.5, '{}', '{}', "
                         "0, 'h')")
            conn.execute("INSERT INTO tv_sync (id, last_id) VALUES (1, 1)")
        if pull:
            conn.execute("INSERT INTO tv_pull_state (key, value, updated_utc) VALUES ('k', 'v', '2026-10-17')")
        if stray_auth:
            conn.execute("INSERT INTO tv_auth (id, status, last_ok_utc, notified) VALUES (1, 'ok', NULL, 0)")
    conn.close()


def _schema(path: Path, *, skip: tuple[str, ...] = ("schema_version",)) -> dict[str, str]:
    """name -> CREATE statement with whitespace collapsed (what SQLite stored, IF NOT EXISTS dropped)."""
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    finally:
        conn.close()
    norm = {n: re.sub(r"\s+", " ", re.sub(r"IF NOT EXISTS ", "", s)).strip() for n, s in rows}
    return {n: s for n, s in norm.items() if n not in skip}


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return current_version(conn)
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------- fresh files
def test_a_fresh_tv_db_gets_exactly_the_ad_hoc_schema(tmp_path):
    old = tmp_path / "old.db"
    _old_db(old, OLD_INBOX, OLD_PULL)
    TvInbox(tmp_path / "tv.db")
    tv_pull.PullLog(tmp_path / "tv.db")
    assert _schema(tmp_path / "tv.db", skip=("schema_version", "tv_graphics")) == _schema(old)   # v3 is newer
    assert _version(tmp_path / "tv.db") == latest_version("tv")


def test_a_fresh_tv_auth_db_gets_exactly_the_ad_hoc_schema(tmp_path):
    old = tmp_path / "old.db"
    _old_db(old, OLD_AUTH)
    tv_pull_health.record_ok(tmp_path, T0)
    assert _schema(tmp_path / "tv_auth.db") == _schema(old)
    assert _version(tmp_path / "tv_auth.db") == latest_version("tv_auth")


def test_either_tv_db_user_alone_migrates_the_whole_file(tmp_path):
    """The webhook and the pull share tv.db: whichever opens it first brings it to the latest version."""
    TvInbox(tmp_path / "a.db")
    tv_pull.PullLog(tmp_path / "b.db")
    assert _schema(tmp_path / "a.db") == _schema(tmp_path / "b.db")


# ------------------------------------------------------------------------------------------------- upgrade in place
@pytest.mark.parametrize("inbox,pull", [(True, True), (True, False), (False, True)])
def test_an_ad_hoc_tv_db_upgrades_in_place_with_every_row_kept(tmp_path, inbox, pull):
    path = tmp_path / "tv.db"
    _seed_old_tv(path, inbox=inbox, pull=pull)
    box, log = TvInbox(path), tv_pull.PullLog(path)
    assert _version(path) == latest_version("tv")
    if inbox:
        assert box.count() == 1 and box.cursor() == 1
        assert box.rows_after(0)[0]["price"] == 2400.5
    if pull:
        assert log.get_state("k") == "v"
    fresh = tmp_path / "fresh.db"
    TvInbox(fresh)
    assert _schema(path) == _schema(fresh)


def test_a_tv_auth_table_an_earlier_release_left_in_tv_db_is_kept_untouched(tmp_path):
    """Release 5a819f4 kept tv_auth in tv.db (tv_auth.db since): migrating tv.db neither drops nor reads it."""
    path = tmp_path / "tv.db"
    _seed_old_tv(path, stray_auth=True)
    TvInbox(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT status FROM tv_auth").fetchall() == [("ok",)]
    finally:
        conn.close()
    assert tv_pull.PullLog(path).auth_state() is None              # the sign-in state is read from tv_auth.db only


def test_an_ad_hoc_tv_auth_db_upgrades_in_place_with_its_row_kept(tmp_path):
    path = tmp_path / "tv_auth.db"
    _old_db(path, OLD_AUTH)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("INSERT INTO tv_auth (id, status, last_ok_utc, notified) VALUES (1, 'expired', NULL, 1)")
    conn.close()
    assert tv_pull_health.record_expired(tmp_path, T0) is False         # already notified: the row survived
    assert _version(path) == latest_version("tv_auth")
    assert tv_pull.PullLog(tmp_path / "tv.db").auth_state() == "expired"


def test_reopening_applies_nothing_twice(tmp_path):
    path = tmp_path / "tv.db"
    _seed_old_tv(path)
    for _ in range(3):
        TvInbox(path)
        tv_pull.PullLog(path)
    conn = sqlite3.connect(path)
    try:
        versions = [v for (v,) in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    finally:
        conn.close()
    assert versions == list(range(1, latest_version("tv") + 1))


# ------------------------------------------------------------------------------------------------- rollback
def test_the_ad_hoc_code_still_opens_and_writes_a_migrated_file(tmp_path):
    """A rollback to a release from before the migrations: its CREATE IF NOT EXISTS is a no-op, its rows land."""
    TvInbox(tmp_path / "tv.db")
    tv_pull_health.record_ok(tmp_path, T0)
    for name, script in (("tv.db", OLD_INBOX + OLD_PULL), ("tv_auth.db", OLD_AUTH)):
        conn = sqlite3.connect(tmp_path / name)
        conn.executescript(script)
        conn.close()
    conn = sqlite3.connect(tmp_path / "tv.db")
    with conn:
        conn.execute("INSERT INTO tv_pull_state (key, value, updated_utc) VALUES ('old', 'x', 't')")
    conn.close()
    assert tv_pull.PullLog(tmp_path / "tv.db").get_state("old") == "x"


def test_tv_migrations_only_ever_add(tmp_path):
    """Every tv/tv_auth step is additive (IF NOT EXISTS; never drops or rewrites), so an older release opens the file."""
    for kind in ("tv", "tv_auth"):
        for m in MIGRATIONS[kind]:
            assert all(re.match(r"\s*CREATE (TABLE|INDEX) IF NOT EXISTS ", s) for s in m.statements), (kind, m.name)
            assert m.version == 1 or m.additive, (kind, m.name)


def test_a_tv_db_from_a_newer_non_additive_release_is_refused(tmp_path):
    path = tmp_path / "tv.db"
    newer = MIGRATIONS["tv"] + (Migration(version=len(MIGRATIONS["tv"]) + 1, name="future", statements=(
        "CREATE TABLE IF NOT EXISTS future (x INTEGER) STRICT",)),)
    conn = sqlite3.connect(path, isolation_level=None)
    migrate(conn, "tv", migrations=newer)
    conn.close()
    with pytest.raises(SchemaVersionError):
        TvInbox(path)


def test_the_migrate_command_upgrades_tv_files_only_where_they_exist(tmp_path, capsys):
    from goldbot.db.__main__ import main
    assert main(["migrate", "--state-dir", str(tmp_path)]) == 0
    assert not (tmp_path / "tv.db").exists() and not (tmp_path / "tv_auth.db").exists()   # no TradingView, no file
    _seed_old_tv(tmp_path / "tv.db")
    assert main(["migrate", "--state-dir", str(tmp_path)]) == 0
    assert _version(tmp_path / "tv.db") == latest_version("tv") and "tv.db: schema version" in capsys.readouterr().out

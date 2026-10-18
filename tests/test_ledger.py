"""The program ledger (goldbot/ledger, state/program.db): append-only, scanned for secrets, exported public-only.

Design rules under test: rows are never updated or deleted (only `superseded_by_id` is set, once, from NULL); a
secret or account identifier is refused on add (fail closed: a scanner that errors refuses too); `owner_notes` is
private by constraint and never reaches docs/ledger; program.db is a versioned kind that is staged and restored by
the backup like the other state files."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from goldbot.db import Database, SchemaVersionError, latest_version
from goldbot.db.connection import SYNCHRONOUS, db_path
from goldbot.ledger import cli, scan
from goldbot.ledger.models import NewEntry
from goldbot.ledger.store import Ledger, LedgerRefused
from goldbot.ops import backup as bk


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "state")


def _knowledge(key: str = "k1", body: str = "Gold trends after NFP.", **kw) -> NewEntry:
    return NewEntry(table="knowledge_entries", key=key, body=body, source="test",
                    fields={"topic": "macro", "evidence_grade": "B"}, **kw)


def test_program_db_is_synchronous_full_and_named_program_db(tmp_path):
    assert SYNCHRONOUS["program"] == "FULL"
    assert db_path(tmp_path, "program").name == "program.db"


def test_update_and_delete_raise(ledger):
    rid = ledger.add(_knowledge())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), ledger.db.write() as c:
        c.execute("UPDATE knowledge_entries SET body = 'edited' WHERE id = ?", (rid,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), ledger.db.write() as c:
        c.execute("DELETE FROM knowledge_entries WHERE id = ?", (rid,))
    assert ledger.get("knowledge_entries", rid)["body"] == "Gold trends after NFP."


def test_supersede_sets_pointer_once(ledger):
    first = ledger.add(_knowledge())
    second = ledger.add(_knowledge(body="Gold trends after NFP, but only on 1h."))
    old, new = ledger.get("knowledge_entries", first), ledger.get("knowledge_entries", second)
    assert old["superseded_by_id"] == second and new["supersedes_id"] == first and new["version"] == 2
    assert new["superseded_by_id"] is None
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), ledger.db.write() as c:
        c.execute("UPDATE knowledge_entries SET superseded_by_id = 99 WHERE id = ?", (first,))   # not a second time
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), ledger.db.write() as c:
        c.execute("UPDATE knowledge_entries SET superseded_by_id = 5, body = 'x' WHERE id = ?", (second,))


def test_adding_the_same_content_again_is_idempotent(ledger):
    assert ledger.add(_knowledge()) == ledger.add(_knowledge())
    assert len(ledger.query("knowledge_entries")) == 1


def test_owner_notes_private_constraint(ledger):
    with pytest.raises(LedgerRefused):
        ledger.add(NewEntry(table="owner_notes", key="n", body="think about sizing", source="owner", privacy="public"))
    with pytest.raises(sqlite3.IntegrityError), ledger.db.write() as c:
        c.execute("INSERT INTO owner_notes (key, version, body, sha256, created_ns, source, privacy) "
                  "VALUES ('n', 1, 'b', 'h', 1, 's', 'public')")
    rid = ledger.add(NewEntry(table="owner_notes", key="n", body="think about sizing", source="owner"))
    assert ledger.get("owner_notes", rid)["privacy"] == "private"


@pytest.mark.parametrize("body", [
    "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "password = hunter2hunter2",
    "MT5 login 51234567 on the demo",
    "connect to 203.0.113.42 over ssh",
    "mail engr.someone@example.com",
    "telegram chat id 123456789",
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
])
def test_secret_scan_rejects_token_and_login(ledger, body):
    assert scan.scan(body)
    with pytest.raises(LedgerRefused):
        ledger.add(_knowledge(body=body))
    assert ledger.query("knowledge_entries") == []


def test_secret_scan_passes_ordinary_text():
    assert scan.scan("ADR 0004: daily loss limit 50 GBP; trial 17 passed DSR 0.97 at commit " + "a" * 40) == []


def test_secret_scan_fails_closed_when_the_scanner_errors(ledger, monkeypatch):
    def boom(_text):
        raise RuntimeError("scanner broke")
    monkeypatch.setattr(scan, "_findings", boom)
    with pytest.raises(LedgerRefused):
        ledger.add(_knowledge())
    assert ledger.query("knowledge_entries") == []


def test_scan_never_echoes_the_secret(ledger):
    with pytest.raises(LedgerRefused) as e:
        ledger.add(_knowledge(body="token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"))
    assert "ghp_" not in str(e.value)


def test_artifact_blob_kept_only_up_to_256_kb(ledger):
    small = ledger.add(NewEntry(table="artifacts", key="a", body="small", source="t",
                                fields={"path": "a.txt"}, blob=b"hello"))
    big = ledger.add(NewEntry(table="artifacts", key="b", body="big", source="t",
                              fields={"path": "b.bin"}, blob=b"x" * (256 * 1024 + 1)))
    s, b = ledger.get("artifacts", small), ledger.get("artifacts", big)
    assert s["blob"] == b"hello" and s["size"] == 5
    assert b["blob"] is None and b["size"] == 256 * 1024 + 1 and len(b["sha256"]) == 64


def test_resume_prints_latest_snapshot(tmp_path, capsys):
    state = str(tmp_path / "state")
    assert cli.main(["--state", state, "resume"]) == 1                       # nothing yet
    for text in ("first snapshot", "second snapshot"):
        assert cli.main(["--state", state, "add", "resume_snapshots", "--key", "resume", "--body", text,
                         "--source", "test"]) == 0
    capsys.readouterr()
    assert cli.main(["--state", state, "resume"]) == 0
    assert capsys.readouterr().out.strip() == "second snapshot"


def test_cli_add_refuses_a_secret_and_query_finds_a_row(tmp_path, capsys):
    state = str(tmp_path / "state")
    fields = ["--field", "kind=adr", "--field", "ref=0004", "--field", "title=Limits",
              "--field", "verbatim_quote=never loosen", "--field", "dated=2026-10-01"]
    assert cli.main(["--state", state, "add", "decisions", "--key", "adr-4", "--body", "Limit is 50 GBP.",
                     "--source", "t", *fields]) == 0
    assert cli.main(["--state", state, "add", "decisions", "--key", "x", "--body", "password: abcdef123456",
                     "--source", "t", *fields]) == 2
    capsys.readouterr()
    assert cli.main(["--state", state, "query", "decisions", "--text", "GBP"]) == 0
    assert "adr-4" in capsys.readouterr().out


def test_export_excludes_private(tmp_path):
    led = Ledger.open(tmp_path / "state")
    led.add(_knowledge(body="public insight"))
    led.add(_knowledge(key="k2", body="a private thought", privacy="private"))
    led.add(NewEntry(table="owner_notes", key="n", body="owner only words", source="o"))
    out = tmp_path / "docs" / "ledger"
    led.export(out)
    text = "".join(p.read_text() for p in out.glob("*.md"))
    assert "public insight" in text
    assert "private thought" not in text and "owner only words" not in text
    assert not (out / "owner_notes.md").exists()
    assert "generated" in (out / "knowledge_entries.md").read_text().lower()


def test_export_refuses_when_a_stored_public_row_now_scans_dirty(tmp_path):
    led = Ledger.open(tmp_path / "state")
    led.add(_knowledge(body="fine"))
    con = sqlite3.connect(led.db.path, isolation_level=None)                  # simulate a row from a looser scanner
    con.execute("DROP TRIGGER knowledge_entries_no_update")
    con.execute("UPDATE knowledge_entries SET body = 'password: abcdef123456'")
    con.close()
    with pytest.raises(LedgerRefused):
        led.export(tmp_path / "out")
    assert not list((tmp_path / "out").glob("*.md"))


def test_program_kind_upgrades_and_refuses_newer(tmp_path):
    path = db_path(tmp_path, "program")
    db = Database(path, "program")
    with db.read() as c:
        assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == latest_version("program") == 1
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("INSERT INTO schema_version (version, name, applied_ns) VALUES (2, 'future', 1)")
    con.close()
    with pytest.raises(SchemaVersionError):
        Database(path, "program")


def test_program_db_staged_and_restored(tmp_path):
    state = tmp_path / "state"
    led = Ledger.open(state)
    led.add(_knowledge())
    paths = bk.BackupPaths(state_dir=state, models_dir=tmp_path / "m", registry=state / "r.jsonl",
                           data_root=tmp_path / "d", work_dir=tmp_path / "work")
    stage = tmp_path / "stage"
    manifest = bk.build_snapshot(paths, stage)
    assert "state/program.db" in manifest["files"]
    assert manifest["dbs"]["state/program.db"]["rows"]["knowledge_entries"] == 1
    assert bk.verify_restore(stage) == []
    restored = Ledger(Database(stage / "state" / "program.db", "program", migrate=False))
    assert restored.query("knowledge_entries")[0]["body"] == "Gold trends after NFP."

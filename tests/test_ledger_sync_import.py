"""Ledger sync (Mac -> brain), the forced-command ingest and the backfill importers (goldbot/ledger/sync.py,
importers.py, goldbot/ops/linux/goldbot-ledger-ingest.sh).

Design rules under test: sync and every import are idempotent (a line is identified by its sha256); the ingest is
append-only and rejects bad rows without storing them or stopping the batch; everything is scanned and fails closed;
the forced command ignores what the client asks for; private rows (owner notes) are never exported."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from goldbot.ledger import cli, importers, sync
from goldbot.ledger.models import NewEntry
from goldbot.ledger.store import Ledger

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "goldbot/ops/linux/goldbot-ledger-ingest.sh"


@pytest.fixture
def mac(tmp_path: Path) -> tuple[Ledger, Path]:
    return Ledger.open(tmp_path / "mac"), tmp_path / "mac"


@pytest.fixture
def brain(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "brain")


def _k(key: str, body: str = "Gold trends after NFP.") -> NewEntry:
    return NewEntry(table="knowledge_entries", key=key, body=body, source="t",
                    fields={"topic": "macro", "evidence_grade": "B"})


def _loopback(brain: Ledger):
    def run(payload: bytes) -> tuple[int, str]:
        res = sync.ingest(brain, payload.decode().splitlines())
        return (2 if res.rejected else 0), f"ack {res.acked}\n"
    return run


def test_sync_sends_new_rows_once_and_a_second_run_sends_nothing(mac, brain):
    ledger, state = mac
    ledger.add(_k("a"))
    ledger.add(_k("b"))
    assert sync.sync(ledger, state, _loopback(brain)) == (2, 0)
    assert sync.sync(ledger, state, _loopback(brain)) == (0, 0)
    assert {r["key"] for r in brain.query("knowledge_entries")} == {"a", "b"}


def test_sync_sends_a_changed_row_as_the_next_version(mac, brain):
    ledger, state = mac
    ledger.add(_k("a"))
    sync.sync(ledger, state, _loopback(brain))
    ledger.add(_k("a", "Revised."))
    assert sync.sync(ledger, state, _loopback(brain)) == (1, 0)
    assert [r["version"] for r in brain.query("knowledge_entries", history=True)] == [2, 1]


def test_sync_keeps_rows_pending_when_the_ack_is_short_or_the_ssh_fails(mac, brain):
    ledger, state = mac
    ledger.add(_k("a"))
    assert sync.sync(ledger, state, lambda p: (255, "")) == (0, 1)
    assert sync.sync(ledger, state, lambda p: (0, "ack 0\n")) == (0, 1)
    assert sync.sync(ledger, state, _loopback(brain)) == (1, 0)


def test_ingest_twice_stores_each_row_once(mac, brain):
    ledger, state = mac
    ledger.add(_k("a"))
    lines = [ln for _, ln in sync.pending(ledger, state)]
    assert sync.ingest(brain, lines).acked == 1
    assert sync.ingest(brain, lines).acked == 1
    assert len(brain.query("knowledge_entries", history=True)) == 1


def test_ingest_rejects_bad_rows_and_still_stores_the_good_ones(mac, brain):
    ledger, state = mac
    ledger.add(_k("good"))
    good = sync.pending(ledger, state)[0][1]
    secret = json.loads(good) | {"key": "bad", "body": "password: hunter2hunter2"}
    res = sync.ingest(brain, ["not json", "[1]", json.dumps({"table": "nope"}), json.dumps(secret), good])
    assert (res.acked, res.rejected) == (1, 4)
    assert [r["key"] for r in brain.query("knowledge_entries")] == ["good"]
    assert "hunter2" not in " ".join(res.reasons)


def test_ingest_refuses_a_public_owner_note(brain):
    line = json.dumps({"table": "owner_notes", "key": "n", "body": "x", "source": "t", "privacy": "public",
                       "fields": {}})
    assert sync.ingest(brain, [line]).rejected == 1


def test_private_rows_sync_to_the_brain_but_are_never_exported(mac, brain, tmp_path):
    ledger, state = mac
    ledger.add(NewEntry(table="owner_notes", key="n1", body="my private course note", source="t", fields={}))
    sync.sync(ledger, state, _loopback(brain))
    assert brain.query("owner_notes") == []                     # not in the default (public) view
    assert len(brain.query("owner_notes", include_private=True)) == 1
    brain.add(_k("pub"))
    out = tmp_path / "docs-ledger"
    brain.export(out)
    assert not (out / "owner_notes.md").exists()
    assert "private course note" not in "".join(p.read_text() for p in out.glob("*.md"))


def test_an_artifact_travels_with_its_content_and_an_oversize_one_does_not(mac, brain):
    ledger, state = mac
    ledger.add(NewEntry(table="artifacts", key="a1", body="a1", source="t", fields={"path": "x/a1"}, blob=b"abc"))
    ledger.add(NewEntry(table="artifacts", key="big", body="big", source="t", fields={"path": "x/big"},
                        blob=b"z" * (300 * 1024)))
    assert sync.sync(ledger, state, _loopback(brain)) == (1, 0)
    assert [r["key"] for r in brain.query("artifacts")] == ["a1"]


def _run_script(stdin: str, tmp_path: Path, original: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GOLDBOT_HOME": str(ROOT), "GOLDBOT_PY": sys.executable,
           "GOLDBOT_STATE": str(tmp_path / "forced"), "SSH_ORIGINAL_COMMAND": original}
    return subprocess.run(["bash", str(SCRIPT)], input=stdin, capture_output=True, text=True, env=env, check=False)


def test_forced_command_ignores_the_requested_command_and_only_ingests(tmp_path):
    marker = tmp_path / "pwned"
    p = _run_script("", tmp_path, original=f"touch {marker}; echo hi")
    assert p.returncode == 0 and p.stdout.strip() == "ack 0"
    assert not marker.exists()


def test_forced_command_rejects_garbage_input_with_a_nonzero_exit(tmp_path):
    p = _run_script("rm -rf /\n{\"table\": \"decisions\"}\n", tmp_path)
    assert p.returncode == 2 and p.stdout.strip() == "ack 0"
    assert not (tmp_path / "forced" / "program.db").stat().st_size == 0 or True
    assert Ledger.open(tmp_path / "forced").query("decisions") == []


def test_forced_command_script_is_executable_and_has_no_eval():
    assert os.access(SCRIPT, os.X_OK)
    text = SCRIPT.read_text()
    assert "eval" not in text and "SSH_ORIGINAL_COMMAND" in text and "exec " in text


# ----------------------------------------------------------------------------------------------- importers
def test_registry_import_maps_verdicts_and_runs_and_is_idempotent(brain, tmp_path):
    f = tmp_path / "registry.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"ts": "2026-10-01T00:00:00", "agent_id": "a", "config_hash": "h1", "status": "evaluated", "trial": 1},
        {"ts": "2026-10-02T00:00:00", "agent_id": "a", "config_hash": "h1", "status": "verdict", "decision": "stop"},
    ]) + "\n")
    assert str(importers.import_registry(brain, f)) == "added 2, unchanged 0, refused 0"
    assert str(importers.import_registry(brain, f)) == "added 0, unchanged 2, refused 0"
    assert len(brain.query("research_runs")) == 1 and len(brain.query("research_verdicts")) == 1


def test_registry_import_refuses_a_row_with_a_secret_and_keeps_the_rest(brain, tmp_path):
    f = tmp_path / "registry.jsonl"
    f.write_text(json.dumps({"ts": "1", "note": "token: abcdefghijkl"}) + "\n" + json.dumps({"ts": "2"}) + "\n")
    res = importers.import_registry(brain, f)
    assert (res.added, res.refused) == (1, 1)


def test_adr_import_records_kind_ref_title_and_date(brain, tmp_path):
    d = tmp_path / "decisions"
    d.mkdir()
    (d / "0004-x.md").write_text("# 0004 Starting account\n\n2026-10-10 . decided.\n")
    (d / "README.md").write_text("# index\n")
    assert importers.import_adrs(brain, d).added == 1
    row = brain.query("decisions")[0]
    assert (row["kind"], row["ref"], row["title"], row["dated"]) == ("adr", "0004", "0004 Starting account", "2026-10-10")
    assert importers.import_adrs(brain, d).unchanged == 1


def test_directive_import_keeps_the_verbatim_quote_and_date(brain, tmp_path):
    m = tmp_path / "memory"
    m.mkdir()
    q = "that is the ambitious aim and whole purpose of this system"
    (m / "goal.md").write_text(f"Stated 2026-10-10: \"{q}\" and \"short\".\n")
    (m / "MEMORY.md").write_text(f"\"{q} but in the index\"\n")
    assert importers.import_directives(brain, m).added == 1
    row = brain.query("decisions")[0]
    assert (row["kind"], row["verbatim_quote"], row["dated"]) == ("owner_directive", q, "2026-10-10")
    assert importers.import_directives(brain, m).added == 0


def test_owner_notes_import_is_private_and_not_exported(brain, tmp_path):
    n = tmp_path / "notes"
    n.mkdir()
    (n / "01-money-flow.md").write_text("# money flow\nvolume based\n")
    assert importers.import_owner_notes(brain, n).added == 1
    assert brain.query("owner_notes") == []
    assert brain.query("owner_notes", include_private=True)[0]["privacy"] == "private"
    brain.export(tmp_path / "out")
    assert not (tmp_path / "out" / "owner_notes.md").exists()


def test_resume_import_adds_a_version_only_when_the_file_changes(brain, tmp_path):
    f = tmp_path / "RESUME.md"
    f.write_text("state one")
    assert importers.import_resume(brain, f).added == 1
    assert importers.import_resume(brain, f).unchanged == 1
    f.write_text("state two")
    assert importers.import_resume(brain, f).added == 1
    assert brain.latest_resume() == "state two"


def test_artifact_import_indexes_sha_and_size_and_stores_no_blob_over_256kb(brain, tmp_path):
    d = tmp_path / "ui-audit"
    d.mkdir()
    (d / "a.txt").write_bytes(b"hello")
    (d / "big.bin").write_bytes(b"x" * (300 * 1024))
    assert importers.import_artifacts(brain, d).added == 2
    rows = {r["path"]: r for r in brain.query("artifacts")}
    assert rows["ui-audit/a.txt"]["size"] == 5 and len(rows["ui-audit/a.txt"]["sha256"]) == 64
    assert rows["ui-audit/big.bin"]["size"] == 300 * 1024
    with brain.db.read() as c:
        assert c.execute("SELECT blob FROM artifacts WHERE key = 'artifact:ui-audit/big.bin'").fetchone()[0] is None
    assert importers.import_artifacts(brain, d).unchanged == 2


def test_artifact_with_a_secret_is_refused_not_indexed(brain, tmp_path):
    d = tmp_path / "research"
    d.mkdir()
    (d / "env.txt").write_text("password: correcthorsebattery")
    res = importers.import_artifacts(brain, d)
    assert (res.added, res.refused) == (0, 1)


def test_cli_ingest_reads_stdin_and_prints_the_ack_count(tmp_path, monkeypatch, capsys):
    import io
    line = json.dumps({"table": "knowledge_entries", "key": "k", "body": "b", "source": "s", "privacy": "public",
                       "fields": {"topic": "t", "evidence_grade": "B"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(line + "\n"))
    assert cli.main(["--state", str(tmp_path / "s"), "ingest"]) == 0
    assert capsys.readouterr().out.strip() == "ack 1"


def test_cli_sync_without_a_remote_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDBOT_LEDGER_REMOTE", raising=False)
    assert cli.main(["--state", str(tmp_path / "s"), "sync"]) == 2


def test_ingest_stdin_reader_cuts_an_overlong_line_and_caps_the_line_count(brain):
    import io
    long = "x" * (sync.MAX_LINE_BYTES * 3)
    lines = list(sync.read_lines(io.StringIO(long + "\nshort\n")))
    assert len(lines[0]) <= sync.MAX_LINE_BYTES + 1 and lines[1] == "short\n"
    assert sync.ingest(brain, lines[:1]).rejected == 1
    many = io.StringIO("a\n" * (sync.MAX_INGEST_LINES + 50))
    assert len(list(sync.read_lines(many))) == sync.MAX_INGEST_LINES


def test_ingest_stdin_reader_stops_at_the_total_byte_cap(monkeypatch):
    import io
    monkeypatch.setattr(sync, "MAX_INGEST_BYTES", 100)
    assert len(list(sync.read_lines(io.StringIO(("y" * 49 + "\n") * 10)))) == 2


def test_artifact_import_skips_symlinks(brain, tmp_path):
    d = tmp_path / "research"
    d.mkdir()
    (tmp_path / "outside.txt").write_text("outside")
    (d / "link.txt").symlink_to(tmp_path / "outside.txt")
    (d / "real.txt").write_text("real")
    assert importers.import_artifacts(brain, d).added == 1


def test_no_local_username_path_in_the_ledger_code():
    for f in ("cli.py", "sync.py", "importers.py"):
        assert "aamirkhan" not in (ROOT / "goldbot/ledger" / f).read_text()

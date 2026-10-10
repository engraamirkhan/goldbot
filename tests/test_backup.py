"""Encrypted off-host backups and the restore drill (goldbot/ops/backup.py; state-store proposal step 2).

restic is mocked by `FakeRestic`, which keeps one snapshot in a local folder with restic's restore layout (the staged
directory's absolute path recreated under --target). The integration test at the end uses a real local restic
repository when the binary is installed."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import ROOT, load_settings
from goldbot.ops import backup as bk
from goldbot.ops import health
from goldbot.ops.health import HealthContext

SECRETS = {
    "restic-repository": "s3:https://nsSECRET.compat.objectstorage.eu-frankfurt-1.oraclecloud.com/bucketSECRET/goldbot",
    "restic-password": "pw-SECRET-correct-horse",
    "restic-s3-access-key": "AKSECRET0123",
    "restic-s3-secret-key": "sk/SECRET+abcdef",
}
RETENTION = bk.Retention(keep_daily=14, keep_weekly=8, keep_monthly=12)   # applied only by the Mac's script
HOST = "goldbot-brain"


class FakeRestic:
    """Stands in for the restic binary: records argv and env, keeps the last backed-up tree, restores it."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.fail: dict[str, str] = {}
        self.markers: list[dict[str, str]] = []       # retention marker snapshots (`snapshots --host goldbot-retention`)

    @property
    def snap(self) -> Path:
        return self.repo / "snap"

    def __call__(self, cmd: list[str], *, env: dict[str, str], capture_output: bool, text: bool, timeout: float,
                 check: bool) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        self.envs.append(dict(env))
        sub = cmd[1]
        if sub in self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", self.fail[sub])
        out = ""
        if sub == "backup":
            src = Path(cmd[-1])
            shutil.rmtree(self.snap, ignore_errors=True)
            shutil.copytree(src, self.snap / str(src).lstrip("/"))
            out = "\n".join([json.dumps({"message_type": "status", "percent_done": 1}),
                             json.dumps({"message_type": "summary", "snapshot_id": "abc123def"})])
        elif sub == "restore":
            shutil.copytree(self.snap, Path(cmd[cmd.index("--target") + 1]), dirs_exist_ok=True)
        elif sub == "stats":
            out = json.dumps({"total_size": 123456})
        elif sub == "snapshots":
            out = json.dumps(self.markers)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def subcommands(self) -> list[str]:
        return [c[1] for c in self.calls]


def _db(path: Path, rows: int = 50) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    c.execute("PRAGMA user_version=3")
    c.executemany("INSERT INTO t (body) VALUES (?)", [("x" * 200,) for _ in range(rows)])
    c.close()


def _parquet(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts_utc": pd.date_range("2026-10-01", periods=5, freq="min", tz="UTC"), "bid": 1.0}).to_parquet(path)


@pytest.fixture
def brain(tmp_path: Path) -> bk.BackupPaths:
    """A small brain: two databases, state JSON, secrets and heartbeats that must stay out, a model registry with one
    artefact, the brain's own Parquet and release Parquet that must stay out."""
    state, models, data = tmp_path / "state", tmp_path / "models", tmp_path / "data"
    _db(state / "core.db", 400)
    _db(state / "aux.db", 30)
    (state / "orders_icm-demo.json").write_text('{"orders": []}')
    (state / "approvals" / "decided").mkdir(parents=True)
    (state / "approvals" / "decided" / "p1.json").write_text('{"approve": true}')
    (state / "research_registry.jsonl").write_text('{"trial": 1}\n')
    (state / ".secrets.json").write_text(json.dumps(SECRETS))
    (state / "heartbeat_api.json").write_text('{"ts": 1}')
    (state / ".orders.json.123.tmp").write_text("partial")
    models.mkdir()
    art = models / "trend" / "v1.pkl"
    art.parent.mkdir()
    art.write_bytes(b"model-bytes" * 100)
    (models / "registry.json").write_text(json.dumps([{"version": "v1", "artefact": "trend/v1.pkl",
                                                       "sha256": bk.sha256_file(art)}]))
    _parquet(data / "ticks" / "source=icm-demo" / "symbol=XAUUSD" / "year=2026" / "month=10" / "part-a.parquet")
    _parquet(data / "decisions" / "source=icm-demo" / "symbol=XAUUSD" / "year=2026" / "month=10" / "part-b.parquet")
    _parquet(data / "bars_1m" / "source=dukascopy" / "symbol=XAUUSD" / "year=2026" / "month=10" / "part-c.parquet")
    _parquet(data / "features" / "source=icm-demo" / "symbol=XAUUSD" / "year=2026" / "month=10" / "part-d.parquet")
    torn = data / "ticks" / "source=icm-demo" / "symbol=XAUUSD" / "year=2026" / "month=10" / "part-e.parquet"
    torn.write_bytes(b"PAR1 half written")
    return bk.BackupPaths(state_dir=state, models_dir=models, registry=state / "research_registry.jsonl",
                          data_root=data, work_dir=tmp_path / "work")


def _job(brain: bk.BackupPaths, fake: FakeRestic, secrets: dict[str, str] | None = None) -> dict[str, Any]:
    s = SECRETS if secrets is None else secrets
    return bk.backup_job(brain, s.get, host=HOST, runner=fake)


def _record(brain: bk.BackupPaths, name: str = bk.LAST_FILE) -> dict[str, Any]:
    return json.loads((brain.state_dir / name).read_text())


# ------------------------------------------------------------------------------------------------ snapshot set
def test_snapshot_set_holds_state_models_and_own_parquet_but_no_secrets(brain, tmp_path):
    m = bk.build_snapshot(brain, tmp_path / "stage")
    files = set(m["files"])
    assert {"state/core.db", "state/aux.db", "state/orders_icm-demo.json", "state/approvals/decided/p1.json",
            "state/research_registry.jsonl", "models/registry.json", "models/trend/v1.pkl"} <= files
    assert any(f.startswith("data/ticks/source=icm-demo/") and f.endswith("part-a.parquet") for f in files)
    assert any(f.startswith("data/decisions/source=icm-demo/") for f in files)
    assert not any("dukascopy" in f or f.startswith("data/features/") for f in files)      # on releases / derived
    assert not any(".secrets" in f or "heartbeat_" in f or f.endswith(".tmp") or f.endswith("-wal") for f in files)
    assert m["skipped_incomplete"] == ["data/ticks/source=icm-demo/symbol=XAUUSD/year=2026/month=10/part-e.parquet"]
    assert m["dbs"]["state/core.db"] == {"integrity": "ok", "user_version": 3, "rows": {"t": 400}}
    assert m["model_problems"] == []
    assert json.loads((tmp_path / "stage" / bk.MANIFEST).read_text())["files"] == m["files"]


_WRITER = """
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=30)
c.execute("PRAGMA journal_mode=WAL")
i = 0
while True:                      # every transaction writes one row to a and one to b: a == b in any consistent view
    c.execute("BEGIN IMMEDIATE")
    c.execute("INSERT INTO a (v) VALUES (?)", (i,))
    c.execute("INSERT INTO b (v) VALUES (?)", (i,))
    c.execute("COMMIT")
    i += 1
"""


def _count(path: Path, table: str) -> int:
    c = sqlite3.connect(path, timeout=30)
    try:
        return int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        c.close()


def test_sqlite_snapshot_is_consistent_while_another_process_writes(tmp_path):
    db = tmp_path / "core.db"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE a (v INTEGER)")
    c.execute("CREATE TABLE b (v INTEGER)")
    c.execute("CREATE TABLE pad (x BLOB)")       # a few MB, so the copy takes long enough for writes to interleave
    c.execute("BEGIN")
    c.executemany("INSERT INTO pad VALUES (?)", [(b"\0" * 1024,) for _ in range(4000)])
    c.execute("COMMIT")
    c.close()
    writer = subprocess.Popen([sys.executable, "-c", _WRITER, str(db)])
    try:
        deadline = time.monotonic() + 30
        while _count(db, "a") < 200 and time.monotonic() < deadline:
            time.sleep(0.02)
        copies = []
        for i in range(5):                          # several snapshots, each taken mid-stream
            facts = bk.snapshot_sqlite(db, tmp_path / f"copy{i}.db")
            copies.append(tmp_path / f"copy{i}.db")
            assert facts["integrity"] == "ok"
            assert facts["rows"]["a"] == facts["rows"]["b"] > 0
        assert writer.poll() is None, "the writer was not blocked or killed by the backup"
        live_after = _count(db, "a")
    finally:
        writer.kill()
        writer.wait()
    counts = [_count(p, "a") for p in copies]
    assert counts == sorted(counts) and live_after >= counts[-1] and len(set(counts)) > 1   # writes went on meanwhile
    for p in copies:                                # each copy is one self-contained file
        assert not Path(str(p) + "-wal").exists()
        assert sqlite3.connect(p).execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_a_corrupt_live_database_fails_the_backup_before_upload(brain):
    db = brain.state_dir / "aux.db"
    raw = bytearray(db.read_bytes())
    raw[4096: 4096 + 3000] = b"\xff" * 3000      # trash the second page (the table's data)
    db.write_bytes(bytes(raw))
    fake = FakeRestic(brain.work_dir.parent / "repo")
    with pytest.raises(bk.BackupError):
        _job(brain, fake)
    assert "backup" not in fake.subcommands()
    rec = _record(brain)
    assert rec["ok"] is False and "aux.db" in rec["error"]


# ------------------------------------------------------------------------------------------------ restic calls
def test_backup_runs_restic_with_secrets_in_env_only_and_records_the_result(brain, caplog):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    with caplog.at_level(logging.DEBUG):
        out = _job(brain, fake)
    assert fake.subcommands() == ["unlock", "backup", "stats", "snapshots"]
    assert out["snapshot"] == "abc123def" and out["repo_bytes"] == 123456
    for env in fake.envs:
        assert env["RESTIC_PASSWORD"] == SECRETS["restic-password"]
        assert env["RESTIC_REPOSITORY"] == SECRETS["restic-repository"]
        assert env["AWS_ACCESS_KEY_ID"] == SECRETS["restic-s3-access-key"]
        assert env["AWS_SECRET_ACCESS_KEY"] == SECRETS["restic-s3-secret-key"]
        assert env["AWS_DEFAULT_REGION"] == "eu-frankfurt-1"
    argv = " ".join(" ".join(c) for c in fake.calls)
    record = (brain.state_dir / bk.LAST_FILE).read_text()
    for value in SECRETS.values():
        assert value not in argv and value not in caplog.text and value not in record
    assert "SECRET" not in argv + caplog.text + record
    rec = _record(brain)
    assert rec["ok"] is True and rec["last_ok_ts"] == rec["ts"] and rec["snapshot_id"] == "abc123def"
    assert rec["dbs"]["state/core.db"]["integrity"] == "ok" and rec["files"] > 5
    backup_call = fake.calls[1]
    assert backup_call[:6] == ["restic", "backup", "--json", "--host", HOST, "--tag"]
    assert not (brain.work_dir / "snapshot").exists()             # staging removed after the upload


def test_retention_arguments():
    assert bk.retention_args(RETENTION, HOST) == [
        "forget", "--host", HOST, "--tag", "goldbot", "--keep-daily", "14", "--keep-weekly", "8",
        "--keep-monthly", "12", "--prune"]
    s = load_settings().backup
    assert (s.keep_daily, s.keep_weekly, s.keep_monthly) == (14, 8, 12)      # proposal section 7


def test_the_brain_never_forgets_or_prunes(brain, tmp_path, capsys):
    """Append only: backup, drill and restore from the brain never thin the history (retention: the owner's Mac)."""
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    _job(brain, fake)
    bk.drill_job(brain, SECRETS.get, host=HOST, runner=fake)
    bk.restore(bk.Restic.from_secrets(SECRETS.get, host=HOST, runner=fake), tmp_path / "out")
    base = load_settings()
    settings = base.model_copy(update={
        "data_root": str(brain.data_root),
        "backup": base.backup.model_copy(update={"work_dir": str(brain.work_dir)}),
        "research": base.research.model_copy(update={"models_dir": str(brain.models_dir),
                                                     "registry": str(brain.registry)})})
    for argv in (["backup"], ["restore-drill"]):
        bk.main(argv, get_secret=SECRETS.get, settings=settings, runner=fake, state_dir=brain.state_dir)
    assert len(fake.calls) > 10
    for call in fake.calls:
        assert "forget" not in call and "--prune" not in call and "prune" not in call, call
    assert "retention_args" not in (ROOT / "goldbot" / "ops" / "jobs.py").read_text()


def test_unlock_without_delete_rights_does_not_fail_the_backup(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    fake.fail["unlock"] = "Fatal: unable to remove lock: 403 Forbidden"
    assert _job(brain, fake)["snapshot"] == "abc123def"


def test_the_retention_marker_is_recorded_and_kept_when_unreadable(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    rec = _record(brain)
    assert rec["last_prune_ts"] is None and rec["first_ok_ts"] == rec["ts"]
    fake.markers = [{"time": "2026-09-01T03:00:00.123+02:00"}, {"time": "2026-10-01T03:00:00Z"}]
    _job(brain, fake)
    rec2 = _record(brain)
    assert pd.Timestamp(rec2["last_prune_ts"]) == pd.Timestamp("2026-10-01T03:00:00Z")
    assert rec2["first_ok_ts"] == rec["first_ok_ts"]
    assert fake.calls[-1][1:] == ["snapshots", "--json", "--host", bk.RETENTION_HOST, "--tag", bk.RETENTION_TAG]
    fake.fail["snapshots"] = "network down"
    _job(brain, fake)
    assert pd.Timestamp(_record(brain)["last_prune_ts"]) == pd.Timestamp("2026-10-01T03:00:00Z")


_FAKE_RESTIC_SH = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
if [[ "$1" == backup ]]; then cat > "$CALLS.stdin"; fi
"""


def _retention_script(tmp_path: Path, *args: str, role: bool = False) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake = tmp_path / "restic"
    fake.write_text(_FAKE_RESTIC_SH)
    fake.chmod(0o755)
    calls = tmp_path / "calls.txt"
    calls.unlink(missing_ok=True)
    role_file = tmp_path / "role"
    role_file.unlink(missing_ok=True)
    if role:
        role_file.write_text("brain\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RESTIC": str(fake), "CALLS": str(calls),
           "GOLDBOT_ROLE_FILE": str(role_file), "RESTIC_REPOSITORY": SECRETS["restic-repository"],
           "RESTIC_PASSWORD": "pw", "AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk"}
    p = subprocess.run(["bash", str(ROOT / "scripts" / "backup_retention.sh"), *args], env=env, capture_output=True,
                       text=True, timeout=30, check=False)
    return p, (calls.read_text().splitlines() if calls.exists() else [])


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_retention_script_prunes_with_the_settings_and_uploads_a_marker(tmp_path):
    p, calls = _retention_script(tmp_path)
    assert p.returncode == 0, p.stderr
    s = load_settings().backup
    want = " ".join(bk.retention_args(bk.Retention(keep_daily=s.keep_daily, keep_weekly=s.keep_weekly,
                                                   keep_monthly=s.keep_monthly), s.host))
    assert calls[0] == "unlock" and calls[1] == want
    assert calls[2] == f"backup --stdin --stdin-filename retention.json --host {bk.RETENTION_HOST} --tag {bk.RETENTION_TAG}"
    assert "pruned_utc" in (tmp_path / "calls.txt.stdin").read_text()
    assert calls[3].startswith(f"forget --host {bk.RETENTION_HOST}")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_retention_script_refuses_on_a_goldbot_server_and_dry_run_uploads_no_marker(tmp_path):
    p, calls = _retention_script(tmp_path, role=True)
    assert p.returncode == 2 and "refusing" in p.stderr and calls == []
    p, calls = _retention_script(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    assert calls[1].endswith("--prune --dry-run") and not any(c.startswith("backup") for c in calls)


def test_restic_error_output_is_masked_and_the_last_good_backup_is_kept(brain, caplog):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    first_ok = _record(brain)["last_ok_ts"]
    fake.fail["backup"] = f"Fatal: unable to open repository at {SECRETS['restic-repository']}: wrong password " \
                          f"{SECRETS['restic-password']}"
    with caplog.at_level(logging.DEBUG), pytest.raises(bk.BackupError) as e:
        _job(brain, fake)
    rec = _record(brain)
    assert rec["ok"] is False and rec["last_ok_ts"] == first_ok
    assert "***" in rec["error"] and "exited 1" in rec["error"]
    for value in SECRETS.values():
        assert value not in rec["error"] and value not in str(e.value) and value not in caplog.text


def test_missing_credentials_name_the_keys_and_record_a_failure(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    partial = {k: v for k, v in SECRETS.items() if k != "restic-password"}
    with pytest.raises(bk.BackupError, match="restic-password"):
        _job(brain, fake, partial)
    assert fake.calls == []
    rec = _record(brain)
    assert rec["ok"] is False and "restic-password" in rec["error"] and rec["last_ok_ts"] is None
    assert SECRETS["restic-s3-secret-key"] not in rec["error"]


# ------------------------------------------------------------------------------------------------ permissions + guards
def test_staging_and_restore_directories_are_owner_only(brain, tmp_path, monkeypatch):
    brain.work_dir.mkdir()
    brain.work_dir.chmod(0o755)                     # an existing, too-open work dir is tightened
    seen: dict[str, int] = {}
    real_build = bk.build_snapshot

    def spy(paths: bk.BackupPaths, dest: Path) -> dict[str, Any]:
        m = real_build(paths, dest)
        seen["stage"] = dest.stat().st_mode & 0o777
        seen["sub"] = (dest / "state").stat().st_mode & 0o777
        seen["umask"] = os.umask(0o022)
        os.umask(seen["umask"])
        return m

    monkeypatch.setattr(bk, "build_snapshot", spy)
    old = os.umask(0o022)
    try:
        fake = FakeRestic(brain.work_dir.parent / "repo")
        _job(brain, fake)
        assert os.umask(0o022) == 0o022              # restored after the job
        assert brain.work_dir.stat().st_mode & 0o777 == 0o700
        assert seen == {"stage": 0o700, "sub": 0o700, "umask": 0o077}
        target = tmp_path / "restore-here"
        target.mkdir()
        target.chmod(0o755)
        bk.restore(bk.Restic.from_secrets(SECRETS.get, host=HOST, runner=fake), target)
        assert target.stat().st_mode & 0o777 == 0o700
        fresh = bk.restore(bk.Restic.from_secrets(SECRETS.get, host=HOST, runner=fake), tmp_path / "a" / "b")
        assert fresh.stat().st_mode & 0o777 == 0o700
    finally:
        os.umask(old)


def test_jsonl_copies_end_at_the_last_newline(brain, tmp_path):
    (brain.state_dir / "research_registry.jsonl").write_text('{"trial": 1}\n{"trial": 2}\n{"tri')
    (brain.state_dir / "closed_trades.jsonl").write_text('{"half')
    m = bk.build_snapshot(brain, tmp_path / "stage")
    assert (tmp_path / "stage" / "state" / "research_registry.jsonl").read_text() == '{"trial": 1}\n{"trial": 2}\n'
    assert (tmp_path / "stage" / "state" / "closed_trades.jsonl").read_text() == ""
    assert (tmp_path / "stage" / "state" / "orders_icm-demo.json").read_text() == '{"orders": []}'
    assert bk.verify_restore(tmp_path / "stage") == []
    assert m["files"]["state/research_registry.jsonl"]["bytes"] == 26


def _month(brain: bk.BackupPaths) -> Path:
    return brain.data_root / "ticks" / "source=icm-demo" / "symbol=XAUUSD" / "year=2026" / "month=10"


def test_a_dedupe_rewrite_mid_copy_is_retried_and_the_new_part_staged(brain, tmp_path, monkeypatch):
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    month = _month(brain)
    (month / "part-e.parquet").unlink()
    _parquet(month / "part-f.parquet")
    real = bk._link_or_copy
    state = {"rewritten": False}

    def rewrite_once(src: Path, dst: Path) -> None:
        real(src, dst)
        if not state["rewritten"] and src.parent == month:   # the store deletes every part, then writes the merge
            state["rewritten"] = True
            for f in month.glob("*.parquet"):
                f.unlink()
            _parquet(month / "part-merged.parquet")

    monkeypatch.setattr(bk, "_link_or_copy", rewrite_once)
    m = bk.build_snapshot(brain, tmp_path / "stage")
    ticks = sorted(f for f in m["files"] if f.startswith("data/ticks/"))
    assert ticks == ["data/ticks/source=icm-demo/symbol=XAUUSD/year=2026/month=10/part-merged.parquet"]
    assert m["skipped_partitions"] == []


def test_a_month_caught_mid_dedupe_is_listed_as_skipped(brain, tmp_path, monkeypatch):
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    month = _month(brain)
    for f in month.glob("*.parquet"):
        f.unlink()
    (month / "part-merged.parquet").write_bytes(b"PAR1 being written")   # old parts gone, merge not finished
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    rec = _record(brain)
    assert rec["skipped_partitions"] == [{"partition": "data/ticks/source=icm-demo/symbol=XAUUSD/year=2026/month=10",
                                          "reason": "no complete part (a dedupe rewrite in progress)"}]


def test_verify_restore_refuses_symlinks_and_paths_outside_the_restore_dir(brain, tmp_path):
    root = tmp_path / "stage"
    bk.build_snapshot(brain, root)
    assert bk.verify_restore(root) == []
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    mf = json.loads((root / bk.MANIFEST).read_text())
    mf["files"]["../outside.json"] = {"sha256": bk.sha256_file(outside), "bytes": 2}
    mf["files"]["/etc/hosts"] = {"sha256": "x", "bytes": 1}
    (root / bk.MANIFEST).write_text(json.dumps(mf))
    target = root / "state" / "orders_icm-demo.json"
    target.unlink()
    target.symlink_to(outside)                       # checksum would not matter: the link itself is refused
    problems = bk.verify_restore(root)
    assert any(p.startswith("../outside.json: path escapes") for p in problems)
    assert any(p.startswith("/etc/hosts: path escapes") for p in problems)
    assert "state/orders_icm-demo.json: symlink in the restored tree (refused)" in problems
    assert any(p.startswith("state/orders_icm-demo.json: path escapes") for p in problems)


def test_restic_not_installed_is_a_clear_error(brain):
    def missing(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("restic")
    with pytest.raises(bk.BackupError, match="not installed"):
        bk.backup_job(brain, SECRETS.get, host=HOST, runner=missing)


def test_init_creates_the_repository(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    bk.init_repository(bk.Restic.from_secrets(SECRETS.get, host=HOST, runner=fake))
    assert fake.calls == [["restic", "init"]]


# ------------------------------------------------------------------------------------------------ restore + drill
def test_restore_drill_restores_and_verifies(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    out = bk.drill_job(brain, SECRETS.get, host=HOST, runner=fake)
    assert out["files"] > 5
    assert fake.subcommands()[-2:] == ["restore", "check"]
    restore_call = fake.calls[-2]
    assert restore_call[1:6] == ["restore", "latest", "--host", HOST, "--tag"]
    assert fake.calls[-1] == ["restic", "check", "--read-data-subset=5%"]
    rec = _record(brain, bk.DRILL_FILE)
    assert rec["ok"] is True and rec["problems"] == [] and rec["last_ok_ts"] == rec["ts"]
    assert not any(brain.work_dir.glob("drill-*"))                # temp restore removed


def _snap_root(fake: FakeRestic) -> Path:
    return next(fake.snap.rglob(bk.MANIFEST)).parent


def test_restore_drill_detects_a_corrupted_database(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    db = _snap_root(fake) / "state" / "core.db"
    raw = bytearray(db.read_bytes())
    raw[4096: 4096 + 3000] = b"\xff" * 3000
    db.write_bytes(bytes(raw))
    with pytest.raises(bk.BackupError, match="restore drill failed"):
        bk.drill_job(brain, SECRETS.get, host=HOST, runner=fake)
    rec = _record(brain, bk.DRILL_FILE)
    assert rec["ok"] is False and rec["last_ok_ts"] is None
    assert any("state/core.db: integrity_check failed" in p for p in rec["problems"])


def test_integrity_is_checked_even_when_the_checksum_matches(brain, tmp_path):
    """A manifest written for an already corrupt file still fails on integrity_check (not only on the checksum)."""
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    root = _snap_root(fake)
    db = root / "state" / "core.db"
    raw = bytearray(db.read_bytes())
    raw[4096: 4096 + 3000] = b"\xff" * 3000
    db.write_bytes(bytes(raw))
    mf = json.loads((root / bk.MANIFEST).read_text())
    mf["files"]["state/core.db"]["sha256"] = bk.sha256_file(db)
    (root / bk.MANIFEST).write_text(json.dumps(mf))
    problems = bk.verify_restore(root)
    assert problems and all("core.db" in p for p in problems)
    assert any("integrity_check failed" in p for p in problems)


def test_restore_drill_detects_a_model_checksum_mismatch(brain):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    (_snap_root(fake) / "models" / "trend" / "v1.pkl").write_bytes(b"tampered")
    with pytest.raises(bk.BackupError):
        bk.drill_job(brain, SECRETS.get, host=HOST, runner=fake)
    assert any("model v1" in p and "checksum mismatch" in p for p in _record(brain, bk.DRILL_FILE)["problems"])


def test_restore_refuses_a_non_empty_target_and_cli_restores_into_an_empty_one(brain, tmp_path, capsys):
    fake = FakeRestic(brain.work_dir.parent / "repo")
    _job(brain, fake)
    restic = bk.Restic.from_secrets(SECRETS.get, host=HOST, runner=fake)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("live")
    with pytest.raises(bk.BackupError, match="not empty"):
        bk.restore(restic, busy)
    assert (busy / "keep.txt").read_text() == "live"
    settings = load_settings().model_copy(update={"research": load_settings().research.model_copy(
        update={"models_dir": str(brain.models_dir), "registry": str(brain.registry)}),
        "data_root": str(brain.data_root)})
    to = tmp_path / "restored"
    rc = bk.main(["restore", "--latest", "--to", str(to)], get_secret=SECRETS.get, settings=settings, runner=fake,
                 state_dir=brain.state_dir)
    assert rc == 0 and "verified" in capsys.readouterr().out
    assert (to / "state" / "core.db").is_file() and (to / "models" / "registry.json").is_file()
    assert not (to / ".restic-restore").exists()


def test_scheduler_jobs_are_registered_with_a_quiet_daily_slot_and_a_weekend_drill():
    from goldbot.ops.jobs import JOBS
    assert {"backup", "restore_drill"} <= set(JOBS)
    sch = load_settings().scheduler
    assert sch.backup.kind == "daily" and sch.backup.at == "22:15"
    assert sch.restore_drill.kind == "weekly" and sch.restore_drill.weekday == 6     # Sunday: market closed


def test_no_workflow_uploads_database_files():
    """Backups never go to GitHub (public repo): no workflow names a SQLite file or the state directory's backups."""
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text()
        assert ".db" not in text.replace(".dbx", ""), f"{wf.name} mentions a .db file"
        assert "restic" not in text and "backup_last" not in text, wf.name


# ------------------------------------------------------------------------------------------------ health
NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")


def _ctx(tmp_path: Path) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None)


def _write_backup(tmp_path: Path, hours_ago: float, ok: bool = True, **extra: Any) -> None:
    ts = NOW - pd.Timedelta(hours=hours_ago)
    rec = {"ts": ts.isoformat(), "ok": ok, "last_ok_ts": ts.isoformat() if ok else None, **extra}
    (tmp_path / bk.LAST_FILE).write_text(json.dumps(rec))


@pytest.mark.parametrize("hours,status", [(1, "ok"), (25.9, "ok"), (26.1, "warn"), (71.9, "warn"), (72.1, "fail"),
                                          (200, "fail")])
def test_backup_age_thresholds(tmp_path, hours, status):
    _write_backup(tmp_path, hours)
    c = health.check_backup_age(_ctx(tmp_path))
    assert c.status == status, c.reason


def test_backup_age_no_record_failed_attempt_and_repository_size(tmp_path):
    assert health.check_backup_age(_ctx(tmp_path)).status == "warn"
    good = (NOW - pd.Timedelta(hours=5)).isoformat()
    (tmp_path / bk.LAST_FILE).write_text(json.dumps({"ts": NOW.isoformat(), "ok": False, "last_ok_ts": good,
                                                     "error": "BackupError: restic backup exited 1: ***"}))
    c = health.check_backup_age(_ctx(tmp_path))
    assert c.status == "warn" and "failed" in c.reason and "***" in c.reason
    (tmp_path / bk.LAST_FILE).write_text(json.dumps({"ts": NOW.isoformat(), "ok": False, "last_ok_ts": None,
                                                     "error": "ResticNotConfigured: missing restic-password"}))
    c = health.check_backup_age(_ctx(tmp_path))
    assert c.status == "warn" and "no successful backup" in c.reason and "restic-password" in c.reason
    _write_backup(tmp_path, 2, repo_bytes=16e9)
    c = health.check_backup_age(_ctx(tmp_path))
    assert c.status == "warn" and "20 GB" in c.reason
    (tmp_path / bk.LAST_FILE).write_text("{not json")
    assert health.check_backup_age(_ctx(tmp_path)).status == "warn"
    assert health._alerting("backup_age", "warn")          # a missed night reaches Telegram, not only at 72 h


def test_restore_drill_health(tmp_path):
    assert health.check_restore_drill(_ctx(tmp_path)).status == "warn"
    f = tmp_path / bk.DRILL_FILE
    f.write_text(json.dumps({"ts": (NOW - pd.Timedelta(days=2)).isoformat(), "ok": True, "files": 12}))
    assert health.check_restore_drill(_ctx(tmp_path)).status == "ok"
    f.write_text(json.dumps({"ts": (NOW - pd.Timedelta(days=9)).isoformat(), "ok": True, "files": 12}))
    assert health.check_restore_drill(_ctx(tmp_path)).status == "warn"
    f.write_text(json.dumps({"ts": NOW.isoformat(), "ok": False, "problems": ["state/core.db: integrity_check failed"]}))
    c = health.check_restore_drill(_ctx(tmp_path))
    assert c.status == "fail" and "integrity_check" in c.reason


def test_backup_prune_health(tmp_path):
    assert health.check_backup_prune(_ctx(tmp_path)).status == "ok"                  # no backup yet
    _write_backup(tmp_path, 1, first_ok_ts=(NOW - pd.Timedelta(days=10)).isoformat())
    c = health.check_backup_prune(_ctx(tmp_path))
    assert c.status == "ok" and "no retention run yet" in c.reason
    _write_backup(tmp_path, 1, first_ok_ts=(NOW - pd.Timedelta(days=46)).isoformat())
    c = health.check_backup_prune(_ctx(tmp_path))
    assert c.status == "warn" and "backup_retention.sh" in c.reason
    _write_backup(tmp_path, 1, last_prune_ts=(NOW - pd.Timedelta(days=44)).isoformat())
    assert health.check_backup_prune(_ctx(tmp_path)).status == "ok"
    _write_backup(tmp_path, 1, last_prune_ts=(NOW - pd.Timedelta(days=46)).isoformat())
    c = health.check_backup_prune(_ctx(tmp_path))
    assert c.status == "warn" and "> 45 d" in c.reason
    assert health._alerting("backup_prune", "warn")


def test_backup_checks_are_in_the_full_report(tmp_path):
    names = {c.name for c in health.run_checks(_ctx(tmp_path)).checks}
    assert {"backup_age", "backup_prune", "restore_drill"} <= names


# ------------------------------------------------------------------------------------------------ real restic
@pytest.mark.integration
@pytest.mark.skipif(shutil.which("restic") is None, reason="restic not installed")
def test_real_restic_round_trip_and_drill(brain, tmp_path):
    repo = tmp_path / "restic-repo"
    secrets = {"restic-repository": str(repo), "restic-password": "integration-pw",
               "restic-s3-access-key": "unused", "restic-s3-secret-key": "unused"}
    bk.init_repository(bk.Restic.from_secrets(secrets.get, host=HOST))
    bk.backup_job(brain, secrets.get, host=HOST)
    bk.drill_job(brain, secrets.get, host=HOST, check_subset="100%")
    assert _record(brain, bk.DRILL_FILE)["ok"] is True
    root = bk.restore(bk.Restic.from_secrets(secrets.get, host=HOST), tmp_path / "out")
    assert bk.verify_restore(root) == []
    assert _count(root / "state" / "core.db", "t") == 400

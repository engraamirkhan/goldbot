"""Encrypted, off-host backups of the brain's state with a restore drill (docs/proposals/2026-10-state-store.md, step 2
and section 7; decision record docs/decisions/0001-backup-scope-and-thresholds.md).

What a snapshot holds (built in a staging directory, then saved by restic):
* every SQLite file under the state directory, copied with the online backup API (`sqlite3.Connection.backup`, one
  step under one read transaction, so the copy is a consistent point in time while the services keep writing),
  switched to a single-file journal and checked with `PRAGMA integrity_check` before anything is uploaded;
* the rest of the state directory (JSON/JSONL state, approvals, reports), except secrets (`.secrets.json`),
  heartbeats, temp files and the live `*.db-wal`/`*.db-shm` files;
* the trial registry (when it lives outside the state directory) and the models directory (checksummed artefacts);
* the brain's own Parquet that cannot be downloaded again (every `source=` that is not a release source, e.g. the
  engine's ticks, fills, trades and decisions journal). Release-backed sources (Dukascopy, FRED) and derived tables
  (features, labels) are left out;
* `MANIFEST.json`: SHA-256 and size of every file, plus schema version and row counts of every database.

restic encrypts client-side and writes to Oracle Object Storage through its S3-compatible endpoint. Credentials come
from the keyring / secret store (`goldbot accounts set restic-repository|restic-password|restic-s3-access-key|
restic-s3-secret-key`) and reach restic only through its environment: never an argument, never a log line, and any
value that appears in restic's error output is masked before it is recorded. Never GitHub.

Each run records state/backup_last.json; the weekly drill restores the latest snapshot to a temporary directory,
re-checks every checksum, `integrity_check`, schema versions and row counts against the manifest, every model
artefact against models/registry.json, runs `restic check --read-data-subset`, and records
state/restore_drill_last.json. Health reads both (`backup_age`, `restore_drill`).

  python -m goldbot.ops.run backup [--init]           # one backup now (--init: create the repository once)
  python -m goldbot.ops.run restore --latest --to DIR # restore into an empty DIR (never over live state)
  python -m goldbot.ops.run restore-drill             # the weekly drill, now
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pydantic import Field

from goldbot.base import Record, UtcTimestamp, write_atomic

log = logging.getLogger("goldbot.backup")

# keyring / secret-store keys (values never leave the process except in restic's environment)
SECRET_KEYS: dict[str, str] = {
    "RESTIC_REPOSITORY": "restic-repository",       # s3:https://<namespace>.compat.objectstorage.<region>.oraclecloud.com/<bucket>/goldbot
    "RESTIC_PASSWORD": "restic-password",
    "AWS_ACCESS_KEY_ID": "restic-s3-access-key",    # OCI "Customer Secret Key" (access key)
    "AWS_SECRET_ACCESS_KEY": "restic-s3-secret-key",
}
LAST_FILE = "backup_last.json"
DRILL_FILE = "restore_drill_last.json"
MANIFEST = "MANIFEST.json"
TAG = "goldbot"
RELEASE_SOURCES = ("dukascopy", "fred")          # re-downloadable from releases data-v1 / macro-v1
DERIVED_TABLES = ("features", "labels")          # recomputed from bars
_STATE_EXCLUDE = (re.compile(r".*\.db(-wal|-shm|-journal)?$"), re.compile(r"^\.secrets\.json$"),
                  re.compile(r".*\.tmp$"), re.compile(r"^heartbeat_.*\.json$"))
_OCI_REGION = re.compile(r"compat\.objectstorage\.([a-z0-9-]+)\.oraclecloud\.com")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class BackupError(RuntimeError):
    pass


class ResticNotConfigured(BackupError):
    pass


# ------------------------------------------------------------------------------------------------ configuration
class Retention(Record):
    keep_daily: int = Field(14, ge=1)
    keep_weekly: int = Field(8, ge=0)
    keep_monthly: int = Field(12, ge=0)


class BackupPaths(Record):
    state_dir: Path
    models_dir: Path
    registry: Path                         # trial registry (JSONL)
    data_root: Path
    work_dir: Path                         # staging + drill scratch; on the same filesystem as data_root (hard links)
    exclude_sources: tuple[str, ...] = RELEASE_SOURCES
    exclude_tables: tuple[str, ...] = DERIVED_TABLES

    @classmethod
    def from_settings(cls, settings: Any, state_dir: str | Path = "state") -> "BackupPaths":
        b = settings.backup
        return cls(state_dir=Path(state_dir), models_dir=Path(settings.research.models_dir),
                   registry=Path(settings.research.registry), data_root=Path(settings.data_root),
                   work_dir=Path(b.work_dir).expanduser(), exclude_sources=tuple(b.data_exclude_sources),
                   exclude_tables=tuple(b.data_exclude_tables))


def retention_args(r: Retention, host: str) -> list[str]:
    """`restic forget` for this host's goldbot snapshots only, pruning unreferenced data in the same run."""
    return ["forget", "--host", host, "--tag", TAG, "--keep-daily", str(r.keep_daily), "--keep-weekly",
            str(r.keep_weekly), "--keep-monthly", str(r.keep_monthly), "--prune"]


# ------------------------------------------------------------------------------------------------ restic
class Restic:
    """restic as a subprocess. Secrets live only in `env`; `run` logs the subcommand, never the environment, and masks
    any secret value in the error text it raises."""

    def __init__(self, env: dict[str, str], *, host: str, binary: str = "restic", runner: Runner = subprocess.run,
                 timeout_s: float = 3600):
        self.env, self.host, self.binary, self.runner, self.timeout_s = env, host, binary, runner, timeout_s
        self._secrets = [v for k, v in env.items() if k in SECRET_KEYS and v]

    @classmethod
    def from_secrets(cls, get_secret: Callable[[str], str | None], *, host: str, runner: Runner = subprocess.run,
                     timeout_s: float = 3600, binary: str = "restic") -> "Restic":
        values = {env: get_secret(key) for env, key in SECRET_KEYS.items()}
        missing = [SECRET_KEYS[e] for e, v in values.items() if not v]
        if missing:
            raise ResticNotConfigured("restic not configured, missing in the secret store: " + ", ".join(missing)
                                      + " (goldbot accounts set <key>; RUNBOOK 'Backups')")
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", str(Path.home())), **{k: str(v) for k, v in values.items()}}
        m = _OCI_REGION.search(env["RESTIC_REPOSITORY"])
        if m:                                       # OCI's S3 endpoint needs its region in the request signature
            env["AWS_DEFAULT_REGION"] = m.group(1)
        return cls(env, host=host, runner=runner, timeout_s=timeout_s, binary=binary)

    def redact(self, text: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(s, "***")
        return text

    def run(self, args: list[str], *, timeout_s: float | None = None) -> str:
        log.info("restic %s", args[0])               # the subcommand only: arguments carry paths, never secrets
        try:
            p = self.runner([self.binary, *args], env=self.env, capture_output=True, text=True,
                            timeout=timeout_s or self.timeout_s, check=False)
        except FileNotFoundError as exc:
            raise BackupError(f"{self.binary} is not installed (brain_bootstrap.sh installs it with apt)") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackupError(f"restic {args[0]} timed out after {exc.timeout:.0f} s") from exc
        if p.returncode != 0:
            err = self.redact((p.stderr or p.stdout or "").strip())[-600:]
            raise BackupError(f"restic {args[0]} exited {p.returncode}: {err}")
        return p.stdout or ""


# ------------------------------------------------------------------------------------------------ snapshot set
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                       "ORDER BY name")]


def db_facts(path: Path) -> dict[str, Any]:
    """integrity_check, user_version and row counts of a (copied or restored) database file, opened read-only."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = "; ".join(str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall())
        rows = {t: int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in _tables(conn)}
        return {"integrity": integrity, "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
                "rows": rows}
    finally:
        conn.close()


def snapshot_sqlite(src: Path, dst: Path, *, busy_timeout_s: float = 30.0) -> dict[str, Any]:
    """A consistent copy of a live WAL database: the online backup API in one step (one read transaction, so writers
    keep committing to the WAL and the copy is the state at one instant), then a single-file journal mode on the copy.
    Raises BackupError when the copy fails integrity_check."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=busy_timeout_s)
        target = sqlite3.connect(dst)
        try:
            source.backup(target)                   # pages=-1: everything in one step
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
            source.close()
        facts = db_facts(dst)
    except sqlite3.Error as exc:
        raise BackupError(f"{src.name}: online backup failed: {type(exc).__name__}: {exc}") from exc
    if facts["integrity"] != "ok":
        raise BackupError(f"{src.name}: integrity_check failed on the copy: {facts['integrity'][:300]}")
    return facts


def _parquet_complete(path: Path) -> bool:
    """A Parquet file starts and ends with b"PAR1"; a part still being written does not end with it yet."""
    try:
        size = path.stat().st_size
        if size < 12:
            return False
        with path.open("rb") as fh:
            head = fh.read(4)
            fh.seek(-4, os.SEEK_END)
            return head == b"PAR1" and fh.read(4) == b"PAR1"
    except OSError:
        return False


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hard link when possible (no extra disk; writers replace files rather than editing them in place), else copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _excluded_state(rel: Path) -> bool:
    return any(p.match(rel.name) for p in _STATE_EXCLUDE)


def build_snapshot(paths: BackupPaths, dest: Path) -> dict[str, Any]:
    """Stage the snapshot set in `dest` (empty) and write MANIFEST.json; returns the manifest."""
    dest.mkdir(parents=True, exist_ok=True)
    state, work = paths.state_dir.resolve(), paths.work_dir.resolve()
    dbs: dict[str, Any] = {}
    skipped: list[str] = []
    for db in sorted(state.rglob("*.db")):
        if work in db.resolve().parents or not db.is_file():
            continue
        rel = Path("state") / db.relative_to(state)
        dbs[rel.as_posix()] = snapshot_sqlite(db, dest / rel)
    for f in sorted(state.rglob("*")):
        rel_state = f.relative_to(state)
        if not f.is_file() or f.is_symlink() or work in f.resolve().parents or _excluded_state(rel_state):
            continue
        out = dest / "state" / rel_state
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)                        # small files written atomically by their owners: a whole copy
    reg = paths.registry
    if reg.is_file() and state not in reg.resolve().parents:
        (dest / "registry").mkdir(parents=True, exist_ok=True)
        shutil.copy2(reg, dest / "registry" / reg.name)
    if paths.models_dir.is_dir():
        for f in sorted(paths.models_dir.rglob("*")):
            if f.is_file() and not f.is_symlink() and not f.name.endswith(".tmp"):
                out = dest / "models" / f.relative_to(paths.models_dir)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
    if paths.data_root.is_dir():
        for sdir in sorted(paths.data_root.glob("*/source=*")):
            table, source = sdir.parent.name, sdir.name.split("=", 1)[1]
            if table in paths.exclude_tables or source in paths.exclude_sources:
                continue
            for f in sorted(sdir.rglob("*.parquet")):
                rel = Path("data") / f.relative_to(paths.data_root)
                if not _parquet_complete(f):
                    skipped.append(rel.as_posix())   # a part being written right now: tomorrow's snapshot has it
                    continue
                _link_or_copy(f, dest / rel)
    files = {f.relative_to(dest).as_posix(): {"sha256": sha256_file(f), "bytes": f.stat().st_size}
             for f in sorted(dest.rglob("*")) if f.is_file()}
    manifest = {"created_utc": pd.Timestamp.now("UTC").isoformat(), "files": files, "dbs": dbs,
                "skipped_incomplete": skipped, "model_problems": verify_models(dest / "models")}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


# ------------------------------------------------------------------------------------------------ verification
def verify_models(models_dir: Path) -> list[str]:
    """Every artefact in models/registry.json exists and matches its recorded SHA-256 (ModelRegistry.load's check)."""
    reg = models_dir / "registry.json"
    if not reg.is_file():
        return []
    try:
        entries = json.loads(reg.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [f"models/registry.json unreadable: {exc}"]
    out = []
    for e in entries:
        f = models_dir / str(e.get("artefact", ""))
        if not f.is_file():
            out.append(f"model {e.get('version')}: artefact {e.get('artefact')} missing")
        elif sha256_file(f) != e.get("sha256"):
            out.append(f"model {e.get('version')}: artefact {e.get('artefact')} checksum mismatch")
    return out


def verify_restore(root: Path) -> list[str]:
    """Problems found in a restored snapshot (empty: verified). Checks the manifest's checksums, every database's
    integrity_check, schema version and row counts, and every model artefact against the registry."""
    mf = root / MANIFEST
    if not mf.is_file():
        return [f"{MANIFEST} missing: not a goldbot snapshot"]
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [f"{MANIFEST} unreadable: {exc}"]
    problems: list[str] = []
    for rel, meta in manifest.get("files", {}).items():
        f = root / rel
        if not f.is_file():
            problems.append(f"{rel}: missing")
        elif sha256_file(f) != meta.get("sha256"):
            problems.append(f"{rel}: checksum mismatch")
    for rel, want in manifest.get("dbs", {}).items():
        f = root / rel
        if not f.is_file():
            continue                                # reported above
        try:
            got = db_facts(f)
        except sqlite3.DatabaseError as exc:
            problems.append(f"{rel}: integrity_check failed: {exc}")
            continue
        if got["integrity"] != "ok":
            problems.append(f"{rel}: integrity_check failed: {got['integrity'][:200]}")
        if got["user_version"] != want.get("user_version"):
            problems.append(f"{rel}: user_version {got['user_version']} != {want.get('user_version')}")
        if got["rows"] != want.get("rows"):
            problems.append(f"{rel}: row counts differ from the manifest")
    problems += verify_models(root / "models")
    return problems


# ------------------------------------------------------------------------------------------------ records
class BackupRecord(Record):
    ts: UtcTimestamp                       # this attempt
    ok: bool
    last_ok_ts: UtcTimestamp | None = None
    snapshot_id: str | None = None
    files: int = 0
    bytes: int = 0
    dbs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    skipped_incomplete: list[str] = Field(default_factory=list)
    model_problems: list[str] = Field(default_factory=list)
    repo_bytes: int | None = None
    retention: Retention | None = None
    duration_s: float = 0.0
    error: str | None = None


class DrillRecord(Record):
    ts: UtcTimestamp
    ok: bool
    last_ok_ts: UtcTimestamp | None = None
    snapshot: str | None = None
    files: int = 0
    problems: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None


def _previous_ok(path: Path) -> pd.Timestamp | None:
    try:
        v = json.loads(path.read_text(encoding="utf-8")).get("last_ok_ts")
        return pd.Timestamp(v) if v else None
    except (ValueError, OSError, AttributeError):
        return None


def _record(path: Path, rec: Record) -> None:
    write_atomic(path, rec.model_dump_json(indent=1))


def _empty_dir(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)


# ------------------------------------------------------------------------------------------------ backup
def init_repository(restic: Restic) -> str:
    """Create the encrypted repository once (`restic init`); refuses (restic error) when one already exists."""
    return restic.run(["init"])


def run_backup(paths: BackupPaths, restic: Restic, retention: Retention) -> BackupRecord:
    """Stage, upload, apply retention, measure. Raises BackupError; `backup_job` records the outcome."""
    t0 = time.monotonic()
    stage = paths.work_dir / "snapshot"             # a fixed path, so restic finds its parent snapshot
    _empty_dir(stage)
    try:
        manifest = build_snapshot(paths, stage)
        restic.run(["unlock"])                      # stale locks only (a crashed earlier run); live locks stay
        out = restic.run(["backup", "--json", "--host", restic.host, "--tag", TAG, str(stage.resolve())])
        snap = None
        for line in out.splitlines()[::-1]:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("message_type") == "summary":
                snap = msg.get("snapshot_id")
                break
        restic.run(retention_args(retention, restic.host))
        repo_bytes = None
        try:
            stats = json.loads(restic.run(["stats", "--json", "--mode", "raw-data"]) or "{}")
            repo_bytes = int(stats["total_size"]) if "total_size" in stats else None
        except (BackupError, ValueError, TypeError):
            log.warning("restic stats failed: repository size unknown")   # informational only
        files = manifest["files"]
        return BackupRecord(ts=pd.Timestamp.now("UTC"), ok=True, snapshot_id=snap, files=len(files),
                            bytes=sum(int(m["bytes"]) for m in files.values()), dbs=manifest["dbs"],
                            skipped_incomplete=manifest["skipped_incomplete"],
                            model_problems=manifest["model_problems"], repo_bytes=repo_bytes, retention=retention,
                            duration_s=round(time.monotonic() - t0, 1))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def backup_job(paths: BackupPaths, get_secret: Callable[[str], str | None], *, host: str, retention: Retention,
               runner: Runner = subprocess.run, timeout_s: float = 3600) -> dict[str, Any]:
    """The scheduler job: one backup, recorded in state/backup_last.json whatever happens; raises on failure so the
    scheduler records it too (health: backup_age, scheduler job check)."""
    path = paths.state_dir / LAST_FILE
    prev_ok = _previous_ok(path)
    t0 = time.monotonic()
    restic: Restic | None = None
    try:
        restic = Restic.from_secrets(get_secret, host=host, runner=runner, timeout_s=timeout_s)
        rec = run_backup(paths, restic, retention)
        rec.last_ok_ts = rec.ts
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        rec = BackupRecord(ts=pd.Timestamp.now("UTC"), ok=False, last_ok_ts=prev_ok, retention=retention,
                           duration_s=round(time.monotonic() - t0, 1),
                           error=(restic.redact(msg) if restic else msg)[:800])
        _record(path, rec)
        log.error("backup failed: %s", rec.error)
        raise BackupError(rec.error) from None
    _record(path, rec)
    log.info("backup ok: snapshot %s, %d files, %d bytes", rec.snapshot_id, rec.files, rec.bytes)
    return {"snapshot": rec.snapshot_id, "files": rec.files, "bytes": rec.bytes, "repo_bytes": rec.repo_bytes,
            "skipped_incomplete": len(rec.skipped_incomplete), "model_problems": rec.model_problems}


# ------------------------------------------------------------------------------------------------ restore
def restore(restic: Restic, target: Path, snapshot: str = "latest") -> Path:
    """Restore a snapshot into `target`, which must be empty or absent: a restore never writes over live state (the
    owner stops the services and moves the files into place, RUNBOOK 'Backups'). Returns `target`, holding
    MANIFEST.json, state/, models/ and data/."""
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise BackupError(f"{target} is not empty: restore only into an empty directory")
    scratch = target / ".restic-restore"
    scratch.mkdir(parents=True)
    try:
        args = ["restore", snapshot, "--target", str(scratch)]
        if snapshot == "latest":
            args[2:2] = ["--host", restic.host, "--tag", TAG]
        restic.run(args)
        # restic recreates the staging directory's absolute path under the target; the manifest marks the root
        found = sorted(scratch.rglob(MANIFEST), key=lambda p: len(p.parts))
        if not found:
            raise BackupError(f"restored snapshot has no {MANIFEST}: not a goldbot snapshot")
        for child in found[0].parent.iterdir():
            shutil.move(str(child), str(target / child.name))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target


def restore_drill(paths: BackupPaths, restic: Restic, *, check_subset: str = "5%") -> DrillRecord:
    t0 = time.monotonic()
    paths.work_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="drill-", dir=paths.work_dir))
    try:
        root = restore(restic, tmp / "restored")
        problems = verify_restore(root)
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8")) if (root / MANIFEST).is_file() else {}
        restic.run(["check", f"--read-data-subset={check_subset}"])
        return DrillRecord(ts=pd.Timestamp.now("UTC"), ok=not problems, snapshot=manifest.get("created_utc"),
                           files=len(manifest.get("files", {})), problems=problems[:50],
                           duration_s=round(time.monotonic() - t0, 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def drill_job(paths: BackupPaths, get_secret: Callable[[str], str | None], *, host: str, check_subset: str = "5%",
              runner: Runner = subprocess.run, timeout_s: float = 3600) -> dict[str, Any]:
    """The weekly scheduler job: restore the latest snapshot to a temp dir and verify it; recorded in
    state/restore_drill_last.json; raises when the drill fails (health: restore_drill)."""
    path = paths.state_dir / DRILL_FILE
    prev_ok = _previous_ok(path)
    t0 = time.monotonic()
    restic: Restic | None = None
    try:
        restic = Restic.from_secrets(get_secret, host=host, runner=runner, timeout_s=timeout_s)
        rec = restore_drill(paths, restic, check_subset=check_subset)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        rec = DrillRecord(ts=pd.Timestamp.now("UTC"), ok=False, duration_s=round(time.monotonic() - t0, 1),
                          error=(restic.redact(msg) if restic else msg)[:800])
    rec.last_ok_ts = rec.ts if rec.ok else prev_ok
    _record(path, rec)
    if not rec.ok:
        why = rec.error or "; ".join(rec.problems)[:800]
        log.error("restore drill failed: %s", why)
        raise BackupError(f"restore drill failed: {why}")
    log.info("restore drill ok: %d files verified", rec.files)
    return {"files": rec.files, "snapshot": rec.snapshot}


# ------------------------------------------------------------------------------------------------ CLI
def main(argv: list[str], *, get_secret: Callable[[str], str | None] | None = None, settings: Any = None,
         runner: Runner = subprocess.run, state_dir: str | Path = "state") -> int:
    """`run.py backup [--init]`, `run.py restore --latest|--snapshot ID --to DIR`, `run.py restore-drill`."""
    import argparse

    from goldbot.config import load_settings
    if get_secret is None:
        from goldbot.ops.accounts import get_secret as _gs
        get_secret = _gs
    settings = settings or load_settings()
    b = settings.backup
    paths = BackupPaths.from_settings(settings, state_dir)
    retention = Retention(keep_daily=b.keep_daily, keep_weekly=b.keep_weekly, keep_monthly=b.keep_monthly)
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "backup":
            if rest[:1] == ["--init"]:
                init_repository(Restic.from_secrets(get_secret, host=b.host, runner=runner, timeout_s=b.timeout_s))
                print("restic repository created (keep the restic password in your password manager)")
                return 0
            print(json.dumps(backup_job(paths, get_secret, host=b.host, retention=retention, runner=runner,
                                        timeout_s=b.timeout_s), indent=1))
            return 0
        if cmd == "restore":
            ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run restore")
            g = ap.add_mutually_exclusive_group(required=True)
            g.add_argument("--latest", action="store_true")
            g.add_argument("--snapshot")
            ap.add_argument("--to", required=True, help="an empty directory")
            a = ap.parse_args(rest)
            restic = Restic.from_secrets(get_secret, host=b.host, runner=runner, timeout_s=b.timeout_s)
            root = restore(restic, Path(a.to), "latest" if a.latest else a.snapshot)
            problems = verify_restore(root)
            for p in problems:
                print(f"PROBLEM {p}")
            print(f"restored to {root}: {'verified' if not problems else f'{len(problems)} problem(s)'}")
            return 1 if problems else 0
        if cmd == "restore-drill":
            print(json.dumps(drill_job(paths, get_secret, host=b.host, check_subset=b.check_subset, runner=runner,
                                       timeout_s=b.timeout_s), indent=1))
            return 0
    except BackupError as exc:
        print(f"error: {exc}")
        return 1
    print(__doc__)
    return 1


__all__ = ["BackupError", "ResticNotConfigured", "Retention", "BackupPaths", "Restic", "retention_args",
           "snapshot_sqlite", "build_snapshot", "verify_restore", "verify_models", "run_backup", "backup_job",
           "restore", "restore_drill", "drill_job", "BackupRecord", "DrillRecord", "main"]

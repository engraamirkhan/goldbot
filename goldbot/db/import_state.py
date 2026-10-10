"""Import the JSON state files into core.db / aux.db (docs/proposals/2026-10-state-store.md, section 6).

"`import-state [--dry-run]` validates every file with its existing pydantic model, upserts by natural key
(idempotent), records file SHA-256s in `imports`, leaves the files in place, and stops loudly on an unreadable
orders or risk file."

Each step of the migration plan registers its importers in `importers()`. Every import runs in one write transaction
on the target file together with its `imports` row, so a crash leaves either nothing or the whole file imported.
A file already imported with the same SHA-256 is skipped; an append-only source (`once=True`, e.g. audit.jsonl)
is imported at most once, so a re-run never duplicates its lines.

    python -m goldbot.db import-state --state-dir state [--dry-run]
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

from goldbot.base import FrozenRecord
from goldbot.db.connection import Database, DbKind, now_ns

Loader = Callable[[Path, sqlite3.Connection], int]     # validate + upsert inside the caller's transaction; row count


class ImportSpec(FrozenRecord):
    source: str            # file name under the state dir
    kind: DbKind
    loader: Loader
    once: bool = False     # append-only source: never import a second time, even if the file changed


class ImportResult(FrozenRecord):
    source: str
    kind: DbKind
    status: str            # imported | skipped | missing | dry_run
    rows: int = 0
    sha256: str = ""


class _DryRun(Exception):
    def __init__(self, rows: int):
        self.rows = rows


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_file(db: Database, spec: ImportSpec, path: Path, *, dry_run: bool = False) -> ImportResult:
    if not path.exists():
        return ImportResult(source=spec.source, kind=spec.kind, status="missing")
    sha = file_sha256(path)
    try:
        with db.write() as conn:
            prev = conn.execute("SELECT sha256 FROM imports WHERE source = ?", (spec.source,)).fetchone()
            if prev is not None and (spec.once or prev[0] == sha):
                return ImportResult(source=spec.source, kind=spec.kind, status="skipped", sha256=sha)
            rows = spec.loader(path, conn)
            if dry_run:
                raise _DryRun(rows)                      # rolls the transaction back
            conn.execute("INSERT INTO imports (source, sha256, rows, imported_ns) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(source) DO UPDATE SET sha256=excluded.sha256, rows=excluded.rows, "
                         "imported_ns=excluded.imported_ns", (spec.source, sha, rows, now_ns()))
    except _DryRun as d:
        return ImportResult(source=spec.source, kind=spec.kind, status="dry_run", rows=d.rows, sha256=sha)
    return ImportResult(source=spec.source, kind=spec.kind, status="imported", rows=rows, sha256=sha)


def importers() -> list[ImportSpec]:
    """Every registered importer, in dependency order. Imported lazily: goldbot.db never imports its callers."""
    from goldbot.api.auth import import_audit_jsonl, import_users_json
    return [ImportSpec(source="users.json", kind="aux", loader=import_users_json),
            ImportSpec(source="audit.jsonl", kind="aux", loader=import_audit_jsonl, once=True)]


def import_state(state_dir: str | Path, *, dry_run: bool = False) -> list[ImportResult]:
    """Import every registered file found under `state_dir`. Files are left in place as a backup."""
    d = Path(state_dir)
    dbs: dict[DbKind, Database] = {}
    out = []
    for spec in importers():
        if spec.kind not in dbs:
            dbs[spec.kind] = Database.open(d, spec.kind)
        out.append(import_file(dbs[spec.kind], spec, d / spec.source, dry_run=dry_run))
    return out


__all__ = ["ImportSpec", "ImportResult", "import_file", "importers", "import_state", "file_sha256"]

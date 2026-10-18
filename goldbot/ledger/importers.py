"""Backfill importers for the program ledger: history that exists as files becomes ledger rows.

Design rules:
* "A row is never updated or deleted": every importer is idempotent. Keys are derived from the content (the line's
  sha256, the file's name, the quote's hash), so a second run adds nothing and a changed file becomes a new version.
* "Secrets and account identifiers never enter the ledger": every row goes through Ledger.add, which scans it and
  fails closed. A refused row is counted and skipped; the rest still import. Nothing is read from the keyring.
* Owner notes are PRIVATE (owner_notes is private by constraint) and never exported.
* Artifacts index path, sha256 and size; content over 256 KB is hashed but not stored (models.MAX_BLOB_BYTES).
Each importer returns an `ImportResult` (added, unchanged, refused)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from goldbot.ledger.models import NewEntry
from goldbot.ledger.store import Ledger, LedgerRefused

MAX_ARTIFACT_READ = 8 * 1024 * 1024          # a bigger file is skipped unread
QUOTE = re.compile(r"[\"“]([^\"”\n]{25,400})[\"”]")
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
ADR_NAME = re.compile(r"^(\d{4})-.+\.md$")


@dataclass
class ImportResult:
    added: int = 0
    unchanged: int = 0
    refused: int = 0

    def __iadd__(self, other: ImportResult) -> ImportResult:
        self.added += other.added
        self.unchanged += other.unchanged
        self.refused += other.refused
        return self

    def __str__(self) -> str:
        return f"added {self.added}, unchanged {self.unchanged}, refused {self.refused}"


def _put(ledger: Ledger, res: ImportResult, entry: NewEntry) -> None:
    try:
        top = ledger._rows(f"SELECT COALESCE(MAX(id), 0) AS m FROM {entry.table}")[0]["m"]     # noqa: SLF001
        rid = ledger.add(entry)
    except LedgerRefused:
        res.refused += 1
        return
    if rid > top:
        res.added += 1
    else:
        res.unchanged += 1


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_registry(ledger: Ledger, path: Path) -> ImportResult:
    """research registry (release research-v1 registry.jsonl): a `verdict` row -> research_verdicts, every other
    row -> research_runs. Keyed by the line's sha256, so re-importing the same file adds nothing."""
    res = ImportResult()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            res.refused += 1
            continue
        if not isinstance(row, dict):
            res.refused += 1
            continue
        run_id = f"{row.get('agent_id', '?')}:{row.get('config_hash', '?')}:{row.get('ts', '?')}"
        table: Literal["research_verdicts", "research_runs"] = (
            "research_verdicts" if row.get("status") == "verdict" else "research_runs")
        _put(ledger, res, NewEntry(table=table, key=f"registry:{_sha(line.encode())}", body=line,
                                   source="registry.jsonl@research-v1", fields={"run_id": run_id}))
    return res


def import_adrs(ledger: Ledger, directory: Path) -> ImportResult:
    """docs/decisions/NNNN-*.md -> decisions, kind adr (the title is the first heading)."""
    res = ImportResult()
    for p in sorted(directory.glob("*.md")):
        m = ADR_NAME.match(p.name)
        if not m:
            continue
        text = p.read_text(encoding="utf-8")
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), p.stem)
        d = DATE.search(text)
        _put(ledger, res, NewEntry(table="decisions", key=f"adr:{m.group(1)}", body=text, source=f"docs/decisions/{p.name}",
                                   fields={"kind": "adr", "ref": m.group(1), "title": title, "verbatim_quote": "",
                                           "dated": d.group(1) if d else ""}))
    return res


def import_directives(ledger: Ledger, memory_dir: Path) -> ImportResult:
    """Quoted owner directives in the Claude memory notes -> decisions, kind owner_directive: the verbatim quote and
    the date of its note (a date in the file, else the file's modification day)."""
    res = ImportResult()
    for p in sorted(memory_dir.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        text = p.read_text(encoding="utf-8")
        d = DATE.search(text)
        dated = d.group(1) if d else datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        for q in dict.fromkeys(QUOTE.findall(text)):
            qh = _sha(q.encode())[:12]
            _put(ledger, res, NewEntry(table="decisions", key=f"directive:{p.stem}:{qh}", body=q, source=f"memory/{p.name}",
                                       fields={"kind": "owner_directive", "ref": p.stem, "title": p.stem,
                                               "verbatim_quote": q, "dated": dated}))
    return res


def import_owner_notes(ledger: Ledger, directory: Path) -> ImportResult:
    """~/tmp/owner-notes/*.md -> owner_notes (PRIVATE; never exported)."""
    res = ImportResult()
    for p in sorted(directory.glob("*.md")):
        _put(ledger, res, NewEntry(table="owner_notes", key=f"note:{p.stem}", body=p.read_text(encoding="utf-8"),
                                   source=f"owner-notes/{p.name}", privacy="private", fields={}))
    return res


def import_resume(ledger: Ledger, path: Path) -> ImportResult:
    """state/RESUME.md -> resume_snapshots (a changed file is the next version of the same key)."""
    res = ImportResult()
    if path.exists():
        _put(ledger, res, NewEntry(table="resume_snapshots", key="resume", body=path.read_text(encoding="utf-8"),
                                   source="state/RESUME.md", fields={}))
    return res


def import_artifacts(ledger: Ledger, *directories: Path) -> ImportResult:
    """Files under the directories -> artifacts: path, sha256, size; content only up to 256 KB. A file over 8 MB is
    skipped unread."""
    res = ImportResult()
    for root in directories:
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            if p.stat().st_size > MAX_ARTIFACT_READ:
                res.refused += 1
                continue
            data = p.read_bytes()
            rel = f"{root.name}/{p.relative_to(root)}"
            _put(ledger, res, NewEntry(table="artifacts", key=f"artifact:{rel}", body=f"{rel} {len(data)} bytes",
                                       source=f"artifact-index:{root.name}", fields={"path": rel}, blob=data))
    return res

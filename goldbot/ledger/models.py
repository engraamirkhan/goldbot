"""Records of the program ledger (state/program.db, schema in goldbot/db/migrations.py PROGRAM).

Every table shares the common columns (id, key, version, body, sha256, created_ns, source, supersedes_id,
superseded_by_id, privacy); `fields` carries the table's own columns by name. `owner_notes` is private by constraint,
the other tables are public unless the caller says `private`."""
from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from goldbot.base import Record

Table = Literal["knowledge_entries", "owner_notes", "decisions", "research_runs", "research_verdicts", "work_log",
                "resume_snapshots", "artifacts"]
Privacy = Literal["public", "private"]

# the caller-supplied own columns of each table (artifacts' size and blob are derived from `blob`)
TABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "knowledge_entries": ("topic", "evidence_grade"),
    "owner_notes": (),
    "decisions": ("kind", "ref", "title", "verbatim_quote", "dated"),
    "research_runs": ("run_id",),
    "research_verdicts": ("run_id",),
    "work_log": ("lane", "branch", "commit_sha", "hand_back", "deploy"),
    "resume_snapshots": (),
    "artifacts": ("path",),
}
MAX_BLOB_BYTES = 256 * 1024
COMMON = ("id", "key", "version", "body", "sha256", "created_ns", "source", "supersedes_id", "superseded_by_id",
          "privacy")


class NewEntry(Record):
    """One row to append. A key that already exists gets the next version and supersedes the previous latest."""

    table: Table
    key: str
    body: str
    source: str
    privacy: Privacy | None = None            # None: private for owner_notes, public for every other table
    fields: dict[str, str] = {}
    blob: bytes | None = None                 # artifacts only; stored when at most MAX_BLOB_BYTES

    @model_validator(mode="after")
    def _shape(self) -> NewEntry:
        want = set(TABLE_FIELDS[self.table])
        if set(self.fields) != want:
            raise ValueError(f"{self.table} takes exactly the fields {sorted(want)}, got {sorted(self.fields)}")
        if not self.key.strip() or not self.source.strip():
            raise ValueError("key and source are required")
        if self.blob is not None and self.table != "artifacts":
            raise ValueError("only artifacts carry a blob")
        if self.table == "artifacts" and self.blob is None:
            raise ValueError("artifacts need the content (blob) to record its sha256 and size")
        return self

    @property
    def resolved_privacy(self) -> Privacy:
        return self.privacy or ("private" if self.table == "owner_notes" else "public")

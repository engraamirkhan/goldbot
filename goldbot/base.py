"""Shared pydantic bases for goldbot's data types.

Every record that crosses a module boundary (orders, risk inputs, proposals, settings, agent identity) is a
pydantic model so values are validated on construction and assignment, and serialise with model_dump().
`arbitrary_types_allowed` lets records carry pandas/numpy values (timestamps, frames, index arrays) checked
by isinstance; `extra="forbid"` turns a misspelt field into an error instead of a silent default.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer


class Record(BaseModel):
    """Mutable record, validated on construction and on attribute assignment."""

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, extra="forbid")


class FrozenRecord(BaseModel):
    """Immutable record (hashable when its fields are); change it with model_copy(update=...)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")


def _to_utc_timestamp(v: Any) -> pd.Timestamp:
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# A UTC pandas Timestamp that round-trips through JSON (ISO 8601). Use it for every timestamp field that is
# persisted: a bare pd.Timestamp field only accepts Timestamp instances, so reading a saved file back would fail.
UtcTimestamp = Annotated[pd.Timestamp, BeforeValidator(_to_utc_timestamp),
                         PlainSerializer(lambda t: t.isoformat(), return_type=str, when_used="json")]


def _fsync_dir(d: Path) -> None:
    """Make a rename in `d` durable (POSIX); Windows has no directory fsync and NTFS journals renames."""
    if os.name == "nt":
        return
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


fsync_dir = _fsync_dir      # public name for appenders (gates_phase.append_closed_trade) that create a file


def write_atomic(path: Path, text: str, *, durable: bool = True) -> None:
    """Replace `path` with `text` so a reader never sees a half-written file. durable: the data and the rename reach
    the disk before returning (fsync file and directory), so a power cut leaves the old or the new content, never an
    empty file. Use it for state a restart depends on (orders, risk, approvals, halts); heartbeats rewritten every few
    seconds may pass durable=False."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")   # unique, O_EXCL
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        os.replace(tmp, path)
        if durable:
            _fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def create_exclusive(path: Path, text: str) -> bool:
    """Create `path` with `text` only if it does not exist yet, durably and all at once: the content is written and
    fsynced under a temporary name, then hard-linked into place (fails if `path` exists). A crash can no longer leave
    an empty file that blocks the decision forever. False if the file already existed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")   # unique, O_EXCL
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        _fsync_dir(path.parent)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def write_private(path: Path, text: str) -> None:
    """Write a file only the owner can read (secrets, users), atomically: the temp file is created 0600, so the
    content is never readable by others even for a moment or when chmod is unsupported, and a crash mid-write
    leaves the previous file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

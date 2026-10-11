"""Shared pydantic bases for goldbot's data types.

Every record that crosses a module boundary (orders, risk inputs, proposals, settings, agent identity) is a
pydantic model so values are validated on construction and assignment, and serialise with model_dump().
`arbitrary_types_allowed` lets records carry pandas/numpy values (timestamps, frames, index arrays) checked
by isinstance; `extra="forbid"` turns a misspelt field into an error instead of a silent default.

Persisted state is read tolerantly, built strictly (docs/decisions/0010-tolerant-persisted-reads.md): code builds
records with extra="forbid", but a file or row written by a newer release is read with `load_record` /
`load_record_json`, which drop fields this release does not know (logged once, counted in `schema_skew()`). Only
unknown keys are tolerated; a wrong type, a missing required field or bad JSON still fails. Credentials, the owner's
halt (Control), Settings and bridge order requests stay strict (fail closed).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, TypeVar

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


M = TypeVar("M", bound=BaseModel)
SkewKey = tuple[str, str, tuple[str, ...]]          # (source, model, unknown field paths)

_log = logging.getLogger(__name__)
_skew_lock = threading.Lock()
_skew: Counter[SkewKey] = Counter()
_refused: set[SkewKey] = set()                     # keys a strict reader refused (an owner's click dropped)
_last_refused: pd.Timestamp | None = None          # wall clock of the latest refusal (health bounds its FAIL on it)

SKEW_SUMMARY_MAX = 20       # entries published in engine_<account>.json (refused first); the rest only counted
SKEW_FIELDS_MAX = 10        # field paths per published entry


def _unknown_fields(value: Any, path: str = "") -> list[str]:
    """Dotted paths of the fields a model validated with extra="allow" kept as extras, at every nesting level
    (`[]` marks a list/tuple/set item, `{}` a mapping value), so the warning names the field, not the file."""
    out: list[str] = []
    if isinstance(value, BaseModel):
        out += [f"{path}{k}" for k in sorted(value.model_extra or {})]
        for name in type(value).model_fields:
            out += _unknown_fields(getattr(value, name), f"{path}{name}.")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            out += _unknown_fields(item, f"{path}[].")
    elif isinstance(value, Mapping):
        for item in value.values():
            out += _unknown_fields(item, f"{path}{{}}.")
    return sorted(set(out))


def note_schema_skew(source: str, model: str, fields: Sequence[str], *, refused: bool = False) -> None:
    """Record that `source` holds fields of `model` this release does not know: counted in `schema_skew()`, logged
    once per (source, model, fields). refused: a strict reader (ADR 0010 allowlist) refused the record instead of
    ignoring the fields. Also for readers outside load_record (a file's top-level tables, a strict refusal)."""
    key: SkewKey = (source, model, tuple(fields))
    with _skew_lock:
        first = key not in _skew
        _skew[key] += 1
        if refused:
            global _last_refused
            _refused.add(key)
            _last_refused = pd.Timestamp.now(tz="UTC")
    if first:
        what = "refused by a strict reader" if refused else "ignored"
        _log.warning("%s: %s written by a newer release has fields this release does not know (%s): %s",
                     source, model, what, ", ".join(fields))


def unknown_fields_json(cls: type[BaseModel], text: str | bytes) -> list[str]:
    """The fields of `text` that `cls` does not know, at every nesting level; [] when there are none or when `text`
    is invalid for another reason (bad JSON, a wrong type, a missing field). For strict readers that refuse a record
    and must say whether a newer release is the cause."""
    try:
        return _unknown_fields(cls.model_validate_json(text, extra="allow"))
    except ValueError:
        return []


def schema_skew() -> dict[SkewKey, int]:
    """How often each (source, model, unknown fields) was read since the process started: a non-empty result means
    state written by a newer release is being read by this one (expected only after a rollback)."""
    with _skew_lock:
        return dict(_skew)


class SkewEntry(FrozenRecord):
    source: str
    model: str
    fields: list[str]                # at most SKEW_FIELDS_MAX
    count: int                       # reads since the process started
    refused: bool                    # a strict reader refused the record (ADR 0010 allowlist) instead of ignoring fields


class SkewSummary(FrozenRecord):
    """The bounded `schema_skew` block of engine_<account>.json, read by health (`check_schema_skew`)."""
    count: int                       # distinct (source, model, fields)
    reads: int                       # reads of all of them
    refused: bool                    # any entry refused: an owner's click was dropped
    entries: list[SkewEntry]         # refused first, then by source; at most SKEW_SUMMARY_MAX
    truncated: int                   # entries left out of `entries`
    # wall clock of the latest refusal: health FAILs for SKEW_REFUSED_FAIL_S after it, then WARNs. None with refused
    # set (a block from before this field) reads as a fresh refusal (FAIL)
    last_refused_utc: UtcTimestamp | None = None


def skew_summary(counts: Mapping[SkewKey, int], refused: set[SkewKey], last_refused: pd.Timestamp | None = None,
                 limit: int = SKEW_SUMMARY_MAX) -> SkewSummary:
    """`counts` as a SkewSummary of at most `limit` entries, refused ones first so a dropped click is never the entry
    the bound leaves out."""
    keys = sorted(counts, key=lambda k: (k not in refused, k))
    entries = [SkewEntry(source=k[0], model=k[1], fields=list(k[2][:SKEW_FIELDS_MAX]), count=counts[k],
                         refused=k in refused) for k in keys[:limit]]
    return SkewSummary(count=len(keys), reads=sum(counts.values()), refused=any(k in refused for k in keys),
                       entries=entries, truncated=max(0, len(keys) - limit), last_refused_utc=last_refused)


def schema_skew_summary() -> SkewSummary:
    """`schema_skew()` for the engine state file, so skew reaches the owner through health, not only the log."""
    with _skew_lock:
        counts, refused, last = dict(_skew), set(_refused), _last_refused
    return skew_summary(counts, refused, last)


def load_record(cls: type[M], data: Any, *, source: str) -> M:
    """Validate persisted `data` (a dict from JSON) as `cls`, tolerating fields a newer release added.

    ADR 0010: "persisted state is read tolerantly, built strictly". Unknown keys at any nesting level are dropped,
    logged once per (source, model, fields) and counted in `schema_skew()`; everything else is validated exactly as
    on construction, so a wrong type or a missing required field still raises. `source` is a stable label (not a
    per-file path) so the warning is logged once, not once per file."""
    m = cls.model_validate(data, extra="allow")
    unknown = _unknown_fields(m)
    if not unknown:
        return m
    note_schema_skew(source, cls.__name__, unknown)
    return cls.model_validate(data, extra="ignore")


def load_record_json(cls: type[M], text: str | bytes, *, source: str) -> M:
    """`load_record` for JSON text; malformed JSON raises ValueError as model_validate_json does."""
    m = cls.model_validate_json(text, extra="allow")
    unknown = _unknown_fields(m)
    if not unknown:
        return m
    note_schema_skew(source, cls.__name__, unknown)
    return cls.model_validate_json(text, extra="ignore")


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
    # unique name, created exclusively and never through a planted symlink; mode forced to 0600 (security review)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

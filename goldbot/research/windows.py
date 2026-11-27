"""Research data windows W2 (owner directive 2026-11-02; ADR research-windows-w2, supersedes W1's odd/even years),
frozen before any pre-2010 bar or sprint3000 result was read.

| window    | span (UTC, end exclusive)       | use                                                         |
|-----------|---------------------------------|-------------------------------------------------------------|
| learn_old | 2003-01-01 .. 2006-01-01        | extra learning data, new trials only                        |
| embargo   | 2006-01-01 .. 2006-02-01        | not used (gap between learn_old and test_old)               |
| test_old  | 2006-02-01 .. 2010-01-01        | held-out test, read ONCE, only for confirm-passed rules     |
| learn     | 2010-01-01 .. 2018-01-01        | learn / screen                                              |
| confirm   | 2018-01-01 .. 2025-10-01        | one test                                                    |
| holdout   | 2025-10-01 onward               | sealed: never read here                                     |

Why contiguous blocks, not odd/even years (W1): 2003-2009 was one bull regime, so alternate years share it and a
test on even years is not independent of learning on odd ones; a one-month embargo stops month-scale features and
labels that span the boundary from carrying learn_old information into test_old.

A trade belongs to the window of its ENTRY (`window_of`, `entry_mask`); indicator warm-up may read earlier bars
whatever their window (`read_window_bars(warmup_days=...)`), never a holdout bar.

Guards are explicit exceptions, not `assert` (stripped under `python -O`):
- test_old is returned or read only with `allow_test_old=True` (from `allow_test_old_for`); otherwise `TestOldSealed`.
- holdout is never returned or read: `HoldoutSealed`, whatever the flags; the confirm window's 2025 file is cut at
  HOLDOUT_FROM before it leaves this module. The embargo is never a window to read (`EmbargoSealed`).

Read-once ledger (`READ_ONCE_FAMILIES`, `record_read_once`): a registered family's stage reads of a window are each
read once, recorded in a JSONL ledger beside the research registry BEFORE the read (status `started`); a second read
of the same family and window raises `AlreadyRead`, except ONE resume by the same run (the same `resume_key`, e.g.
the frozen-model hash) after a crash left no stage record (the caller checks the record), so a crash never burns the
read and a different run can never take it. H-101 (prereg-2026-11-25-model-zoo-fusion.md, family
`model_zoo_fusion`): Stage L reads learn once; "Stage C, confirm 2018-01..2025-09, read once"; "Stage T, test_old ...,
read once, winner only (`windows.allow_test_old_for`, CONFIRM-SEEN rule)".
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.holdout_guard import HOLDOUT_START, HoldoutSealed
from goldbot.data.release import BAR_FILE_PREFIX, read_bar_files

log = logging.getLogger(__name__)

# The read ledger's lock (code review LOW-1 on PR #478): flock on POSIX, msvcrt.locking on byte 0 on Windows (the VPS
# runs goldbot natively; as data/store.py). Neither available: a ledger write refuses (`LedgerLockUnavailable`).
if sys.platform == "win32":                # pragma: no cover - exercised on the Windows VPS
    import msvcrt

    def _lock_fd(fd: int) -> None:
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.01)

    def _try_lock_fd(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    try:
        import fcntl
    except ImportError:                    # pragma: no cover - a platform with neither lock
        fcntl = None  # type: ignore[assignment]

    def _lock_fd(fd: int) -> None:
        if fcntl is None:
            raise LedgerLockUnavailable("no fcntl on this platform: the read ledger cannot be locked")
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _try_lock_fd(fd: int) -> bool:
        """One non-blocking attempt at the exclusive lock: True when taken."""
        if fcntl is None:
            raise LedgerLockUnavailable("no fcntl on this platform: the read ledger cannot be locked")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock_fd(fd: int) -> None:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)


class LedgerLockUnavailable(RuntimeError):
    """The platform offers no file lock: a read-once ledger is never written unlocked."""


class Window(StrEnum):
    LEARN_OLD = "learn_old"
    EMBARGO = "embargo"
    TEST_OLD = "test_old"
    LEARN = "learn"
    CONFIRM = "confirm"
    HOLDOUT = "holdout"


class TestOldSealed(PermissionError):
    """test_old was asked for without the explicit flag, or for a strategy that has not passed confirm."""

    __test__ = False   # not a pytest class


class EmbargoSealed(PermissionError):
    """The embargo month (2006-01) was asked for as a window. It is never a learn or test window."""


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


FIRST_BAR_FROM = _utc("2003-01-01")
HOLDOUT_FROM = HOLDOUT_START          # 2025-10-01 = data.holdout_guard (its HoldoutSealed is this module's too)
MAX_WARMUP_DAYS = 366

# [start, end) per window, in time order; together they tile FIRST_BAR_FROM .. HOLDOUT_FROM with no hole or overlap
SPANS: dict[Window, tuple[pd.Timestamp, pd.Timestamp]] = {
    Window.LEARN_OLD: (FIRST_BAR_FROM, _utc("2006-01-01")),
    Window.EMBARGO: (_utc("2006-01-01"), _utc("2006-02-01")),
    Window.TEST_OLD: (_utc("2006-02-01"), _utc("2010-01-01")),
    Window.LEARN: (_utc("2010-01-01"), _utc("2018-01-01")),
    Window.CONFIRM: (_utc("2018-01-01"), HOLDOUT_FROM),
}


def _window(w: Window | str) -> Window:
    try:
        return Window(w)
    except ValueError:
        raise ValueError(f"unknown window {w!r}; one of {', '.join(x.value for x in Window)}") from None


def bounds(window: Window | str, *, allow_test_old: bool = False) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) of `window` in UTC. test_old needs `allow_test_old=True`; holdout and embargo always raise."""
    w = _window(window)
    if w is Window.HOLDOUT:
        raise HoldoutSealed("the holdout (2025-10-01 onward) is sealed; no research path reads it")
    if w is Window.EMBARGO:
        raise EmbargoSealed("2006-01 is the embargo between learn_old and test_old; it is never read as a window")
    if w is Window.TEST_OLD and not allow_test_old:
        raise TestOldSealed("test_old (2006-02..2009-12) is read once per strategy, only after it passed confirm; "
                            "pass allow_test_old=allow_test_old_for(id, confirm_passed) from that single read")
    return SPANS[w]


def years(window: Window | str, *, allow_test_old: bool = False) -> tuple[int, ...]:
    """The calendar years `window` touches (test_old: 2006..2009, 2006 from February). Same guards as `bounds`."""
    lo, hi = bounds(window, allow_test_old=allow_test_old)
    return tuple(range(lo.year, (hi - pd.Timedelta(1, "ns")).year + 1))


def window_of(ts: pd.Timestamp | str) -> Window:
    """The window a trade belongs to, by its ENTRY time (UTC; naive times are taken as UTC). Classification only."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if t >= HOLDOUT_FROM:
        return Window.HOLDOUT
    for w, (lo, hi) in SPANS.items():
        if lo <= t < hi:
            return w
    raise ValueError(f"{t} is before any research window (first is {FIRST_BAR_FROM:%Y-%m-%d})")


def entry_mask(ts: Iterable[pd.Timestamp] | pd.Series | pd.DatetimeIndex, window: Window | str, *,
               allow_test_old: bool = False) -> np.ndarray:
    """Boolean per entry time: True where the entry falls in `window`. Runs count only these entries; bars before
    them (warm-up) are not trades. Same guards as `bounds`."""
    lo, hi = bounds(window, allow_test_old=allow_test_old)
    s = ts if isinstance(ts, pd.Series) else pd.Series(list(ts))
    idx = pd.DatetimeIndex(pd.to_datetime(s, utc=True))
    return np.asarray((idx >= lo) & (idx < hi), dtype=bool)


def available_years(folder: Path | str, window: Window | str, *, allow_test_old: bool = False) -> tuple[int, ...]:
    """The years of `window` whose yearly 1m file is on disk in `folder` (old windows are "as available")."""
    folder = Path(folder)
    return tuple(y for y in years(window, allow_test_old=allow_test_old)
                 if (folder / f"{BAR_FILE_PREFIX}{y}.parquet").exists())


def read_window_bars(folder: Path | str, window: Window | str, *, allow_test_old: bool = False,
                     warmup_days: int = 0) -> pd.DataFrame:
    """The 1m bars of `window` from the yearly files in `folder` (release.read_bar_files: error rows dropped, UTC,
    de-duplicated), cut to the window's span, so nothing at or after HOLDOUT_FROM ever leaves.

    `warmup_days` (0..MAX_WARMUP_DAYS) also returns that many calendar days before the window start, whatever window
    they belong to. Callers count only entries where `entry_mask` is True. FileNotFoundError when none of the window's
    years is on disk."""
    if not 0 <= warmup_days <= MAX_WARMUP_DAYS:
        # warm-up is at most a year back: a larger value would pull whole windows in under the name of warm-up
        raise ValueError(f"warmup_days must be in 0..{MAX_WARMUP_DAYS}")
    folder = Path(folder)
    lo, hi = bounds(window, allow_test_old=allow_test_old)
    if not available_years(folder, window, allow_test_old=allow_test_old):
        raise FileNotFoundError(f"no {BAR_FILE_PREFIX}<year>.parquet in {folder} for window {_window(window).value}")
    start = lo - pd.Timedelta(days=warmup_days)
    last_year = (hi - pd.Timedelta(1, "ns")).year
    b = read_bar_files(folder, start.year, last_year)
    out = b[(b["ts_utc"] >= start) & (b["ts_utc"] < hi)].reset_index(drop=True)
    if len(out) and out["ts_utc"].max() >= HOLDOUT_FROM:     # belt and braces: never let a holdout bar out
        raise HoldoutSealed("a holdout bar reached read_window_bars output")
    return out


def label_embargo(max_hold: pd.Timedelta) -> pd.Timedelta:
    """The purge/embargo between a training span and the span judged after it (plan 2026-11-25 W1-B, for sr-v3):
    "EMBARGO = max(1 d, max hold)", so no still-open label and no day-scale feature crosses the cut."""
    hold = pd.Timedelta(max_hold)
    if hold < pd.Timedelta(0):
        raise ValueError(f"a hold is >= 0, got {hold}")
    return max(pd.Timedelta(days=1), hold)


def allow_test_old_for(strategy_id: str, confirm_passed: Iterable[str]) -> bool:
    """The `allow_test_old` flag for one strategy: True only if `strategy_id` is in `confirm_passed` (the ids whose
    confirm read passed), else TestOldSealed. Scripts take `--window test_old` through this, so an id that has not
    passed confirm can never open test_old."""
    if strategy_id not in set(confirm_passed):
        raise TestOldSealed(f"{strategy_id!r} has not passed confirm; test_old stays sealed for it")
    return True


class AlreadyRead(PermissionError):
    """A read-once window of a registered family was asked for a second time."""


# family -> the windows it reads once (pre-registered cards only; a new card adds its family in its own commit)
READ_ONCE_FAMILIES: dict[str, frozenset[Window]] = {
    # H-101, prereg-2026-11-25-model-zoo-fusion.md: Stage L (learn labels), Stage C, Stage T
    "model_zoo_fusion": frozenset({Window.LEARN, Window.CONFIRM, Window.TEST_OLD}),
}
READS_LEDGER = "window_reads.jsonl"     # beside the research registry (state/), never committed


class LedgerCorrupt(ValueError):
    """A read ledger line before the last one does not parse: the ledger is refused, never guessed around."""


def _tail_row(torn: str) -> dict[str, Any] | None:
    """An unterminated final line that parses as a JSON object is a complete row whose newline was lost."""
    try:
        v = json.loads(torn)
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def parse_ledger(text: str, *, where: str = "ledger") -> list[dict[str, Any]]:
    """The rows of a JSONL read ledger. Every newline-terminated line must parse (`LedgerCorrupt` otherwise). An
    unterminated final line that parses as a JSON object is a real row (its newline was lost) and counts; only an
    unparsable final fragment (a writer that died mid-write) is left out and reported (code review MEDIUM-1 on
    PR #478: a recorded read never disappears)."""
    parts = text.split("\n")
    torn = parts.pop()                      # "" when the text ends with a newline
    rows = []
    for i, line in enumerate(parts):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LedgerCorrupt(f"{where}: line {i + 1} does not parse: {exc}") from None
    if torn.strip():
        row = _tail_row(torn)
        if row is not None:
            rows.append(row)
        else:
            log.warning("%s: an unparsable unterminated final line (%d bytes) was left out: a torn write", where,
                        len(torn))
    return rows


def read_ledger(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    return parse_ledger(p.read_text(encoding="utf-8"), where=str(p)) if p.exists() else []


@contextmanager
def locked_ledger(path: Path | str) -> Iterator[int]:
    """An O_APPEND descriptor on the ledger under an exclusive lock, for read-check-append. An unterminated final
    line that parses as a JSON object gets its newline (it stays a row); an unparsable one is moved to
    `<ledger>.torn` and cut, so it never becomes a middle line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    locked = False
    try:
        _lock_fd(fd)
        locked = True
        data = p.read_bytes()
        if data and not data.endswith(b"\n"):
            keep = data.rfind(b"\n") + 1
            tail = data[keep:].decode("utf-8", errors="replace")
            if _tail_row(tail) is not None:
                _write_all(fd, b"\n")
            else:
                with open(p.with_name(p.name + ".torn"), "ab") as fh:
                    fh.write(data[keep:] + b"\n")
                os.ftruncate(fd, keep)
                log.warning("%s: a torn final line (%d bytes) moved to %s.torn", p, len(data) - keep, p.name)
        yield fd
    finally:
        if locked:
            _unlock_fd(fd)
        os.close(fd)


def _write_all(fd: int, buf: bytes) -> None:
    """One os.write of `buf`; a short write is cut back to the size before it and raises (never a partial row)."""
    before = os.fstat(fd).st_size
    try:
        n = os.write(fd, buf)
    except OSError:
        os.ftruncate(fd, before)
        raise
    if n != len(buf):
        os.ftruncate(fd, before)
        raise OSError(f"short write to the read ledger ({n} of {len(buf)} bytes): cut back, nothing recorded")
    os.fsync(fd)


def append_row(fd: int, row: Mapping[str, Any]) -> None:
    """One row, one os.write on the O_APPEND descriptor (all of it or nothing), then fsync."""
    _write_all(fd, (json.dumps(row, default=str) + "\n").encode("utf-8"))


def reads_of(ledger: Path | str, family: str) -> set[Window]:
    """The read-once windows `family` has already read according to `ledger` (a missing ledger holds none)."""
    rows = read_ledger(ledger)
    return {Window(r["window"]) for r in rows if r.get("family") == family}


def _rows_of(ledger: Path, family: str, window: Window) -> list[dict[str, Any]]:
    rows = read_ledger(ledger)
    return [r for r in rows if r.get("family") == family and r.get("window") == window.value]


def record_read_once(ledger: Path | str, family: str, window: Window | str, *, ref: Mapping[str, Any],
                     resume_key: str | None = None) -> None:
    """Record `family`'s single read of `window` BEFORE the read (status started). One resume is allowed for the same
    `resume_key` (status resumed); any other second read raises AlreadyRead. Unregistered family or window:
    ValueError; the holdout and embargo: their sealed errors."""
    w = _window(window)
    if w is Window.HOLDOUT:
        raise HoldoutSealed("the holdout (2025-10-01 onward) is sealed; no research path reads it")
    if w is Window.EMBARGO:
        raise EmbargoSealed("2006-01 is the embargo between learn_old and test_old; it is never read as a window")
    if w not in READ_ONCE_FAMILIES.get(family, frozenset()):
        raise ValueError(f"{family!r} has no read-once registration for {w.value} (windows.READ_ONCE_FAMILIES)")
    p = Path(ledger)
    with locked_ledger(p) as fd:            # read, check and append under one exclusive lock (LOW-A, LOW-B)
        prior = _rows_of(p, family, w)
        status = "started"
        if prior:
            resumable = (len(prior) == 1 and resume_key is not None and prior[0].get("key") == resume_key)
            if not resumable:
                raise AlreadyRead(f"{family!r} already read {w.value}: read once (one resume by the same run only)")
            status = "resumed"
        row = {"family": family, "window": w.value, "status": status, "key": resume_key,
               "ts": pd.Timestamp.now(tz="UTC").isoformat(), "ref": dict(ref)}
        append_row(fd, row)

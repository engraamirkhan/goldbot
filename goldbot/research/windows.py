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
"""
from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.release import BAR_FILE_PREFIX, read_bar_files


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


class HoldoutSealed(PermissionError):
    """The holdout was asked for. Nothing in research reads it."""


class EmbargoSealed(PermissionError):
    """The embargo month (2006-01) was asked for as a window. It is never a learn or test window."""


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


FIRST_BAR_FROM = _utc("2003-01-01")
HOLDOUT_FROM = _utc("2025-10-01")      # = arms.HOLDOUT_FROM, exploration_data.HOLDOUT_START
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


def allow_test_old_for(strategy_id: str, confirm_passed: Iterable[str]) -> bool:
    """The `allow_test_old` flag for one strategy: True only if `strategy_id` is in `confirm_passed` (the ids whose
    confirm read passed), else TestOldSealed. Scripts take `--window test_old` through this, so an id that has not
    passed confirm can never open test_old."""
    if strategy_id not in set(confirm_passed):
        raise TestOldSealed(f"{strategy_id!r} has not passed confirm; test_old stays sealed for it")
    return True

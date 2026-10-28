"""Explore-window guard (design section 3.2 step 1): selection sees 2010-01-01 .. 2017-12-31 and nothing later.

Enforced in code, not by convention. `EXPLORE_CUTOFF` is the first instant that is refused (2018-01-01 00:00 UTC,
the start of the confirm window); every bar timestamp must be strictly before it.

Signatures chosen here (the design leaves them open):
    guard_times(times, *, what="bars") -> None            raises WindowViolation if any time >= EXPLORE_CUTOFF
    truncate_to_explore(df, time_col=None) -> DataFrame   the ONLY place later rows may be dropped (a loader reading a
                                                          wide file); returns rows in [EXPLORE_START, EXPLORE_CUTOFF)
    ExploreFrame.from_frame(df, time_col=None)            guards the index (or `time_col`) and every datetime64 column
                                                          (e.g. a label end time t1), then holds the frame
    ExploreFrame.frame / .digest / len()                  the frame, and a sha256 of its content (the "evaluator
                                                          input digest equals the truncated digest" test)
Times earlier than EXPLORE_START are not an error (warm-up history for indicators is allowed), but they are
never selection data: evaluators score events inside [EXPLORE_START, EXPLORE_CUTOFF) only.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns

EXPLORE_START = pd.Timestamp("2010-01-01", tz="UTC")
EXPLORE_CUTOFF = pd.Timestamp("2018-01-01", tz="UTC")     # first refused instant
_CUTOFF_NS = 1_514_764_800 * 1_000_000_000                # 2018-01-01T00:00Z (asserted equal in tests)
_START_NS = 1_262_304_000 * 1_000_000_000                 # 2010-01-01T00:00Z


class WindowViolation(ValueError):
    """A bar at or after 2018-01-01 reached a selection-stage reader."""


def _as_index(times: pd.DatetimeIndex | pd.Series | pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(times)
    return idx.tz_localize("UTC") if idx.tz is None else idx


def guard_times(times: pd.DatetimeIndex | pd.Series | pd.Index, *, what: str = "bars") -> None:
    """Raise WindowViolation if any timestamp is at or after EXPLORE_CUTOFF (NaT is ignored)."""
    idx = _as_index(times)
    idx = idx.dropna()
    if len(idx) == 0:
        return
    ns = epoch_ns(idx)
    if int(ns.max()) >= _CUTOFF_NS:
        bad = int((ns >= _CUTOFF_NS).sum())
        raise WindowViolation(f"{bad} {what} at or after {EXPLORE_CUTOFF.isoformat()}: the explore window ends "
                              "2017-12-31")


def _times(df: pd.DataFrame, time_col: str | None) -> pd.DatetimeIndex:
    return _as_index(df.index if time_col is None else df[time_col])


def truncate_to_explore(df: pd.DataFrame, time_col: str | None = None) -> pd.DataFrame:
    """Rows with time in [EXPLORE_START, EXPLORE_CUTOFF). The one sanctioned truncation point."""
    ns = epoch_ns(_times(df, time_col))
    keep = (ns >= _START_NS) & (ns < _CUTOFF_NS)
    return df.loc[keep]


def _digest(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(",".join(map(str, df.columns)).encode())
    if isinstance(df.index, pd.DatetimeIndex):
        h.update(epoch_ns(_as_index(df.index)).tobytes())
    else:
        h.update(repr(list(df.index)).encode())
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            h.update(epoch_ns(_as_index(s)).tobytes())
        elif pd.api.types.is_numeric_dtype(s):
            h.update(np.ascontiguousarray(s.to_numpy(dtype="float64")).tobytes())
        else:
            h.update(repr(s.tolist()).encode())
    return h.hexdigest()


class ExploreFrame:
    """A frame proven to hold no bar at or after the cutoff. Construction refuses."""

    __slots__ = ("_frame", "_digest")

    def __init__(self, frame: pd.DataFrame) -> None:
        if isinstance(frame.index, pd.DatetimeIndex):
            guard_times(frame.index)
        for c in frame.columns:                        # every datetime column (ts_utc, visible_at, a label end time)
            if pd.api.types.is_datetime64_any_dtype(frame[c]):
                guard_times(frame[c], what=f"'{c}' values")
        self._frame = frame
        self._digest: str | None = None

    @classmethod
    def from_frame(cls, df: pd.DataFrame, time_col: str | None = None) -> ExploreFrame:
        guard_times(_times(df, time_col), what="bars")
        return cls(df)

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    @property
    def digest(self) -> str:
        if self._digest is None:
            self._digest = _digest(self._frame)
        return self._digest

    def __len__(self) -> int:
        return len(self._frame)

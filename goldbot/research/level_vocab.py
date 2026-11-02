"""The key-level vocabulary of the sprint-3000 screen (brief ADDENDUM A; prereg docs/research/sprint3000/prereg.md
section 1), on the zones of `goldbot.features.key_levels`:

* entry `level_break(min_strength_pct, min_tf)`: "close through the nearest zone of >= that strength spanning >= min_tf,
  side = break direction". Long when the close is above the hi of the nearest such zone above the PREVIOUS decision
  close, short when below the lo of the nearest one at or below it.
* entry `level_reject(min_strength_pct, min_tf)`: "bar enters the zone and closes back out, side = away from the zone".
  Short when the previous close was below the nearest zone above, the bar's high reached its lo and the close is
  back below it; long mirror-wise on the zone below. Both at once: no signal.
* filter `near_level(max_dist_atr, min_strength_pct)`: "entry within max_dist_atr of such a zone". Distance from the
  decision close to the nearer zone's edge (0 inside it), in ATR(14) of the entry timeframe (the unit of the spec's
  stop), any timeframe.
* filter `room_to_level(min_r)`: "the next opposing zone is >= min_r x stop distance away in the trade direction". The
  opposing zone of a long is the nearest zone not entirely below the close (a zone the close sits in leaves no room);
  room = its lo - close, at least 0; a short mirror-wise. Opposing zones are those with strength_pct >=
  ROOM_MIN_STRENGTH_PCT = 75, the top quartile, the same bar the catalogue's near_level uses for "a zone" (frozen
  before any screen result, ADR 0068: the brief names no strength; with every zone counted the nearest is a fresh 15m
  swing or a bare $50 number about 0.16 ATR(1d) away, so the filter would only measure zone density). No opposing zone:
  unlimited room (passes). Fails closed while ATR(14, 1d) warms up, while strength_pct has fewer than PCT_MIN_DAYS of
  samples (`pct_ready`), and for a bar without a side.

Maturity (quant review of #281, F2): a primitive emits nothing (entries 0, filters False) where the book is not
mature (`book_mature`: under MATURE_DAYS = 540 days of input history, the life of a 1w swing), so a window's first
bars never act on a book a full history would see differently. `LevelContext` is built once on the whole history
and sliced to a window (`window`), never rebuilt on the window's own bars.

Point in time: entries compare the bar with the zones known at the previous decision close (the zone existed before
the bar); filters read the book at the decision close itself (the entry is decided there). `LevelContext` builds every
zone view a catalogue needs for one decision timeframe in one pass over the book (`key_levels.key_level_frames`).
Research only: nothing here sizes, gates or places an order.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.features.key_levels import LevelFilter, key_level_frames

ROOM_MIN_STRENGTH_PCT = 75.0          # room_to_level: an opposing zone is a top-quartile zone (ADR 0068)
ENTRY_TYPES = ("level_break", "level_reject")
FILTER_TYPES = ("near_level", "room_to_level")


def _col(z: pd.DataFrame, c: str) -> np.ndarray:
    return z[c].to_numpy(dtype=float)


def _flag(z: pd.DataFrame, c: str) -> np.ndarray:
    """A boolean frame column (False where missing after a shift)."""
    return z[c].eq(True).to_numpy(dtype=bool)


def level_break(bars: pd.DataFrame, prev: pd.DataFrame) -> np.ndarray:
    """+1 / -1 / 0 per decision bar (module docstring). `prev`: the zone frame at the previous decision close."""
    c = bars["close"].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        up = c > _col(prev, "res_hi")
        dn = c < _col(prev, "sup_lo")
    return np.where(_flag(prev, "book_mature"), up.astype(np.int8) - dn.astype(np.int8), 0).astype(np.int8)


def level_reject(bars: pd.DataFrame, prev: pd.DataFrame) -> np.ndarray:
    """+1 / -1 / 0 per decision bar (module docstring). `prev`: the zone frame at the previous decision close."""
    c, h, lo = (bars[k].to_numpy(dtype=float) for k in ("close", "high", "low"))
    pc = np.concatenate(([np.nan], c[:-1]))
    r_lo, s_hi = _col(prev, "res_lo"), _col(prev, "sup_hi")
    with np.errstate(invalid="ignore"):
        short = (pc < r_lo) & (h >= r_lo) & (c < r_lo)
        long_ = (pc > s_hi) & (lo <= s_hi) & (c > s_hi)
    side = long_.astype(np.int8) - short.astype(np.int8)
    return np.where((long_ & short) | ~_flag(prev, "book_mature"), 0, side).astype(np.int8)


def _edge_distance(c: np.ndarray, cur: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Distance from the close up to the nearest zone above and down to the one below, to the zone edge (0 when the
    close is inside it; NaN when there is no zone)."""
    with np.errstate(invalid="ignore"):
        up = np.maximum(_col(cur, "res_lo") - c, 0.0)
        dn = np.maximum(c - _col(cur, "sup_hi"), 0.0)
    return up, dn


def near_level(bars: pd.DataFrame, cur: pd.DataFrame, atr_entry: np.ndarray, max_dist_atr: float) -> np.ndarray:
    """True where the decision close is within max_dist_atr x ATR(14, entry timeframe) of a zone's edge."""
    up, dn = _edge_distance(bars["close"].to_numpy(dtype=float), cur)
    d = np.fmin(up, dn)
    with np.errstate(invalid="ignore"):
        return np.isfinite(d) & (d <= max_dist_atr * np.asarray(atr_entry, dtype=float)) & _flag(cur, "book_mature")


def room_to_level(bars: pd.DataFrame, cur: pd.DataFrame, side: np.ndarray, stop_dist: np.ndarray,
                  min_r: float) -> np.ndarray:
    """True where the opposing zone in the trade's direction is at least min_r x stop distance away (module
    docstring). `cur`: the frame of LevelFilter(ROOM_MIN_STRENGTH_PCT) at the decision close."""
    c = bars["close"].to_numpy(dtype=float)
    s = np.asarray(side)
    stop = np.asarray(stop_dist, dtype=float)
    with np.errstate(invalid="ignore"):
        long_room = np.where(_col(cur, "sup_hi") > c, 0.0, np.maximum(_col(cur, "res_lo") - c, 0.0))
        short_room = np.where(_col(cur, "res_lo") < c, 0.0, np.maximum(c - _col(cur, "sup_hi"), 0.0))
        room = np.where(s > 0, long_room, np.where(s < 0, short_room, np.nan))
        ok = np.where(np.isnan(room), True, room >= min_r * stop)        # no opposing zone: unlimited room
    warm = np.isfinite(_col(cur, "atr_1d")) & _flag(cur, "pct_ready") & _flag(cur, "book_mature")
    return ok & warm & (s != 0) & np.isfinite(stop)


def filter_of(kind: str, params: Mapping[str, Any]) -> LevelFilter:
    """The zone view a level primitive reads."""
    if kind in ENTRY_TYPES:
        return LevelFilter(float(params["min_strength_pct"]), str(params["min_tf"]))
    if kind == "near_level":
        return LevelFilter(float(params["min_strength_pct"]), None)
    if kind == "room_to_level":
        return LevelFilter(ROOM_MIN_STRENGTH_PCT, None)
    raise ValueError(f"{kind!r} is not a key-level primitive")


def filters_for(specs: Iterable[Mapping[str, Any]]) -> list[LevelFilter]:
    """Every zone view the level primitives of these catalogue specs read (entry and filters), each once."""
    out: dict[LevelFilter, None] = {}
    for spec in specs:
        blocks = [spec["entry"], *(spec.get("filters") or [])]
        for b in blocks:
            if b["type"] in ENTRY_TYPES or b["type"] in FILTER_TYPES:
                out[filter_of(b["type"], b.get("params") or {})] = None
    return list(out)


class LevelContext(FrozenRecord):
    """Zone frames at each decision close of one timeframe, for every filter a catalogue needs (one book pass over
    the whole history; a window is a slice of it)."""
    frames: dict[LevelFilter, pd.DataFrame]
    prev_frames: dict[LevelFilter, pd.DataFrame]      # each frame shifted one decision bar, on the whole history
    history_start: pd.Timestamp                        # the first 1m bar the book was built from

    @classmethod
    def build(cls, bars_1m: pd.DataFrame, decision: pd.DataFrame, filters: list[LevelFilter]) -> LevelContext:
        """`decision`: the decision bars (with `visible_at`, their close); `bars_1m`: the 1m bars they were built
        from, from the start of the history (all timeframes' swings come from them)."""
        at = pd.Series(pd.to_datetime(decision["visible_at"], utc=True)).reset_index(drop=True)
        frames = {f: fr.set_axis(decision.index) for f, fr in
                  (key_level_frames(bars_1m, filters, at=at) if filters else {}).items()}
        start = pd.Timestamp(pd.to_datetime(bars_1m["ts_utc"], utc=True).iloc[0]) if len(bars_1m) else at.iloc[0]
        return cls(frames=frames, prev_frames={f: fr.shift(1) for f, fr in frames.items()}, history_start=start)

    def window(self, start: pd.Timestamp, end: pd.Timestamp) -> LevelContext:
        """The decision closes in [start, end) of this whole-history context. ValueError when the context was built
        from bars starting after `start` (a window's own bars would make a younger book than the screen's)."""
        if self.history_start > start:
            raise ValueError(f"LevelContext built from {self.history_start}, after the window start {start}: "
                             "build it once on the whole history and slice it")

        def cut(fr: pd.DataFrame) -> pd.DataFrame:
            t = pd.to_datetime(fr["available_utc"], utc=True)
            return fr[((t >= start) & (t < end)).to_numpy()]
        return LevelContext(frames={f: cut(fr) for f, fr in self.frames.items()},
                            prev_frames={f: fr[self.frames[f].index.isin(cut(self.frames[f]).index)]
                                         for f, fr in self.prev_frames.items()},
                            history_start=self.history_start)

    def at(self, f: LevelFilter) -> pd.DataFrame:
        """The zones at each decision close (filters read these)."""
        return self.frames[f]

    def prev(self, f: LevelFilter) -> pd.DataFrame:
        """The zones at the previous decision close (entries read these); the first row of the history is empty."""
        return self.prev_frames[f]

"""The key-level types of the sprint-3000 vocabulary (brief ADDENDUM A; ADR 0068), on the sprint-3000 market.

The semantics are `goldbot.research.level_vocab`'s, unchanged (this module only feeds it the screen's tape and bars):
* entry `level_break(min_strength_pct, min_tf)`: "close through the nearest zone of >= that strength spanning >= min_tf,
  side = break direction";
* entry `level_reject(min_strength_pct, min_tf)`: "bar enters the zone and closes back out, side = away from the zone"
  (a grade-D null control of the screen);
* filter `near_level(max_dist_atr, min_strength_pct)`: "entry within max_dist_atr of such a zone", in ATR(14) of the
  entry timeframe;
* filter `room_to_level(min_r)`: "the next opposing zone is >= min_r x stop distance away in the trade direction", the
  stop distance being the spec's own (atr_mult x ATR(atr_n)); opposing zones are top-quartile (ROOM_MIN_STRENGTH_PCT).

Point in time: "a zone exists only from the close of the bar that confirms it". The book is built from the screen's 1m
mid tape (each minute visible at its close), read at each decision bar's nominal close `dec`: entries use the zones of
the PREVIOUS decision close, filters the book at the decision close. Nothing acts where the book is not mature (540 days
of tape behind it, the frozen definition).

Warm-up (level batch only): a window's tape must start LEVEL_WARMUP_DAYS before its first decision so the book at every
decision equals the one a longer history would give (`level_window`): the 540-day maturity covers the zones, the
strength percentile pools 250 more days of books, and a 1w swing is confirmed SWING_LAG weeks late at both ends.
"Warm-up may read earlier bars" (W2); trades still belong to the frozen window of their entry. It never reads before
the window's LEVEL_DATA_FLOOR (no window held out from it): explore stays at 2009-01-01, so its earliest decisions see
a younger percentile pool (ADR 0068, amendment 2026-11-03).

The book is built once per decision timeframe, for every zone view the screen's specs need, in the parent process
(`prepare_levels`, a vocab prepare hook) and sent to the workers in `Market.prepared`.
Research only: nothing here sizes, gates or places an order."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.features import key_levels as kl
from goldbot.research import level_vocab as lv
from goldbot.research.sprint3000 import vocab as V
from goldbot.research.sprint3000.market import Bars, Market, Window

LEVEL_WARMUP_DAYS = kl.MATURE_DAYS + kl.PCT_WINDOW_DAYS + 2 * kl.SWING_LAG * 7     # 540 + 250 + 70 = 860
COLUMNS = ("res_lo", "res_hi", "sup_lo", "sup_hi", "atr_1d", "pct_ready", "book_mature")   # what the primitives read
LevelTf = Literal["15m", "1h", "4h", "1d", "1w", "1M"]


# The earliest minute each window's level warm-up may read (quant review F1 of a6e4420f). A warm-up never reaches a
# window that is held out from it: explore stops at 2009-01-01 (B3: 2009 for warm-up only, never the test_old decision
# years 2006-02..2008); confirm may read the explore years it follows, never test_old; test_old and learn_old start
# at the learn_old years (report-only), as W2 already lets test_old read 2005.
LEVEL_DATA_FLOOR: dict[str, pd.Timestamp] = {
    "explore": pd.Timestamp("2009-01-01", tz="UTC"),
    "confirm": pd.Timestamp("2010-01-01", tz="UTC"),
    "test_old": pd.Timestamp("2003-01-01", tz="UTC"),
    "learn_old": pd.Timestamp("2002-01-01", tz="UTC"),
}


def level_window(w: Window) -> Window:
    """The window with its tape starting LEVEL_WARMUP_DAYS before its first decision, but never before the window's
    LEVEL_DATA_FLOOR (KeyError for a window without one) nor later than its frozen warm-up start; its decisions and
    end are unchanged."""
    start = min(w.data_from, max(LEVEL_DATA_FLOOR[w.name], w.dec_from - pd.Timedelta(days=LEVEL_WARMUP_DAYS)))
    return w.model_copy(update={"data_from": start})


def _tape(mk: Market) -> pd.DataFrame:
    t = mk.tape
    return pd.DataFrame({"ts_utc": pd.to_datetime(t.t, utc=True), "open": t.o, "high": t.h, "low": t.l, "close": t.c})


Views = dict[kl.LevelFilter, tuple[pd.DataFrame, pd.DataFrame]]      # zone view -> (at the close, at the previous)


def _fill(mk: Market, tf: str, have: Views, filters: Sequence[kl.LevelFilter]) -> None:
    """Build the views missing from `have` in one book pass over the whole tape."""
    missing = [f for f in dict.fromkeys(filters) if f not in have]
    if not missing:
        return
    b = mk.bars[tf]
    ctx = lv.LevelContext.build(_tape(mk), pd.DataFrame({"visible_at": pd.to_datetime(b.dec, utc=True)}), missing)
    for f in missing:
        have[f] = (ctx.at(f)[list(COLUMNS)], ctx.prev(f)[list(COLUMNS)])


def context(mk: Market, tf: str, filters: Sequence[kl.LevelFilter]) -> lv.LevelContext:
    """The zone views at each `tf` decision close: from `Market.prepared` when the parent built them, else built here
    (once per process, memoised)."""
    have: Views = mk.prepared[("levels", tf)] if ("levels", tf) in mk.prepared else mk.memo.setdefault(("levels", tf), {})
    _fill(mk, tf, have, filters)
    start = pd.Timestamp(int(mk.tape.t[0]) if len(mk.tape.t) else 0, tz="UTC")
    return lv.LevelContext(frames={f: have[f][0] for f in filters}, prev_frames={f: have[f][1] for f in filters},
                           history_start=start)


def prepare_levels(mk: Market, specs: list[V.Spec]) -> None:
    """Build every zone view the specs read, one book pass per decision timeframe (vocab prepare hook)."""
    by_tf: dict[str, list[kl.LevelFilter]] = {}
    for s in specs:
        if s.level_types:
            by_tf.setdefault(s.tf, []).extend(lv.filters_for([s.model_dump(mode="json")]))
    for tf, fs in by_tf.items():
        _fill(mk, tf, mk.prepared.setdefault(("levels", tf), {}), fs)


V.register_prepare(prepare_levels)


def _bars(b: Bars) -> pd.DataFrame:
    return pd.DataFrame({"close": b.c, "high": b.h, "low": b.l})


def _sides(side: np.ndarray) -> V.Sides:
    return side > 0, side < 0


# ---------------------------------------------------------------- entries
class LevelEntry(FrozenRecord):
    min_strength_pct: float = Field(ge=0, le=100)
    min_tf: LevelTf


@V.register_entry("level_break", LevelEntry)
def level_break(p: LevelEntry, b: Bars, mk: Market | None) -> V.Sides:
    f = lv.filter_of("level_break", p.model_dump())
    return _sides(lv.level_break(_bars(b), context(V._need(mk, "level_break"), b.tf, [f]).prev(f)))


@V.register_entry("level_reject", LevelEntry)
def level_reject(p: LevelEntry, b: Bars, mk: Market | None) -> V.Sides:
    f = lv.filter_of("level_reject", p.model_dump())
    return _sides(lv.level_reject(_bars(b), context(V._need(mk, "level_reject"), b.tf, [f]).prev(f)))


# ---------------------------------------------------------------- filters
class NearLevel(FrozenRecord):
    max_dist_atr: float = Field(gt=0)
    min_strength_pct: float = Field(ge=0, le=100)


@V.register_filter("near_level", NearLevel)
def near_level(p: NearLevel, b: Bars, mk: Market, risk: np.ndarray) -> V.Sides:
    f = lv.filter_of("near_level", p.model_dump())
    ok = lv.near_level(_bars(b), context(mk, b.tf, [f]).at(f), V.atr(b, kl.ATR_N, mk), p.max_dist_atr)
    return ok, ok


class RoomToLevel(FrozenRecord):
    min_r: float = Field(gt=0)


@V.register_filter("room_to_level", RoomToLevel)
def room_to_level(p: RoomToLevel, b: Bars, mk: Market, risk: np.ndarray) -> V.Sides:
    """`risk`: each bar's stop distance (the spec's atr_mult x ATR(atr_n)), the R of min_r."""
    f = lv.filter_of("room_to_level", p.model_dump())
    cur, bars, n = context(mk, b.tf, [f]).at(f), _bars(b), len(b.c)
    return (lv.room_to_level(bars, cur, np.ones(n), risk, p.min_r),
            lv.room_to_level(bars, cur, -np.ones(n), risk, p.min_r))

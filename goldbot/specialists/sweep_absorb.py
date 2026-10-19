"""H-48 `sweep_absorb` (docs/research/prereg-kb-2026-10-18.md section 1, written before any outcome existed): fade a
shallow sweep of the previous day's or previous session's extreme that is reclaimed within two bars.

Mechanism (Osler 2005, stop-loss orders sit just beyond prior extremes): a pierce of < 0.5 ATR that closes back inside
within two 15m bars means the stops were hit and absorbed without follow-through. The deep, close-beyond arm is a
breakout (B03, R-04) and is not tested.

The rule, fixed now ("the depth 0.5, reclaim 2 and stop floor are fixed by arithmetic and the mechanism, not tuned"):
* Levels known at the bar (`features.trader._session_levels`): `pdh`/`pdl` and `prev_sess_high`/`prev_sess_low`.
* Pierce: the first bar whose high is above (low below) its level while the previous bar's high was at or below (low at
  or above) the level it had; at most one event per level per 4 bars.
* Absorbed: within bars t..t+2 a close back to or inside the level while the deepest penetration so far is < 0.5 x
  ATR14 read at the bar before the pierce. A penetration of 0.5 ATR or more ends the search (so does 1.0).
* Entry at the close of the reclaim bar, fading the sweep, when that bar starts in the London or New York window.
* Stop: the sweep extreme + 0.25 x ATR14 (at the entry bar) beyond; kept only if the stop distance is 0.6-1.0 x ATR14
  (0.6 is the cost floor, 1.0 = $8 at the median ATR15, which sizes at 0.01 lot). Target 2 x the stop distance, 16 bars.
* The tier-1 news blackout drops a candidate (`in_blackout` == 1), as the batch.

Point in time: every value at bar t comes from bars <= t (tests: appending bars never changes a past candidate). One
trade at a time, the barriers searched on 1m bars from the entry close ("entries/exits on 1m barriers", `barriers_on_1m`:
the stop first only when both fall in one minute) and the lot-fit overlay (0.01 lot, unsizable trades skipped) are the
shared label and report mechanics (`labels.one_at_a_time`, `triple_barrier_1m`, `research_pass.py --lot-fit`).

Research only: `screening` keeps it out of the founders, the pools and the loop grid (GRID_V1 unchanged). Run with
`research.yml -f specialist=sweep_absorb -f variants='["prereg"]' -f lot_fit=true`.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import ValidationError

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.features.technical import atr
from goldbot.features.trader import _session_levels
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import AgentIdentity, Specialist, register
from goldbot.specialists.smc_structure import SmcStructureSpecialist

FAMILY = "sweep_absorb"
MAX_DEPTH_ATR = 0.5         # an absorbed sweep penetrates by less than this
RECLAIM_BARS = 2            # ... and closes back within bars t..t+2
MIN_GAP_BARS = 4            # at most one event per level per this many bars
STOP_BUFFER_ATR = 0.25
MIN_STOP_ATR, MAX_STOP_ATR = 0.6, 1.0
TARGET_R = 2.0
MAX_BARS = 16
SESSIONS = ("london", "newyork")
# (level key, +1 = a high sweep (faded short), -1 = a low sweep (faded long))
LEVELS = (("pdh", 1), ("prev_sess_high", 1), ("pdl", -1), ("prev_sess_low", -1))


class SweepAbsorbConfig(FrozenRecord):
    """The only setting is the pre-registered configuration's name: nothing else can be tuned."""

    rung: Literal["prereg"] = "prereg"


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int),
                         "risk_usd": pd.Series(dtype=float), "max_bars": pd.Series(dtype=int)})


def sweep_events(bars: pd.DataFrame) -> pd.DataFrame:
    """Every absorbed sweep, before the session and stop filters (the pre-registration's "absorbed, all sessions"):
    columns idx (the reclaim bar), side (the faded trade: -1 short after a high sweep), extreme, level, key."""
    cols = ["idx", "side", "extreme", "level", "key"]
    n = len(bars)
    if n < 3:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols})
    h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = atr(bars, 14).to_numpy(dtype=float)
    lv = _session_levels(bars)
    rows: list[tuple[int, int, float, float, str]] = []
    for key, sweep in LEVELS:
        ref = lv[key]
        with np.errstate(invalid="ignore"):
            if sweep > 0:
                pierce = (h[1:] > ref[1:]) & (h[:-1] <= ref[:-1])
            else:
                pierce = (lo[1:] < ref[1:]) & (lo[:-1] >= ref[:-1])
        last = -10 ** 9
        for p_ in np.flatnonzero(pierce) + 1:
            p = int(p_)
            if p - last < MIN_GAP_BARS:
                continue
            last = p
            level, scale = float(ref[p]), float(a[p - 1])
            if not (np.isfinite(level) and np.isfinite(scale) and scale > 0):
                continue
            ext = float(h[p] if sweep > 0 else lo[p])
            for t in range(p, min(p + RECLAIM_BARS + 1, n)):
                ext = float(max(ext, h[t]) if sweep > 0 else min(ext, lo[t]))
                pen = (ext - level) if sweep > 0 else (level - ext)
                if pen >= MAX_DEPTH_ATR * scale:
                    break
                if (c[t] <= level) if sweep > 0 else (c[t] >= level):
                    rows.append((t, -sweep, ext, level, key))
                    break
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["idx", "key"], kind="stable").reset_index(drop=True) if len(out) else out


@register
class SweepAbsorbSpecialist(Specialist):
    family = FAMILY
    timeframe = "15m"
    screening = True
    default_config = SweepAbsorbConfig().model_dump()
    presets = {"prereg": {"rung": "prereg"}}
    # declared meta-model inputs, the 15m level-break set unchanged (a model is pre-registered only after the
    # rule-only screen passes; the prereg fixes no feature, so none is chosen here)
    model_features = SmcStructureSpecialist.model_features
    barriers_on_1m = True             # "entries/exits on 1m barriers" (prereg section 1)

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        super().__init__(identity, **overrides)
        try:
            self.params = SweepAbsorbConfig(**{k: v for k, v in self.config.items()
                                               if k in SweepAbsorbConfig.model_fields})
        except ValidationError as exc:
            raise ValueError(f"{FAMILY}: {exc}") from None

    @property
    def label_spec(self) -> BarrierSpec:
        """Stop 1 R (R = the candidate's stop distance, `barrier_atr`), target 2 R, 16 bars; plain barriers."""
        return BarrierSpec(target_atr=TARGET_R, stop_atr=1.0, max_bars=MAX_BARS, name=FAMILY)

    def _scan(self, bars: pd.DataFrame) -> pd.DataFrame:
        if len(bars) == 0:
            return _empty()
        ev = sweep_events(bars)
        if ev.empty:
            return _empty()
        a = atr(bars, 14).to_numpy(dtype=float)
        c = bars["close"].to_numpy(dtype=float)
        idx = ev["idx"].to_numpy(dtype=int)
        ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
        in_window = np.isin(DEFAULT_SESSIONS.session_label(ts[idx]), SESSIONS)
        side = ev["side"].to_numpy(dtype=int)
        a_t = a[idx]
        # the stop sits beyond the sweep extreme: above it for a short (side -1), below it for a long (side +1)
        stop = ev["extreme"].to_numpy(dtype=float) - side * STOP_BUFFER_ATR * a_t
        dist = side * (c[idx] - stop)
        with np.errstate(invalid="ignore"):
            ok = (in_window & np.isfinite(a_t) & (dist >= MIN_STOP_ATR * a_t - 1e-9)
                  & (dist <= MAX_STOP_ATR * a_t + 1e-9))
        frame = pd.DataFrame({"idx": idx[ok], "side": side[ok], "risk_usd": dist[ok]})
        # one candidate per bar: the first level in key order; opposite sides on one bar make none
        both = frame.groupby("idx")["side"].transform("nunique") > 1
        frame = frame[~both].drop_duplicates("idx").reset_index(drop=True)
        frame["max_bars"] = MAX_BARS
        return frame

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        frame = self._scan(mid_bars)
        if len(frame) and "in_blackout" in features.columns:
            blk = features["in_blackout"].to_numpy(dtype=float)[frame["idx"].to_numpy()]
            frame = frame[~(blk == 1)].reset_index(drop=True)
        return frame

    def barrier_atr(self, mid_bars: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> pd.Series:
        """R per decision bar: the candidate's stop distance in USD, NaN elsewhere (no label)."""
        frame = self._scan(mid_bars)
        r = np.full(len(mid_bars), np.nan)
        r[frame["idx"].to_numpy(dtype=int)] = frame["risk_usd"].to_numpy(dtype=float)
        return pd.Series(r, index=mid_bars.index)

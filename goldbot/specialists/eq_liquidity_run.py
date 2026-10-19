"""H-49 stage 2 `eq_liquidity_run` (docs/research/prereg-kb-2026-10-18.md section 2, written before any outcome
existed; it is dispatched only if the stage-1 base-rate diagnostic `scripts/kb5_magnet_base_rate.py` meets its
pre-registered pass condition): trade toward an equal high or equal low that price is approaching.

Mechanism (our inference from Osler 2003/2005, no published test): equal highs/lows are salient, so stops pile up just
beyond them and price is drawn through them. The claim tested is that price trades through the level.

The rule, fixed now:
* Equal level: `smc_replica.equal_levels` (pivot length 3, threshold 0.1 x ATR200) on the 15m bars; the level is the
  confirmed pivot price, known from its confirming bar (3 bars after the pivot). Active until a bar trades through it
  (high above an equal high, low below an equal low); age 4-96 bars from the confirming bar.
* Approach: the first active bar whose close is 0.3-1.0 x ATR14 from the level on the side it is not yet hit. Entry at
  that close toward the level (long toward an equal high above, short toward an equal low below).
* Target = the level + 0.25 x ATR14 beyond (it must trade through), stop 0.75 x ATR14 against, 16 bars. So the target
  is (0.3..1.0 + 0.25) / 0.75 = 0.73-1.67 R (the prereg text's 1.1-2.3 is an arithmetic slip; the distances are fixed): it varies by candidate and is carried as the candidate's own `target_atr` (in R, R = the stop).
* No trend or session filter (London/New York is a read-only contrast).

Point in time: a level exists from its confirming bar, hit-ness is judged through the entry bar's own high/low, nothing
after bar t is read (tests: appending bars never changes a past candidate). One trade at a time, the stop first on a
shared bar, the lot-fit overlay (0.01 lot, unsizable skipped): the shared label and report mechanics.

Research only: `screening` keeps it out of the founders, the pools and the loop grid (GRID_V1 unchanged). Run with
`research.yml -f specialist=eq_liquidity_run -f variants='["prereg"]' -f lot_fit=true`.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import ValidationError

from goldbot.base import FrozenRecord
from goldbot.features import smc_replica as smc
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import AgentIdentity, Specialist, register
from goldbot.specialists.smc_structure import SmcStructureSpecialist

FAMILY = "eq_liquidity_run"
PIVOT_LEN = 3
MIN_AGE, MAX_AGE = 4, 96          # bars since the confirming bar
NEAR_ATR, FAR_ATR = 0.3, 1.0      # approach band: the close's distance to the level
OVERSHOOT_ATR = 0.25              # the target lies this far beyond the level
STOP_ATR = 0.75
MAX_BARS = 16


class EqLiquidityRunConfig(FrozenRecord):
    """The only setting is the pre-registered configuration's name: nothing else can be tuned."""

    rung: Literal["prereg"] = "prereg"


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int),
                         "risk_usd": pd.Series(dtype=float), "target_atr": pd.Series(dtype=float),
                         "max_bars": pd.Series(dtype=int)})


def confirmed_pivots(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(high, low) flags at the confirming bar of every classic pivot (length 3), equal or not: the stage-1 control."""
    ph, pl = smc.pivot_points(bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float), PIVOT_LEN)
    return ~np.isnan(ph), ~np.isnan(pl)


def approaches(bars: pd.DataFrame, flags: tuple[np.ndarray, np.ndarray] | None = None) -> pd.DataFrame:
    """Every first approach to a level, before any filter beyond the band: idx (the approach bar), side (+1 toward a
    high above), level, confirm (the confirming bar), dist_atr (|close - level| / ATR14). `flags`: the (high, low)
    confirming-bar flags of the levels; None = the equal highs/lows (the rule), `confirmed_pivots` = the stage-1 control."""
    cols = ["idx", "side", "level", "confirm", "dist_atr"]
    n = len(bars)
    h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    if n < 2 * PIVOT_LEN + 2:
        return pd.DataFrame({k: pd.Series(dtype=float) for k in cols})
    a = atr(bars, 14).to_numpy(dtype=float)
    high_flags, low_flags = smc.equal_levels(h, lo, c, length=PIVOT_LEN) if flags is None else flags
    rows: list[tuple[int, int, float, int, float]] = []
    for fl, side in ((high_flags, 1), (low_flags, -1)):
        for conf_ in np.flatnonzero(fl):
            conf = int(conf_)
            level = float(h[conf - PIVOT_LEN] if side > 0 else lo[conf - PIVOT_LEN])
            lo_t, hi_t = conf + MIN_AGE, min(conf + MAX_AGE, n - 1)
            if lo_t > hi_t:
                continue
            # bars after the confirming bar up to the age window's end; a bar that trades through ends the level
            seg = slice(conf + 1, hi_t + 1)
            thru = (h[seg] > level) if side > 0 else (lo[seg] < level)
            first_hit = conf + 1 + int(np.argmax(thru)) if thru.any() else n
            t = np.arange(lo_t, min(hi_t, first_hit - 1) + 1)
            if not len(t):
                continue
            gap = (level - c[t]) if side > 0 else (c[t] - level)       # distance on the side not yet hit
            with np.errstate(invalid="ignore"):
                near = (gap >= NEAR_ATR * a[t]) & (gap <= FAR_ATR * a[t]) & np.isfinite(a[t])
            if near.any():
                k = int(t[int(np.argmax(near))])
                rows.append((k, side, float(level), int(conf), float(gap[int(np.argmax(near))] / a[k])))
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["idx", "confirm"], kind="stable").reset_index(drop=True) if len(out) else out


@register
class EqLiquidityRunSpecialist(Specialist):
    family = FAMILY
    timeframe = "15m"
    screening = True
    default_config = EqLiquidityRunConfig().model_dump()
    presets = {"prereg": {"rung": "prereg"}}
    # declared meta-model inputs, the 15m level-break set unchanged (a model is pre-registered only after the
    # rule-only screen passes; the prereg fixes no feature, so none is chosen here)
    model_features = SmcStructureSpecialist.model_features

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        super().__init__(identity, **overrides)
        try:
            self.params = EqLiquidityRunConfig(**{k: v for k, v in self.config.items()
                                                  if k in EqLiquidityRunConfig.model_fields})
        except ValidationError as exc:
            raise ValueError(f"{FAMILY}: {exc}") from None

    @property
    def label_spec(self) -> BarrierSpec:
        """Stop 1 R (R = 0.75 x ATR14, `barrier_atr`); the target is each candidate's own `target_atr` (the nominal
        value here is the band's midpoint); 16 bars; plain barriers."""
        return BarrierSpec(target_atr=1.7, stop_atr=1.0, max_bars=MAX_BARS, name=FAMILY)

    def _scan(self, bars: pd.DataFrame) -> pd.DataFrame:
        if len(bars) == 0:
            return _empty()
        ev = approaches(bars)
        if ev.empty:
            return _empty()
        a = atr(bars, 14).to_numpy(dtype=float)
        idx = ev["idx"].to_numpy(dtype=int)
        side = ev["side"].to_numpy(dtype=int)
        r = STOP_ATR * a[idx]
        reach = ev["dist_atr"].to_numpy(dtype=float) * a[idx] + OVERSHOOT_ATR * a[idx]
        frame = pd.DataFrame({"idx": idx, "side": side, "risk_usd": r, "target_atr": reach / r})
        # one candidate per bar: the earliest-confirmed level; levels on opposite sides on one bar make none
        both = frame.groupby("idx")["side"].transform("nunique") > 1
        frame = frame[~both].drop_duplicates("idx").reset_index(drop=True)
        frame["max_bars"] = MAX_BARS
        return frame

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return self._scan(mid_bars)

    def barrier_atr(self, mid_bars: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> pd.Series:
        """R per decision bar: 0.75 x ATR14 in USD at the candidate, NaN elsewhere (no label)."""
        frame = self._scan(mid_bars)
        r = np.full(len(mid_bars), np.nan)
        r[frame["idx"].to_numpy(dtype=int)] = frame["risk_usd"].to_numpy(dtype=float)
        return pd.Series(r, index=mid_bars.index)

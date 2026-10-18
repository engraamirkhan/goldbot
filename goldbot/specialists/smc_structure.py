"""Market-structure specialist `smc_structure` (TVR-1 / TVR-2, docs/research/prereg-tv-replica-2026-10-18.md, B2
and the research director's ruling B4a): the owner's chart structure overlay, rebuilt exactly
(`goldbot.features.smc_replica`), traded as two pre-registered rules on 15m decision bars.

* **TVR-1** (`rule: tvr1`, preset `tvr1`, the default). "Long at the close of bar t when `choch_bull_internal[t] = 1`,
  `trend_swing[t-1] = +1` and `fvg_bull` = 1 on any of bars t-2..t. Short mirrored (`choch_bear_internal`, swing
  trend -1, `fvg_bear`)." The pullback inside a swing trend ends when internal structure flips back with a gap.
* **TVR-2** (`rule: tvr2`, preset `tvr2`). "Long at the close of bar t when `bos_bull_swing[t] = 1`. Short when
  `bos_bear_swing[t] = 1`." Continuation after a 50-bar swing high or low breaks. It reads neither the internal scale
  nor the gaps, so `internal_len` and `fvg_lookback` are inert for it and left out of its configuration and agent id.
* Both: stop 1.0 x ATR(14) of the 15m bars, target 2.0 x ATR, at most 32 bars (8 h); plain barriers, no exit policy.

Point in time. Every event comes from the replica's arrays, which are stamped on the bar whose close makes them known:
a leg pivot `length` bars after its extreme, a break on the closing bar, a gap on its third bar. The trend is read at
t-1, the gap on t-2..t. Nothing after bar t's close is read (tests: appending bars never changes a past signal or a
closed label). Both sides cannot fire on one bar (TVR-1's trend condition is opposite per side; a swing BOS one way
sets the trend, so a break the other way on the same bar is a CHoCH, not a BOS).

Series start (B4a). The FVG threshold is a running mean from the first bar and the leg states depend on it, so the
events depend on where the series starts. They are pinned to the first bar of the 2010 Dukascopy 15m series
(`features.smc_live.SERIES_START`): research (`candidates_in_context`) refuses bars that start later. `candidates`
computes on the bars it is given (tests, counts). Live, a window resumes from a carried `ReplicaState`
(`ctx["smc_replica_state"]`, kept by `features.smc_live.ReplicaFeed` in SQLite) of the bar `state_lag` bars before
the decision bar, so every value equals the full-history run: everything is computed from our own bars, never from
TradingView (owner directive 2026-10-18).

One trade at a time in total, not one per side (B4a: the account holds one 0.01 lot trade): `labels.one_at_a_time`,
which research, the shadow book and the engine apply per agent. When stop and target fall in one bar the stop counts
first (`labels.triple_barrier`, B4a). Lot fit (B2: a 1.0 x ATR15 stop fits 0.01 lot at £1,000) is reported by
`research_pass.py --lot-fit`; candidates are not filtered by it, so they equal B2's definitions.

Research only: `screening` keeps it out of the default founders, the pooled models, `ops.jobs.research_families` and
the loop grid (`research.generator._eligible`; GRID_V1 unchanged). Run with
`research.yml -f specialist=smc_structure -f variants='["tvr1","tvr2"]'`.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field, ValidationError

from goldbot.base import FrozenRecord
from goldbot.features import smc_replica as smc
from goldbot.features.smc_live import STATE_KEY, check_series_start
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import AgentIdentity, Specialist, register

FAMILY = "smc_structure"
INERT_FOR_TVR2 = ("internal_len", "fvg_lookback")


class SmcStructureConfig(FrozenRecord):
    """The typed configuration: values are validated and normalised (1 -> 1.0 for a float), so one rule has one id."""

    rule: Literal["tvr1", "tvr2"] = "tvr1"
    swing_len: int = Field(50, ge=2)      # swing-scale leg pivots (the owner's setting)
    internal_len: int = Field(5, ge=2)                   # internal-scale leg pivots (TVR-1 only)
    fvg_lookback: int = Field(2, ge=0)                   # TVR-1: a gap on any of bars t - fvg_lookback .. t
    target_atr: float = Field(2.0, gt=0)
    stop_atr: float = Field(1.0, gt=0)
    max_bars: int = Field(32, ge=1)


DEFAULTS = SmcStructureConfig().model_dump()


def _typed(values: dict[str, Any]) -> dict[str, Any]:
    """`values` with every configuration key validated and normalised by SmcStructureConfig; other keys (timeframe,
    loop filters) pass through to the base class's checks. ValueError on a bad value."""
    own = {k: v for k, v in values.items() if k in DEFAULTS}
    try:
        typed = SmcStructureConfig(**{**DEFAULTS, **own}).model_dump()
    except ValidationError as exc:
        raise ValueError(f"{FAMILY}: {exc}") from None
    return {**values, **{k: typed[k] for k in own}}


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


@register
class SmcStructureSpecialist(Specialist):
    family = FAMILY
    timeframe = "15m"
    screening = True
    default_config = dict(DEFAULTS)
    presets = {"tvr1": {"rule": "tvr1"}, "tvr2": {"rule": "tvr2"}}
    # declared meta-model inputs (a model is pre-registered only after the rule-only screen passes): the move and
    # trend into the break, the volatility regime, the levels around it, the cost and the higher-timeframe trend
    model_features = (
        "ret_1", "ret_4", "ret_16", "ret_96", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile",
        "parkinson_20", "dist_ema20_atr", "dist_ema50_atr", "slope_ema50", "ribbon_state", "adx14", "donchian_pos_20",
        "bb_z_20", "rsi14", "range_width_24_atr", "tick_vol_ratio_20", "tick_count_z_48", "spread_atr",
        "dist_res_atr", "dist_sup_atr", "dow", "h1_adx14", "h1_slope_ema50", "h4_slope_ema50", "h4_dist_ema50_atr",
        "d1_dist_ema50_atr", "d1_slope_ema50",
    )

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        if identity is None:
            overrides = _typed(overrides)
        super().__init__(identity, **overrides)
        self.params = SmcStructureConfig(**{**DEFAULTS, **_typed({k: v for k, v in self.config.items()
                                                                  if k in DEFAULTS})})

    @classmethod
    def unused_config(cls, config: dict[str, Any]) -> set[str]:
        """TVR-2 reads neither the internal scale nor the gaps: those settings cannot make a second id for it."""
        return set(INERT_FOR_TVR2) if config.get("rule") == "tvr2" else set()

    @property
    def label_spec(self) -> BarrierSpec:
        p = self.params
        return BarrierSpec(target_atr=p.target_atr, stop_atr=p.stop_atr, max_bars=p.max_bars, name=FAMILY)

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier"}

    @property
    def replica_params(self) -> smc.ReplicaParams:
        """The replica block this rule reads (the owner's chart settings but for the configured pivot lengths)."""
        return smc.ReplicaParams(swing_len=self.params.swing_len, internal_len=self.params.internal_len)

    @property
    def state_lag(self) -> int:
        """Bars between the carried snapshot and the decision bar: the rule reads the gaps of bars t - fvg_lookback..t
        and the swing trend after bar t - 1, all of which a resumed run computes only after its snapshot."""
        return (self.params.fvg_lookback if self.params.rule == "tvr1" else 0) + 1

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """The bars where the configured rule fires (module docstring), on the replica computed over `mid_bars` as
        the start of the series."""
        if len(mid_bars) == 0:
            return _empty()
        o, h, lo, c = (mid_bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        p = self.params
        st = smc.structure_breaks(o, h, lo, c, p.swing_len, p.internal_len)
        fb, fs = smc.fair_value_gaps(o, h, lo, c) if p.rule == "tvr1" else (None, None)
        trend_prev = np.concatenate(([0.0], st["trend_swing"][:-1]))
        return self._rule(st, fb, fs, trend_prev, 0)

    def _rule(self, ev: Any, fb: Any, fs: Any, trend_prev: np.ndarray, first: int) -> pd.DataFrame:
        """Candidates from the replica's event arrays (`ev[...]`, one value per row); rows before `first` lack the
        bars the rule reads and make none."""
        p = self.params
        if p.rule == "tvr2":
            long = np.asarray(ev["bos_bull_swing"], dtype=bool)
            short = np.asarray(ev["bos_bear_swing"], dtype=bool)
        else:
            w = p.fvg_lookback + 1
            gap_b = pd.Series(np.asarray(fb, dtype=float)).rolling(w, min_periods=1).max().to_numpy() > 0
            gap_s = pd.Series(np.asarray(fs, dtype=float)).rolling(w, min_periods=1).max().to_numpy() > 0
            long = np.asarray(ev["choch_bull_internal"], dtype=bool) & (trend_prev == 1) & gap_b
            short = np.asarray(ev["choch_bear_internal"], dtype=bool) & (trend_prev == -1) & gap_s
        side = np.where(long, 1, np.where(short, -1, 0))
        side[:first] = 0
        idx = np.flatnonzero(side)
        return pd.DataFrame({"idx": idx.astype(int), "side": side[idx].astype(int)})

    def _resumed(self, mid_bars: pd.DataFrame, state: smc.ReplicaState) -> pd.DataFrame:
        """Candidates on the rows after the snapshot whose read bars all follow it; idx positional in `mid_bars`."""
        bars = mid_bars.reset_index(drop=True)
        frame, _ = smc.replica_run(bars, self.replica_params, state)       # refuses stale or foreign state
        if frame.empty:
            return _empty()
        offset = len(bars) - len(frame)
        trend = frame["trend_swing"].to_numpy(dtype=float)
        trend_prev = np.concatenate(([float(state.structure.trend_swing)], trend[:-1]))
        out = self._rule(frame, frame.get("fvg_bull"), frame.get("fvg_bear"), trend_prev, self.state_lag - 1)
        return out.assign(idx=out["idx"] + offset)

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None,
                              ctx: dict[str, Any] | None = None) -> pd.DataFrame:
        """Research's and the engine's entry point. Without `ctx["smc_replica_state"]` the bars must start the series
        at the pinned SERIES_START (B4a, research: run with --from-year 2010); with it (the engine's window and the
        snapshot `state_lag` bars before its decision bar) the replica resumes from the carried state."""
        state = (ctx or {}).get(STATE_KEY)
        if state is not None:
            return self._resumed(mid_bars, state)
        if len(mid_bars):
            try:
                check_series_start(mid_bars)
            except ValueError as exc:
                raise ValueError(f"{FAMILY}: {exc} (run research with --from-year 2010)") from None
        return self.candidates(mid_bars, features)

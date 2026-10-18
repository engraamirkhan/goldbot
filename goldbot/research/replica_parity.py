"""Replica parity: compare goldbot's own structure / money-flow / channel indicators (`features.smc_replica`) with
values pulled from the charting site over a time window, plot by plot.

This is an optional health check. Its result is OK, WARN or SKIP and is never a dependency: the replica is computed
from our bars whether or not a pull exists, and nothing here feeds a model or an order. It reads no return.

Inputs
* `bars`: the chart's own bars, columns `bar_time` (UTC), open, high, low, close, volume, in time order. Pass the
  whole available history. Indicators are computed on all of it and compared only inside [start, end), so warm-up
  and running thresholds match the chart.
* `plots`: long frame with columns `bar_time`, `pine_id`, `plot_id` ("plot_<n>") and `value`, as the pull writes it.
* `studies`: maps a pull's study id to the replica's letters, "a" (structure overlay), "b" (S/R boxes) or "c" (money
  flow). Without it, the letter is guessed from how many plots carry values (>= 25 a, >= 10 b, else c).

Pass criteria: a line is exact when NaN positions agree and every relative error is <= `LINE_RTOL`. An event plot
passes when precision and recall are both >= `EVENT_MIN`. A state plot passes when the share of equal bars is
>= `STATE_MIN`. Any plot below its bar makes the report WARN.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.features import smc_replica as R

LINE_RTOL = 1e-6
EVENT_MIN = 0.995
STATE_MIN = 0.999
Kind = Literal["line", "event", "state"]
Status = Literal["OK", "WARN", "SKIP"]


class PlotParity(FrozenRecord):
    plot: str                 # "<letter>:<plot_id> <replica name>"
    kind: Kind
    n: int                    # bars compared (lines/states) or chart events (events)
    n_replica: int = 0        # replica events (events only)
    precision: float = 1.0
    recall: float = 1.0
    max_rel: float = 0.0
    share_equal: float = 1.0
    ok: bool
    exact: bool = False       # every bar agrees (lines: within LINE_RTOL)


class ParityReport(FrozenRecord):
    status: Status
    start: str
    end: str
    bars_compared: int
    rows: tuple[PlotParity, ...]
    note: str = ""

    def failing(self) -> list[PlotParity]:
        return [r for r in self.rows if not r.ok]

    def inexact(self) -> list[PlotParity]:
        return [r for r in self.rows if not r.exact]


Compute = Callable[[dict[str, np.ndarray], R.ReplicaParams], np.ndarray]


def _ohlcv(cache: dict[str, np.ndarray]) -> tuple[np.ndarray, ...]:
    return cache["open"], cache["high"], cache["low"], cache["close"], cache["volume"]


def _memo(cache: dict[str, Any], key: str, fn: Callable[[], Any]) -> Any:
    if key not in cache:
        cache[key] = fn()
    return cache[key]


def _structure(x: dict[str, Any], p: R.ReplicaParams) -> dict[str, np.ndarray]:
    o, h, lo, c, _ = _ohlcv(x)
    return _memo(x, "st", lambda: R.structure_breaks(o, h, lo, c, p.swing_len, p.internal_len, p.internal_candle_filter))


def _mfi(x: dict[str, Any], p: R.ReplicaParams) -> np.ndarray:
    o, h, lo, c, v = _ohlcv(x)
    m = _memo(x, "mfi", lambda: R.money_flow(h, lo, c, v, p.mfi_len))
    return np.where(np.isfinite(m), m, np.nan)


def _mfi_cross(x: dict[str, Any], p: R.ReplicaParams, which: str) -> np.ndarray:
    m = _mfi(x, p)
    os_ = R.cross_under(m, np.full(len(m), p.mfi_oversold))
    ob_ = R.cross_over(m, np.full(len(m), p.mfi_overbought))
    return {"os": os_, "ob": ob_, "any": os_ | ob_}[which]


def _channels(x: dict[str, Any], p: R.ReplicaParams) -> tuple[np.ndarray, np.ndarray]:
    o, h, lo, c, _ = _ohlcv(x)
    return _memo(x, "sr", lambda: R.sr_channel_breaks(o, h, lo, c, p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels,
                                                      p.sr_lookback))


def _ma(x: dict[str, Any], scheme: str, key: str) -> np.ndarray:
    return _memo(x, f"ma_{scheme}", lambda: R.ma_ribbon(x["close"], scheme))[key]


def _candles(x: dict[str, Any], p: R.ReplicaParams, traverse: bool, codes: Mapping[int, int]) -> np.ndarray:
    o, _, _, c, v = _ohlcv(x)
    cc = R.candle_class(o, c, v, p.candle_vol_len, p.candle_high_mult, p.candle_low_mult, traverse)
    lut = np.full(int(cc.max(initial=0)) + 1, -1.0)
    for k, val in codes.items():
        if k < len(lut):
            lut[k] = val
    return lut[cc]


def _range(x: dict[str, Any], p: R.ReplicaParams, i: int) -> np.ndarray:
    _, h, lo, _, _ = _ohlcv(x)
    return R.range_retracement(h, lo, p.range_len, p.range_fracs)[i]


def _eq(x: dict[str, Any], p: R.ReplicaParams, i: int) -> np.ndarray:
    _, h, lo, c, _ = _ohlcv(x)
    return _memo(x, "eq", lambda: R.equal_levels(h, lo, c, p.equal_len, p.equal_threshold, p.equal_atr_len))[i]


def _fvg(x: dict[str, Any], p: R.ReplicaParams, i: int) -> np.ndarray:
    o, h, lo, c, _ = _ohlcv(x)
    return _memo(x, "fvg", lambda: R.fair_value_gaps(o, h, lo, c, p.fvg_auto_threshold))[i]


A_CANDLE = {R.CANDLE_HIGH_BULL: 60, R.CANDLE_HIGH_BEAR: 61, R.CANDLE_LOW_VOL: 62, R.CANDLE_ENGULF: 63}
B_CANDLE = {R.CANDLE_HIGH_BULL: 3, R.CANDLE_HIGH_BEAR: 4, R.CANDLE_LOW_VOL: 5, R.CANDLE_ENGULF: 6, R.CANDLE_TRAVERSE: 7}

# (letter, plot id, replica name, kind, compute). Plots that are constant under the owner's settings (switched-off
# shapes, colour indices) are not listed: they carry no information to check.
PLOTS: tuple[tuple[str, str, str, Kind, Compute], ...] = (
    *(("a", f"plot_{i}", k, "line", (lambda k: lambda x, p: x[k])(k)) for i, k in enumerate(("open", "high", "low", "close"))),
    *(("a", f"plot_{7 + i}", k, "event", (lambda k: lambda x, p: _structure(x, p)[k])(k))
      for i, k in enumerate(R.STRUCTURE_EVENTS)),
    ("a", "plot_19", "eqh", "event", lambda x, p: _eq(x, p, 0)),
    ("a", "plot_20", "eql", "event", lambda x, p: _eq(x, p, 1)),
    ("a", "plot_21", "fvg_bull", "event", lambda x, p: _fvg(x, p, 0)),
    ("a", "plot_22", "fvg_bear", "event", lambda x, p: _fvg(x, p, 1)),
    ("a", "plot_27", "sma10", "line", lambda x, p: _ma(x, "sma_ema", "ma10")),
    ("a", "plot_29", "sma20", "line", lambda x, p: _ma(x, "sma_ema", "ma20")),
    ("a", "plot_31", "ema50", "line", lambda x, p: _ma(x, "sma_ema", "ma50")),
    ("a", "plot_33", "sma200", "line", lambda x, p: _ma(x, "sma_ema", "ma200")),
    ("a", "plot_37", "candle_class", "state", lambda x, p: _candles(x, p, False, A_CANDLE)),
    ("a", "plot_38", "range_f1", "line", lambda x, p: _range(x, p, 0)),
    ("a", "plot_39", "range_f2", "line", lambda x, p: _range(x, p, 1)),
    ("b", "plot_2", "sr_break_up", "event", lambda x, p: _channels(x, p)[0]),
    ("b", "plot_3", "sr_break_dn", "event", lambda x, p: _channels(x, p)[1]),
    ("b", "plot_4", "ema10_x_ema20_up", "event", lambda x, p: R.cross_over(_ma(x, "ema", "ma10"), _ma(x, "ema", "ma20"))),
    ("b", "plot_6", "ema10_x_ema20_down", "event", lambda x, p: R.cross_under(_ma(x, "ema", "ma10"), _ma(x, "ema", "ma20"))),
    *(("b", pid, f"ema{n}", "line", (lambda n: lambda x, p: _ma(x, "ema", f"ma{n}"))(n))
      for pid, n in (("plot_16", 5), ("plot_8", 10), ("plot_10", 20), ("plot_12", 50), ("plot_14", 200))),
    ("b", "plot_18", "candle_class_traverse", "state", lambda x, p: _candles(x, p, True, B_CANDLE)),
    ("c", "plot_0", "mfi", "line", _mfi),
    ("c", "plot_2", "mfi_cross_marker", "line", lambda x, p: np.where(_mfi_cross(x, p, "any"), _mfi(x, p), np.nan)),
    ("c", "plot_4", "mfi_os_cross", "event", lambda x, p: _mfi_cross(x, p, "os")),
    ("c", "plot_5", "mfi_ob_cross", "event", lambda x, p: _mfi_cross(x, p, "ob")),
    ("c", "plot_6", "mfi_any_cross", "event", lambda x, p: _mfi_cross(x, p, "any")),
)


def guess_studies(plots: pd.DataFrame) -> dict[str, str]:
    used = plots[plots["value"].notna()].groupby("pine_id")["plot_id"].nunique()
    return {str(k): ("a" if n >= 25 else "b" if n >= 10 else "c") for k, n in used.items()}


def _row(name: str, kind: Kind, tv: np.ndarray, us: np.ndarray) -> PlotParity:
    if kind == "line":
        ok_t, ok_u = ~np.isnan(tv), ~np.isnan(us)
        both = ok_t & ok_u
        rel = np.abs(tv[both] - us[both]) / np.maximum(np.abs(tv[both]), 1e-12)
        mx = float(rel.max()) if len(rel) else 0.0
        good = bool((ok_t == ok_u).all() and mx <= LINE_RTOL)
        return PlotParity(plot=name, kind=kind, n=int(both.sum()), max_rel=mx, ok=good, exact=good)
    if kind == "state":
        share = float(np.mean(np.nan_to_num(tv, nan=-1.0) == us)) if len(tv) else 1.0
        return PlotParity(plot=name, kind=kind, n=len(tv), share_equal=share, ok=share >= STATE_MIN, exact=share == 1.0)
    t = np.nan_to_num(tv).astype(bool)
    m = us.astype(bool)
    tp, nt, nm = int((t & m).sum()), int(t.sum()), int(m.sum())
    prec = tp / nm if nm else float(nt == 0)
    rec = tp / nt if nt else float(nm == 0)
    return PlotParity(plot=name, kind=kind, n=nt, n_replica=nm, precision=prec, recall=rec,
                      ok=prec >= EVENT_MIN and rec >= EVENT_MIN, exact=nt == nm == tp)


def parity(bars: pd.DataFrame, plots: pd.DataFrame, start: Any = None, end: Any = None,
           params: R.ReplicaParams = R.DEFAULT_PARAMS, studies: Mapping[str, str] | None = None,
           daily: bool = False) -> ParityReport:
    """Per-plot parity inside [start, end). `daily=True` skips the channel plots (the box indicator draws none on
    daily bars). Plots missing from the pull are skipped; no plot at all gives SKIP."""
    b = bars.sort_values("bar_time").reset_index(drop=True)
    ts = pd.DatetimeIndex(pd.to_datetime(b["bar_time"], utc=True))
    lo_ts = pd.Timestamp(start, tz="UTC") if start is not None and pd.Timestamp(start).tzinfo is None else start
    hi_ts = pd.Timestamp(end, tz="UTC") if end is not None and pd.Timestamp(end).tzinfo is None else end
    mask = np.ones(len(b), dtype=bool)
    if lo_ts is not None:
        mask &= np.asarray(ts >= pd.Timestamp(lo_ts))
    if hi_ts is not None:
        mask &= np.asarray(ts < pd.Timestamp(hi_ts))
    span = (str(ts[mask][0]) if mask.any() else "", str(ts[mask][-1]) if mask.any() else "")
    letters = dict(studies) if studies is not None else guess_studies(plots)
    pl = plots.assign(letter=plots["pine_id"].astype(str).map(letters))
    pl = pl[pl["letter"].notna()]
    wide = pl.pivot_table(index="bar_time", columns=["letter", "plot_id"], values="value", aggfunc="first",
                          dropna=False)
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index, utc=True))
    cache: dict[str, Any] = {k: b[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    rows = []
    for letter, pid, name, kind, fn in PLOTS:
        if (letter, pid) not in wide.columns or (daily and name.startswith("sr_break")):
            continue
        tv = wide[(letter, pid)].reindex(ts).to_numpy(dtype=float)
        us = np.asarray(fn(cache, params), dtype=float)
        rows.append(_row(f"{letter}:{pid} {name}", kind, tv[mask], us[mask]))
    if not rows or not mask.any():
        return ParityReport(status="SKIP", start=span[0], end=span[1], bars_compared=int(mask.sum()), rows=tuple(rows),
                            note="no comparable plots or bars in the window")
    status: Status = "OK" if all(r.ok for r in rows) else "WARN"
    return ParityReport(status=status, start=span[0], end=span[1], bars_compared=int(mask.sum()), rows=tuple(rows))

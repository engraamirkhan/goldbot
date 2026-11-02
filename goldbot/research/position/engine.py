"""The daily position engine (amendment W3.2, W3.3, W3.6): target exposure -> held units -> daily P&L by venue.

W3.2: "P&L is computed per instrument and summed for a book, in units of starting equity, with no compounding:
pnl_d = e_{d-1} x r_d - costs_d - financing_d ... costs_d = RT_point_bps / 2 x |delta e| on each rebalance.
Disaster-stop exits also pay the C1 overshoot." "Disaster stop ... 3 x ATR(20, 1d) from the last rebalance price ...
If it is hit, exposure is 0 until the next daily decision. On that decision, the exposure is restored only if the
signal still says so." "No-trade band: use the band the spec states; if it states none, 10% of the target."

Mechanics (decision at the 21:00 UTC close of day d-1, fill at day d's open, the first 1m open after that close):
* the target exposure e (equity units) becomes held UNITS u = e / open_d at the fill, so a held position's exposure
  moves with price and a rebalance happens only when the target leaves the band: |e - u x close_{d-1}| > band x |e|
  (a change of sign or a target of 0 always trades). A NaN decision keeps the position;
* the day's P&L is a_d x (open_d - close_{d-1}) + b_d x (x_d - open_d): a = units held into the day, b = units after
  the fill, x = the close, or the stop price on a stop day. Summed over a holding this is units x (exit - entry), the
  label engine's trade return (`triple_barrier_next_open_1m` parity); per day it is e_{d-1} x r_d with r marked at
  the 21:00 close (the fill-to-fill split of W3.2 moves only the minutes between that close and the next open);
* the stop is checked first at the open (a gap through it exits there, and the decision is not filled), then on the
  day's range; a stop exit pays half the round trip plus the overshoot;
* every run is flat outside its window: decisions before the window's first day are 0, and the position is closed at
  the last window day's close (paying the exit), so no P&L or trade crosses a window edge;
* financing: the units held at close d-1 carry that close's rollovers (`financing`), at the rate as of that close.

The path (units, stops) does not depend on the venue; each venue books it with its own costs and financing.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.config import PositionSettings
from goldbot.research.position import stats
from goldbot.research.position.costs import PointCosts
from goldbot.research.position.financing import Rates, Venue, rollover_nights
from goldbot.research.position.signals import Panel, PositionSpec, assert_point_in_time, constant, evaluate
from goldbot.research.position.windows import Window, assert_before_holdout

TRADING_DAYS = 252


class EngineConfig(FrozenRecord):
    """W3's fixed program values (research.position); tests switch parts off to isolate one mechanic."""
    target_vol: float | None = Field(0.10, gt=0)       # None: no vol scaling (the signal is the exposure)
    vol_span: int = Field(60, ge=2)
    max_leverage: float = Field(2.0, gt=0)
    band: float = Field(0.10, ge=0, lt=1)
    stop_atr: float | None = Field(3.0, gt=0)          # None: no disaster stop (parity tests only)
    atr_days: int = Field(20, ge=1)
    warmup_days: int = Field(260, ge=0)
    pit_cuts: int = Field(3, ge=0)                     # point-in-time checks per run (0: off)
    null_draws: int = Field(1000, ge=1)
    null_seed: int = 20261102
    boot_resamples: int = Field(4000, ge=10)
    boot_block_days: float = Field(20.0, gt=1)

    @classmethod
    def from_settings(cls, s: PositionSettings) -> EngineConfig:
        return cls(target_vol=s.target_vol, vol_span=s.vol_span_days, max_leverage=s.max_leverage, band=s.band,
                   stop_atr=s.stop_atr, atr_days=s.atr_days, warmup_days=s.warmup_days, null_draws=s.null_draws,
                   null_seed=s.null_seed, boot_resamples=s.boot_resamples, boot_block_days=s.boot_block_days)


class AssetPath(FrozenRecord):
    """One market's held units per day: a (into the day), b (after the open's fill), held (at the close), x (the
    intraday segment's end price), and the stop flags."""
    symbol: str
    dates: pd.DatetimeIndex
    open: np.ndarray
    close: np.ndarray
    close_utc: pd.DatetimeIndex
    a: np.ndarray
    b: np.ndarray
    held: np.ndarray
    x: np.ndarray
    stop_open: np.ndarray
    stop_day: np.ndarray
    in_window: np.ndarray


class VenueResult(FrozenRecord):
    venue: str
    gates: bool
    book: stats.PositionStats
    assets: dict[str, stats.PositionStats]
    daily: pd.DataFrame                                # the book: gross, cost, financing, net, long, short
    assets_daily: dict[str, pd.DataFrame]


class PositionResult(FrozenRecord):
    spec_id: str
    window: str
    rate_source: str                                   # "DTB3" or "constant": a constant-rate run cannot gate
    venues: dict[str, VenueResult]


# ---------------------------------------------------------------- sizing
def ewma_vol(close: pd.Series, span: int) -> pd.Series:
    """Annualised EWMA volatility of daily log returns (zero-mean, RiskMetrics), LAGGED ONE DAY (W3.6): the value on
    d uses returns up to d-1."""
    r = pd.Series(np.log(close.to_numpy(dtype=float)), index=close.index).diff()
    var = (r ** 2).ewm(span=span, min_periods=max(2, span // 3)).mean()
    return np.sqrt(var * TRADING_DAYS).shift(1)


def atr(bars: pd.DataFrame, n: int) -> np.ndarray:
    """ATR(n) as the simple mean of the true range, known at each day's close."""
    h, lo, c = bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float)
    cp = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - lo, np.abs(h - cp), np.abs(lo - cp)]), axis=0)
    return np.asarray(pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy())


def targets(spec: PositionSpec, panel: Panel, cfg: EngineConfig) -> dict[str, pd.Series]:
    """Target exposure per market per decision date: the signal, vol-scaled (`sizing="vol"`), capped at
    max_leverage, 0 during each market's warm-up; NaN (no decision) passes through."""
    out: dict[str, pd.Series] = {}
    for s, sig in evaluate(spec, panel).items():
        assert_before_holdout(sig.index, what=f"{spec.id} signal dates")
        bars = panel[s]
        v = sig.reindex(bars.index)
        if spec.sizing == "vol" and cfg.target_vol is not None:
            v = v * (cfg.target_vol / ewma_vol(bars["close"], cfg.vol_span))
        v = v.clip(-cfg.max_leverage, cfg.max_leverage)
        if spec.long_only and bool((v < 0).any()):
            raise ValueError(f"{spec.id} is long/flat but its exposure for {s} goes short")
        v.iloc[:cfg.warmup_days] = 0.0
        out[s] = v.rename(s)
    return out


def _book_level(spec: PositionSpec, tg: dict[str, pd.Series], panel: Panel, cfg: EngineConfig) -> dict[str, pd.Series]:
    """Flat book on days with fewer than `min_live` live markets; a portfolio target on an EWMA covariance."""
    if spec.min_live <= 1 and spec.book_target_vol is None:
        return tg
    e = pd.DataFrame(tg)
    live = pd.DataFrame({s: pd.Series(np.arange(len(panel[s])) >= cfg.warmup_days, index=panel[s].index)
                         for s in tg}).reindex(e.index).fillna(False).astype(bool)
    if spec.book_target_vol is not None:
        # zero-mean EWMA covariance (RiskMetrics, as `ewma_vol`), through d-1: lagged one day, as W3.6
        r = pd.DataFrame({s: pd.Series(np.log(panel[s]["close"].to_numpy(dtype=float)), index=panel[s].index).diff()
                          for s in tg}).reindex(e.index)
        rv = r.fillna(0.0).to_numpy()
        k = rv.shape[1]
        prod = pd.DataFrame((rv[:, :, None] * rv[:, None, :]).reshape(len(rv), k * k), index=e.index)
        prod.iloc[0] = np.nan
        cov = prod.ewm(span=cfg.vol_span, min_periods=max(2, cfg.vol_span // 3)).mean().shift(1).to_numpy()
        held = e.ffill().fillna(0.0).to_numpy(dtype=float)
        scale = np.full(len(e), np.nan)
        for i in range(len(e)):
            c = cov[i].reshape(k, k)
            if np.isfinite(c).all():
                v = float(held[i] @ c @ held[i]) * TRADING_DAYS
                scale[i] = min(cfg.max_leverage, spec.book_target_vol / np.sqrt(v)) if v > 0 else np.nan
        e = e.mul(pd.Series(scale, index=e.index).fillna(0.0), axis=0)
    e.loc[(live.sum(axis=1) < spec.min_live).to_numpy()] = 0.0
    return {s: e[s].reindex(panel[s].index) for s in tg}


# ---------------------------------------------------------------- path
def simulate(target: pd.Series, bars: pd.DataFrame, cfg: EngineConfig, *, stop_atr: float | None, band: float,
             window: Window, symbol: str = "") -> AssetPath:
    """Held units per day from the decision series (see the module docstring for the rules)."""
    bars = bars.loc[bars.index <= window.last]
    dates = pd.DatetimeIndex(bars.index)
    n = len(dates)
    o, hi, lo, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    t = target.reindex(dates).to_numpy(dtype=float, copy=True)
    t[dates < window.first] = 0.0
    k_atr = atr(bars, cfg.atr_days) if stop_atr is not None else np.full(n, np.nan)
    a, b, held, x = np.zeros(n), np.zeros(n), np.zeros(n), c.copy()
    stop_open, stop_day = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    in_window = np.asarray(dates >= window.first)
    prev, level, fill = 0.0, np.nan, np.nan
    for d in range(n):
        a[d] = prev
        if prev != 0 and np.isnan(level) and stop_atr is not None and d >= 1 and np.isfinite(k_atr[d - 1]):
            level = fill - np.sign(prev) * stop_atr * k_atr[d - 1]     # entered before ATR(n) existed: set it now
        if prev != 0 and np.isfinite(level) and ((prev > 0 and o[d] <= level) or (prev < 0 and o[d] >= level)):
            stop_open[d], bd, level = True, 0.0, np.nan
        else:
            e = t[d - 1] if d >= 1 else 0.0
            bd = prev
            if np.isfinite(e) and abs(e - prev * (c[d - 1] if d >= 1 else 0.0)) > band * abs(e):
                bd, fill = e / o[d], o[d]
                level = o[d] - np.sign(bd) * stop_atr * k_atr[d - 1] if (bd != 0 and stop_atr is not None and d >= 1
                                                                         and np.isfinite(k_atr[d - 1])) else np.nan
            if bd != 0 and np.isfinite(level) and ((bd > 0 and lo[d] <= level) or (bd < 0 and hi[d] >= level)):
                stop_day[d], x[d] = True, level
        b[d] = bd
        held[d] = 0.0 if (stop_day[d] or stop_open[d] or d == n - 1) else bd
        if held[d] == 0.0:
            level = np.nan
        prev = held[d]
    cu = bars["close_utc"] if "close_utc" in bars else (dates + pd.Timedelta(hours=21)).tz_localize("UTC")
    return AssetPath(symbol=symbol, dates=dates, open=o, close=c, close_utc=pd.DatetimeIndex(cu), a=a, b=b, held=held,
                     x=x, stop_open=stop_open, stop_day=stop_day, in_window=in_window)


# ---------------------------------------------------------------- booking
def _book(p: AssetPath, a: np.ndarray, b: np.ndarray, held: np.ndarray, *, x: np.ndarray, stop_open: np.ndarray,
          stop_day: np.ndarray, costs: PointCosts, venue: Venue, rate_prev: np.ndarray, nights: np.ndarray,
          roll: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(gross, cost, financing) per day for units a/b/held, each (n,) or (draws, n); costs positive, financing signed."""
    cp = np.concatenate([[np.nan], p.close[:-1]])
    o = p.open
    h = (costs.rt_bps + venue.extra_rt_bps) / 2.0 * 1e-4
    ov = costs.overshoot_bps * 1e-4
    gross = np.nan_to_num(a * (o - cp)) + b * (x - o)
    cost = (h * np.abs(b - a) * o + ov * stop_open * np.abs(a) * o + (h + ov * stop_day) * np.abs(b - held) * x
            + 2 * h * roll * np.abs(held) * p.close)
    fin = venue.financing_pnl(p.symbol, np.nan_to_num(a * cp), rate_prev, nights)
    return gross, cost, fin


def _rates_nights(p: AssetPath, rates: Rates, venue: Venue) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = rates.pct(p.symbol, p.close_utc)
    rate_prev = np.concatenate([[0.0], r[:-1]])
    nights = np.concatenate([[0], rollover_nights(p.dates[:-1], p.dates[1:], triple_weekday=venue.triple_weekday)])
    nxt = np.concatenate([p.dates.month[1:].to_numpy() != p.dates.month[:-1].to_numpy(), [False]])
    roll = nxt if venue.monthly_roll else np.zeros(len(p.dates), dtype=bool)
    return rate_prev, nights, roll


def book_path(p: AssetPath, costs: PointCosts, venue: Venue, rates: Rates) -> pd.DataFrame:
    """The market's daily frame under `venue`: units and P&L parts, long and short legs, window days only."""
    if venue.long_only and bool((p.b < 0).any()):
        raise ValueError(f"venue {venue.name} is long/flat only: {p.symbol} goes short")
    rate_prev, nights, roll = _rates_nights(p, rates, venue)
    kw = dict(x=p.x, stop_open=p.stop_open, stop_day=p.stop_day, costs=costs, venue=venue, rate_prev=rate_prev,
              nights=nights, roll=roll)
    g, c, f = _book(p, p.a, p.b, p.held, **kw)       # type: ignore[arg-type]
    legs = {}
    for name, part in (("long", np.maximum), ("short", np.minimum)):
        lg, lc, lf = _book(p, part(p.a, 0.0), part(p.b, 0.0), part(p.held, 0.0), **kw)   # type: ignore[arg-type]
        legs[name] = lg - lc + lf
    df = pd.DataFrame({"units_prev": p.a, "units": p.b, "held": p.held, "gross": g, "cost": c, "financing": f,
                       "net": g - c + f, "long": legs["long"], "short": legs["short"],
                       "expo": p.b * p.open, "ret": p.close / np.concatenate([[np.nan], p.close[:-1]]) - 1.0},
                      index=p.dates)
    return df.loc[p.in_window]


def _null_nets(p: AssetPath, costs: PointCosts, venue: Venue, rates: Rates, *, kind: str, draws: int,
               seed: int) -> np.ndarray:
    """Window net totals of `draws` null books of one market: episode signs flipped (kind "sign") or episodes moved
    to random starts in the window with their |e| path (kind "exposure"); re-costed and re-financed."""
    rng = np.random.default_rng(seed)
    w0 = max(int(np.argmax(p.in_window)) - 1, 0)
    sl = slice(w0, len(p.dates))
    sub = p.model_copy(update={k: getattr(p, k)[sl] for k in ("open", "close", "a", "b", "held", "x", "stop_open",
                                                              "stop_day", "in_window")}
                       | {"dates": p.dates[sl], "close_utc": p.close_utc[sl]})
    n = len(sub.dates)
    ep = stats.episodes(sub.b, sub.held)
    n_ep = int(ep.max()) + 1 if (ep >= 0).any() else 0
    rate_prev, nights, roll = _rates_nights(sub, rates, venue)
    if kind == "sign":
        signs = rng.choice(np.array([-1.0, 1.0]), size=(draws, max(n_ep, 1)))
        m = np.where(ep >= 0, signs[:, np.maximum(ep, 0)], 0.0)
        b, held, x, so, sd = sub.b * m, sub.held * m, sub.x, sub.stop_open, sub.stop_day
    else:
        win = np.flatnonzero(sub.in_window)
        last = int(win[-1])
        b = np.zeros((draws, n))
        for e in range(n_ep):
            days = np.flatnonzero(ep == e)
            path = np.abs(sub.b[days]) * sub.open[days]
            span = len(days)
            lo_, hi_ = int(win[0]) + 1, last - span + 1          # the first window day holds no position
            if hi_ < lo_:
                continue
            starts = rng.integers(lo_, hi_ + 1, size=draws)
            cols = starts[:, None] + np.arange(span)[None, :]
            np.add.at(b, (np.repeat(np.arange(draws), span), cols.ravel()), (path[None, :] / sub.open[cols]).ravel())
        held = b.copy()
        held[:, last] = 0.0
        x, so, sd = sub.close, np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    a = np.concatenate([np.zeros((held.shape[0], 1)), held[:, :-1]], axis=1)
    g, c, f = _book(sub, a, b, held, x=x, stop_open=so, stop_day=sd, costs=costs, venue=venue, rate_prev=rate_prev,
                    nights=nights, roll=roll)
    return np.asarray(((g - c + f)[:, sub.in_window]).sum(axis=1))


# ---------------------------------------------------------------- run
def run(spec: PositionSpec, panel: Panel, window: Window, *, cfg: EngineConfig, costs: Mapping[str, PointCosts],
        venues: Mapping[str, Venue], rates: Rates | None = None) -> PositionResult:
    """One spec on one window under each venue. Refuses (HoldoutViolation) any frame holding a holdout date, hands the
    signal only rows up to the window's last day, and checks the signal point in time before booking anything."""
    rates = rates or Rates()
    for k, v in panel.items():
        assert_before_holdout(v.index, what=f"{k} rows")
        if "close_utc" in v.columns:
            assert_before_holdout(v["close_utc"], what=f"{k} closes")
    view = {k: v.loc[v.index <= window.last] for k, v in panel.items()}
    for s in spec.symbols:
        if s not in view or not (view[s].index >= window.first).any():
            raise ValueError(f"{spec.id}: no {s} bars inside window {window.name}")
    if cfg.pit_cuts:
        d0 = view[spec.symbols[0]].index
        d0 = d0[d0 >= window.first]
        cuts = [d0[int(len(d0) * (i + 1) / (cfg.pit_cuts + 1))] for i in range(cfg.pit_cuts)]
        assert_point_in_time(spec, view, cuts)
    tg = _book_level(spec, targets(spec, view, cfg), view, cfg)
    stop = spec.stop_atr if spec.stop_atr is not None else cfg.stop_atr
    band = spec.band if spec.band is not None else cfg.band
    paths = {s: simulate(tg[s], view[s], cfg, stop_atr=stop, band=band, window=window, symbol=s) for s in spec.symbols}
    bench = PositionSpec(id=f"{spec.id}-buy-hold", symbols=spec.symbols, signal=constant(1.0), sizing=spec.sizing)
    btg = targets(bench, view, cfg)
    bpaths = {s: simulate(btg[s], view[s], cfg, stop_atr=None, band=band, window=window, symbol=s)
              for s in spec.symbols}
    null_kind = "exposure" if spec.long_only else "sign"
    out: dict[str, VenueResult] = {}
    for vname, venue in venues.items():
        frames = {s: book_path(p, costs[s], venue, rates) for s, p in paths.items()}
        bh = {s: book_path(p, costs[s], venue.model_copy(update={"long_only": False}), rates)["net"]
              for s, p in bpaths.items()}
        nulls = None
        if venue.gates:
            nulls = {s: _null_nets(p, costs[s], venue, rates, kind=null_kind, draws=cfg.null_draws,
                                   seed=cfg.null_seed + i) for i, (s, p) in enumerate(paths.items())}
        assets = {s: stats.summarise(f, cfg=cfg, null=None if nulls is None else nulls[s], null_kind=null_kind,
                                     buy_hold=bh[s]) for s, f in frames.items()}
        book_daily = pd.concat([f[["gross", "cost", "financing", "net", "long", "short"]] for f in frames.values()],
                               axis=0).groupby(level=0).sum().sort_index()
        book_frame = book_daily.assign(
            units=pd.concat([f["units"].ne(0).astype(int) for f in frames.values()]).groupby(level=0).max())
        bh_book = pd.concat(bh.values()).groupby(level=0).sum()
        book = stats.summarise_book(book_frame, frames, cfg=cfg,
                                    null=None if nulls is None else np.sum(list(nulls.values()), axis=0),
                                    null_kind=null_kind, buy_hold=bh_book)
        out[vname] = VenueResult(venue=venue.name, gates=venue.gates, book=book, assets=assets, daily=book_daily,
                                 assets_daily=frames)
    return PositionResult(spec_id=spec.id, window=window.name, venues=out,
                          rate_source="constant" if rates.uses_constant else "DTB3")

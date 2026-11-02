"""Synthetic inputs for the position-engine tests: 1m store bars, daily mid bars and simple signals."""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from goldbot.research.position.costs import PointCosts
from goldbot.research.position.engine import EngineConfig
from goldbot.research.position.financing import Venue, venue_a
from goldbot.research.position.signals import Panel

ZERO_COSTS = PointCosts(rt_bps=0.0, overshoot_bps=0.0)
NO_FINANCING = venue_a(markup_long_pct=0.0, markup_short_pct=0.0)
UNIT = EngineConfig(target_vol=None, band=0.5, stop_atr=None, warmup_days=0, pit_cuts=0)   # a 0/1 hold never rebalances


def minutes_1m(n_days: int, *, start: str = "2012-03-05", step_min: int = 10, seed: int = 3, price0: float = 1600.0,
               half_spread: float = 0.0) -> pd.DataFrame:
    """Weekday 1m-schema bars every `step_min` minutes from 21:00 UTC the evening before each day, CONTINUOUS (each
    bar opens at the previous close, also across weekends), so the next day's first open equals the day's close."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days)
    stamps = []
    for d in days:
        t0 = pd.Timestamp(d, tz="UTC") - pd.Timedelta(hours=3)            # 21:00 UTC the day before
        stamps.append(pd.date_range(t0, t0 + pd.Timedelta(hours=24), freq=f"{step_min}min", inclusive="left"))
    ts = stamps[0].append(stamps[1:])
    n = len(ts)
    close = price0 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
    open_ = np.concatenate([[price0], close[:-1]])
    hi = np.maximum(open_, close) * (1 + 0.0003)
    lo = np.minimum(open_, close) * (1 - 0.0003)
    h = half_spread
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
                         "bid_open": open_ - h, "bid_high": hi - h, "bid_low": lo - h, "bid_close": close - h,
                         "ask_open": open_ + h, "ask_high": hi + h, "ask_low": lo + h, "ask_close": close + h,
                         "tick_count": 1.0, "spread_mean": 2 * h, "spread_max": 2 * h})


def daily_from_returns(r: np.ndarray, *, start: str = "2004-01-05", price0: float = 1000.0,
                       range_frac: float = 0.002) -> pd.DataFrame:
    """Daily mid bars on business days with close-to-close returns `r`, each opening at the previous close."""
    dates = pd.bdate_range(start, periods=len(r))
    close = price0 * np.cumprod(1 + np.asarray(r, dtype=float))
    open_ = np.concatenate([[price0], close[:-1]])
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + range_frac),
                         "low": np.minimum(open_, close) * (1 - range_frac), "close": close,
                         "close_utc": (dates + pd.Timedelta(hours=21)).tz_localize("UTC")},
                        index=pd.DatetimeIndex(dates, name="date"))


def flat_daily(n: int, *, start: str = "2012-01-02", price: float = 1000.0) -> pd.DataFrame:
    return daily_from_returns(np.zeros(n), start=start, price0=price, range_frac=0.0)


def series_signal(values: pd.Series) -> Callable[[str, Panel], pd.Series]:
    """A signal that returns fixed values (reindexed to the panel's dates); point in time by construction."""
    def fn(symbol: str, panel: Panel) -> pd.Series:
        return values.reindex(panel[symbol].index)
    return fn


def momentum_sign(lookback: int = 1) -> Callable[[str, Panel], pd.Series]:
    def fn(symbol: str, panel: Panel) -> pd.Series:
        c = panel[symbol]["close"]
        return np.sign(c / c.shift(lookback) - 1.0)
    return fn


def lookahead(symbol: str, panel: Panel) -> pd.Series:
    c = panel[symbol]["close"]
    return np.sign(c.shift(-1) / c - 1.0)          # tomorrow's return: must be refused


def venue_flat(rt_bps: float = 0.0) -> Venue:
    return venue_a(markup_long_pct=0.0, markup_short_pct=0.0).model_copy(update={"extra_rt_bps": rt_bps})

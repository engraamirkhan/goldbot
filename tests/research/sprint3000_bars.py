"""Synthetic 1m tapes for the sprint-3000 engine tests: a seeded random walk on weekday minutes (the FX week, Sunday
22:00 to Friday 21:00 UTC), open = the previous close so no minute gaps through a level, and hand-built bar fixtures."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.research.sprint3000.market import MIN, Bars, Market, Window


def regime(t_ns: np.ndarray, t0_ns: int, regime_days: float) -> np.ndarray:
    """+1 / -1 alternating every `regime_days` from t0: the planted drift's sign."""
    return np.where(((np.asarray(t_ns, np.int64) - t0_ns) // int(regime_days * 86_400e9)) % 2 == 0, 1, -1)


def synthetic_minutes(start: str = "2012-01-02", days: int = 60, seed: int = 7, vol: float = 0.12,
                      drift: float = 0.0, regime_days: float = 0.0) -> pd.DataFrame:
    """Columns t (epoch ns), o, h, l, c of a mid-priced random walk around 1600, weekend minutes removed. With
    `regime_days` the per-minute `drift` flips sign every that many days (zero net drift over whole cycles)."""
    t0 = pd.Timestamp(start, tz="UTC").value
    t = t0 + np.arange(days * 1440, dtype=np.int64) * MIN
    ts = pd.DatetimeIndex(pd.to_datetime(t, utc=True))
    wd, hr = ts.weekday.to_numpy(), ts.hour.to_numpy()
    open_ = ~((wd == 5) | ((wd == 4) & (hr >= 21)) | ((wd == 6) & (hr < 22)))
    t = t[open_]
    rng = np.random.default_rng(seed)
    step = rng.standard_t(4, len(t)) * vol + drift * (regime(t, t0, regime_days) if regime_days else 1)
    c = 1600 + np.cumsum(step)
    o = np.r_[c[0] - step[0], c[:-1]]
    wick = np.abs(rng.normal(0, vol / 2, (2, len(t))))
    h = np.maximum(o, c) + wick[0]
    lo = np.minimum(o, c) - wick[1]
    return pd.DataFrame({"t": t, "o": o, "h": h, "l": lo, "c": c})


def window(start: str, end: str, dec_from: str | None = None) -> Window:
    return Window(name="synthetic", data_from=pd.Timestamp(start, tz="UTC"),
                  dec_from=pd.Timestamp(dec_from or start, tz="UTC"), end=pd.Timestamp(end, tz="UTC"))


def synthetic_market(start: str = "2012-01-02", days: int = 60, seed: int = 7, **kw: Any) -> Market:
    m = synthetic_minutes(start, days, seed, **kw)
    end = pd.Timestamp(int(m.t.iloc[-1]) + MIN, tz="UTC").ceil("D")
    return Market.from_minutes(m, window(start, str(end.date())))


def hand_bars(c: list[float], h: list[float] | None = None, lo: list[float] | None = None,
              o: list[float] | None = None, tf: str = "1h") -> Bars:
    """Bars from closes (highs/lows default to close +- 0.2, opens to the previous close), hourly from a Monday."""
    cc = np.asarray(c, dtype=float)
    oo = np.asarray(o, dtype=float) if o is not None else np.r_[cc[0], cc[:-1]]
    hh = np.asarray(h, dtype=float) if h is not None else np.maximum(oo, cc) + 0.2
    ll = np.asarray(lo, dtype=float) if lo is not None else np.minimum(oo, cc) - 0.2
    t0 = pd.Timestamp("2015-01-05 01:00", tz="UTC").value
    dec = t0 + np.arange(1, len(cc) + 1, dtype=np.int64) * 60 * MIN
    return Bars.of(tf, dec, oo, hh, ll, cc)

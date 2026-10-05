"""Synthetic gold-like ticks for tests and dry runs. Never used for training real models."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.timeutil import epoch_ns


def synthetic_ticks(start: str, end: str, *, seed: int = 7, ticks_per_minute: int = 4, price0: float = 2400.0,
                    sessions: SessionTable = DEFAULT_SESSIONS) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    minutes = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    minutes = minutes[sessions.is_open(minutes)]
    n = len(minutes) * ticks_per_minute
    offsets = np.sort(rng.integers(0, 60_000, size=n)).reshape(len(minutes), ticks_per_minute)
    base_ns = np.repeat(epoch_ns(minutes), ticks_per_minute)
    ts = pd.to_datetime(base_ns + offsets.ravel().astype(np.int64) * 1_000_000, unit="ns", utc=True)
    # regime-switching volatility so features have something to see
    vol = np.where(rng.random(n) < 0.02, 0.0012, 0.00025)
    vol = pd.Series(vol).rolling(400, min_periods=1).max().to_numpy()
    ret = rng.normal(0, 1, n) * vol + 0.00001 * np.sin(np.arange(n) / 5000)
    mid = price0 * np.exp(np.cumsum(ret))
    hour = pd.DatetimeIndex(ts).hour
    spread = np.where((hour >= 22) | (hour < 1), 0.60, 0.22) + rng.random(n) * 0.05
    return pd.DataFrame({"ts_utc": pd.DatetimeIndex(ts), "bid": mid - spread / 2, "ask": mid + spread / 2})

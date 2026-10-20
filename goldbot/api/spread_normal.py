"""Backlog 93: the approval card's usual spread ("Spread $0.22 now · usual $0.18 (1.2x)").

"`spread_normal_points` (median spread for this hour-of-week over the last 20 trading days from the engine's spread
history; null when unknown)". The history is the broker's own tick record (goldbot/data/ticks.py: every tick of each
closed server day, not the engine's sample of them). Definition, for quant-reviewer:

* the last LOOKBACK_DAYS recorded server days before the proposal's UTC date (never today's: it is not closed);
* within them, each UTC day whose weekday and hour match the proposal's is one window (about 4 in 20 trading days);
* a window counts with at least MIN_TICKS ticks; its value is the median of ask - bid over its ticks;
* the usual spread is the median of those window medians (each day once, so one news day cannot set the normal),
  in points of 0.01 $/oz, the engine's `spread_points`; null with fewer than MIN_WINDOWS windows or no record.

Read-only and cached per account and hour (the record changes once a day), so polling the card does not re-read it.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data import ticks

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 20
MIN_WINDOWS = 2
MIN_TICKS = 30
POINT = 0.01                     # $/oz per point, as runner.py `spread_points`


def normal_spread_points(state_dir: str | Path, account: str, at: pd.Timestamp) -> float | None:
    """The usual spread for `at`'s UTC hour-of-week (module docstring); None when it cannot be known."""
    at = pd.Timestamp(at).tz_convert("UTC") if pd.Timestamp(at).tzinfo else pd.Timestamp(at, tz="UTC")
    try:
        days = tuple(d for d in ticks.recorded_days(account, state_dir=state_dir) if d < at.date())[-LOOKBACK_DAYS:]
    except (ValueError, OSError):        # not an account label, or an unreadable index
        return None
    return _normal(str(state_dir), account, at.floor("h"), days)


@lru_cache(maxsize=64)
def _normal(state_dir: str, account: str, hour: pd.Timestamp, days: tuple) -> float | None:
    wanted = {(hour - timedelta(days=7 * k)).date() for k in range(1, LOOKBACK_DAYS // 5 + 2)}
    # a server day can hold the start or end of a neighbouring UTC day: read the files next to each matching date
    near = [d for d in days if any(abs((d - w).days) <= 1 for w in wanted)]
    medians: dict[object, float] = {}
    for d in near:
        try:
            t = ticks.load_day(account, d, state_dir=state_dir)
        except (ValueError, OSError) as exc:
            log.warning("tick record %s %s skipped for the usual spread: %s", account, d, type(exc).__name__)
            continue
        ts = t["ts_utc"]
        hit = t[(ts.dt.dayofweek == hour.dayofweek) & (ts.dt.hour == hour.hour) & (ts < hour.normalize())]
        for day, g in hit.groupby(hit["ts_utc"].dt.date):
            if len(g) >= MIN_TICKS and day not in medians:
                medians[day] = float(np.median(g["ask"] - g["bid"]))
    if len(medians) < MIN_WINDOWS:
        return None
    return round(float(np.median(list(medians.values()))) / POINT, 2)

"""The engine's current key zones (owner 2026-11-02: "identify key levels, support and resistance on all time
frames"), published in engine_<account>.json for the dashboard's price chart.

Design rules:
- Display only. Nothing here sizes, gates, places, moves or closes an order: no RiskGate input reads it, and a failure
  is a logged warning that leaves the last published zones (never a failed bar close).
- The zones are `goldbot.features.key_levels.zones_asof` on the engine's own completed 1m bars at the bar close (the
  same point-in-time definition research uses), recomputed only when a 15m bar has closed (the book's own clock).
- The engine holds about 62 trading days of 1m bars (EngineConfig.max_bars_in_memory), so swings older than that
  (most 1w swings) and the trailing-250-day strength pool are truncated live; `history_days` says how much history
  the zones were built on, so the display never claims more than it saw.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.features.key_levels import GRID_TF, KeyZone, zones_asof

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

NEAREST = 3                       # zones published on each side of the price
_STEP = pd.Timedelta(GRID_TF)


def _zone(z: KeyZone) -> dict[str, Any]:
    return {"price": round(z.price, 2), "lo": round(z.lo, 2), "hi": round(z.hi, 2), "strength": round(z.strength, 3),
            "strength_pct": None if z.strength_pct is None else round(z.strength_pct, 1), "tfs": list(z.tfs),
            "touches": z.touches, "role": z.role, "flipped": z.flipped}


def build(complete: pd.DataFrame, close_ts: pd.Timestamp) -> dict[str, Any] | None:
    """The published zones at `close_ts` from completed 1m bars (BAR_COLUMNS); None without bars."""
    if complete.empty:
        return None
    z = zones_asof(complete, close_ts)
    if z.as_of is None:
        return None
    above, below = z.nearest(NEAREST)
    first = pd.Timestamp(complete["ts_utc"].iloc[0])
    return {"as_of": z.as_of.isoformat(), "price": None if z.price is None else round(z.price, 2),
            "atr_1d": None if z.atr_1d is None else round(z.atr_1d, 2),
            "history_days": round((close_ts - first).total_seconds() / 86_400, 1),
            "above": [_zone(x) for x in above], "below": [_zone(x) for x in below]}


def update(eng: Engine, complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    """At a bar close: rebuild the zones when a 15m bar has closed since the last build. Never raises."""
    key = close_ts.floor(_STEP)
    if eng.key_zones_at == key:
        return
    try:
        eng.key_zones = build(complete, close_ts)
        eng.key_zones_at = key
    except Exception as exc:          # noqa: BLE001 - display only: a bar close never fails on it
        log.warning("key zones not updated: %s", type(exc).__name__)


def published(eng: Engine) -> dict[str, Any]:
    """engine_<account>.json: `key_zones` (None until the first build)."""
    return {"key_zones": eng.key_zones}

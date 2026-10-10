"""Regime allocator, rule-table version (first three months; the learned allocator replaces it once
~300 out-of-fold records exist).

Weights per design: trend 1.0 when ADX(14,1h) > 25 else 0; mean-reversion 1.0 when ADX < 18 else 0;
breakout 1.0 when 1h ATR is in its bottom quartile over 60 days else 0, with mean-reversion + breakout
capped at a summed weight of 1.0; session-open always 0.75; everything 0 for 30 minutes either side of a tier-1
event (deliberately stricter than the RiskGate's -15/+30 min entry block; changing it is the owner's decision).
Also exposes the population view: each agent's weight = allocator family weight x agent fitness share.
"""
from __future__ import annotations

import pandas as pd

from goldbot.base import Record


def tier1_minutes(events: pd.DataFrame, now: pd.Timestamp) -> tuple[float | None, float | None]:
    """(minutes to the next tier-1 event at or after `now`, minutes since the last one before it) from a calendar
    frame (ts_utc, tier), as the engine's news blackout reads it; None where there is no such event."""
    if events.empty or "tier" not in events.columns:
        return None, None
    ts = pd.DatetimeIndex(pd.to_datetime(events.loc[events["tier"] == 1, "ts_utc"], utc=True))
    ahead, behind = ts[ts >= now], ts[ts < now]
    to_next = (ahead.min() - now).total_seconds() / 60 if len(ahead) else None
    since = (now - behind.max()).total_seconds() / 60 if len(behind) else None
    return to_next, since


class Regime(Record):
    adx_1h: float
    atr_1h_quartile: int        # 0 bottom .. 3 top, over 60 days
    vol_tercile: int            # 0 low, 1 mid, 2 high
    minutes_to_tier1: float | None
    minutes_since_tier1: float | None


class RuleAllocator:
    name = "rule-table-v1"

    def __init__(self, blackout_before: int = 30, blackout_after: int = 30):
        self.before, self.after = blackout_before, blackout_after

    def in_blackout(self, r: Regime) -> bool:
        return (r.minutes_to_tier1 is not None and r.minutes_to_tier1 <= self.before) or \
               (r.minutes_since_tier1 is not None and r.minutes_since_tier1 <= self.after)

    def weights(self, r: Regime) -> dict[str, float]:
        if self.in_blackout(r):
            return {"trend": 0.0, "mean_reversion": 0.0, "breakout": 0.0, "session_open": 0.0}
        w = {
            "trend": 1.0 if r.adx_1h > 25 else 0.0,
            "mean_reversion": 1.0 if r.adx_1h < 18 else 0.0,
            "breakout": 1.0 if r.atr_1h_quartile == 0 else 0.0,
            "session_open": 0.75,
        }
        s = w["mean_reversion"] + w["breakout"]
        if s > 1.0:
            w["mean_reversion"], w["breakout"] = w["mean_reversion"] / s, w["breakout"] / s
        return w

    @staticmethod
    def agent_weights(family_weights: dict[str, float], fitness: dict[str, tuple[str, float]]) -> dict[str, float]:
        """fitness: agent_id -> (family, fitness >= 0). Within a family, capital splits by fitness share."""
        totals: dict[str, float] = {}
        for _, (fam, f) in fitness.items():
            totals[fam] = totals.get(fam, 0.0) + max(f, 0.0)
        out = {}
        for aid, (fam, f) in fitness.items():
            share = (max(f, 0.0) / totals[fam]) if totals.get(fam, 0.0) > 0 else 0.0
            out[aid] = family_weights.get(fam, 0.0) * share
        return out

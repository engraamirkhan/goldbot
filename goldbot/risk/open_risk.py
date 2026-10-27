"""Remaining stop risk of an open position (BACKLOG 19 heat cap, BACKLOG 35 daily projection).

Design rule (BACKLOG 35, trading-safety review MEDIUM-4): "the projection adds, per open position, max(0, loss from the
current price to its stop) x lots x contract x fx". The current price is the one the position would close at (the bid
for a long, the ask for a short), so the figure is what equity can still lose before the stop fills; equity already
holds the open P&L up to now. fx is the account currency per USD of XAUUSD P&L (`sizing.account_fx`).

Fail closed: a position without a stop (an unbounded loss), or one whose conversion, lots or contract is not a usable
number, has an unknown risk (None). The engine then publishes the account's open risk as unknown, and RiskGate refuses
entries while the heat cap or the projection is on. Exits never read any of this.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd


def _usable(x: float | None) -> bool:
    return x is not None and math.isfinite(x) and x > 0


def remaining_stop_risk(*, side: int, lots: float, sl: float | None, bid: float, ask: float, contract: float,
                        fx: float | None) -> float | None:
    """Account currency the position loses between the current close price and its stop; 0 when the price is at or
    through the stop (the conversion is then not needed); None when unknown (no stop, or unusable terms)."""
    if not (_usable(sl) and _usable(lots) and _usable(contract)) or side not in (1, -1):
        return None
    assert sl is not None
    px = bid if side > 0 else ask
    if not math.isfinite(px):
        return None
    distance = max(0.0, side * (px - sl))
    if distance == 0.0:
        return 0.0
    if not _usable(fx):
        return None
    assert fx is not None
    return distance * lots * contract * fx


def published_fx(open_fx: Mapping[int, tuple[pd.Timestamp, float | None]]) -> dict[str, Any]:
    """Item 128 (docs/design/trades-ux-2026-10-27.md A1): the newest account-currency-per-USD rate the open-risk refresh
    read from the broker (`Engine._open_risk_fx`, per side, once a minute) and the minute it was read, for
    engine_<account>.json. Reporting only: the dashboard and `goldbot run positions` convert open P&L and risk with it
    (account_view.usd_to_account) and show a rate older than 120 s as stale; no gate, size or exit reads it. None
    while no position has needed a rate (the dashboard then says "no rate", never a guess)."""
    known = [(at, fx) for at, fx in open_fx.values() if fx is not None and math.isfinite(fx) and fx > 0]
    if not known:
        return {"usd_to_account": None, "usd_to_account_utc": None}
    at, fx = max(known, key=lambda x: x[0])
    return {"usd_to_account": round(fx, 8), "usd_to_account_utc": pd.Timestamp(at).isoformat()}

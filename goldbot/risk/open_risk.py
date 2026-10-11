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

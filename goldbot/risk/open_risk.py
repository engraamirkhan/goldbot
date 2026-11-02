"""Remaining stop risk of an open position (BACKLOG 19 heat cap, BACKLOG 35 daily projection).

Design rule (BACKLOG 35, trading-safety review MEDIUM-4): "the projection adds, per open position, max(0, loss from the
current price to its stop) x lots x contract x fx". The current price is the one the position would close at (the bid
for a long, the ask for a short), so the figure is what equity can still lose before the stop fills; equity already
holds the open P&L up to now. fx is the account currency per USD of XAUUSD P&L (`sizing.account_fx`).

Fail closed: a position without a stop (an unbounded loss), or one whose conversion, lots or contract is not a usable
number, has an unknown risk (None). The engine then publishes the account's open risk as unknown, and RiskGate refuses
entries while the heat cap or the projection is on. Exits never read any of this.

Multi-instrument plan 2026-11-02 ("account-wide heat"; "notional per correlation group"): `account_open_risk` and
`group_exposure` value every position of the account, whatever its symbol, from that symbol's own quote, contract and
conversion (`SymbolQuote`; fx = account currency per unit of the symbol's P&L currency). A position on a symbol the
engine has no usable quote for makes the account figure unknown, never zero.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

import pandas as pd

from goldbot.base import FrozenRecord


class SymbolQuote(FrozenRecord):
    """One symbol's current close prices and terms, as the engine's SymbolBook last read them: `contract` units per
    lot, `fx` the account currency per unit of the symbol's P&L currency (None: unknown), `group` its correlation
    group (goldbot/instruments.py)."""
    bid: float
    ask: float
    contract: float
    fx: float | None
    group: str


class _Position(Protocol):
    @property
    def position_id(self) -> int: ...
    @property
    def symbol(self) -> str: ...
    @property
    def side(self) -> int: ...
    @property
    def lots(self) -> float: ...
    @property
    def sl(self) -> float | None: ...


def account_open_risk(positions: Iterable[_Position],
                      quotes: Mapping[str, SymbolQuote]) -> tuple[float | None, list[dict[str, Any]]]:
    """(what every open position of the account can still lose down to its stop, account currency; one row per
    position). Each position is valued on its own symbol's quote (`remaining_stop_risk`); one without a quote, a stop
    or a usable conversion makes the total None (unknown: RiskGate refuses while the heat cap or the projection is
    on). No positions: 0."""
    total: float | None = 0.0
    rows: list[dict[str, Any]] = []
    for p in positions:
        q = quotes.get(p.symbol)
        risk = None if q is None else remaining_stop_risk(side=p.side, lots=p.lots, sl=p.sl, bid=q.bid, ask=q.ask,
                                                          contract=q.contract, fx=q.fx)
        rows.append({"position_id": p.position_id, "symbol": p.symbol, "side": p.side, "lots": p.lots, "sl": p.sl,
                     "risk": round(risk, 2) if risk is not None else None})
        total = None if total is None or risk is None else total + risk
    return total, rows


def group_exposure(positions: Iterable[_Position],
                   quotes: Mapping[str, SymbolQuote]) -> tuple[dict[str, float] | None, float]:
    """(open notional per correlation group, account currency: lots x contract x mid x fx; the gold group's lots) for
    RiskGate's per-group cap (`AccountState.group_notional`, `open_lots`). A position the engine cannot value (no
    quote, no usable conversion, price or lots) makes the map None: the engine then sets `exposure_unknown` and
    entries are refused. No positions: ({}, 0.0)."""
    groups: dict[str, float] = {}
    gold_lots = 0.0
    for p in positions:
        q = quotes.get(p.symbol)
        mid = (q.bid + q.ask) / 2 if q is not None else math.nan
        if q is None or not (_usable(q.fx) and _usable(q.contract) and _usable(mid) and _usable(p.lots)):
            return None, 0.0
        assert q.fx is not None
        groups[q.group] = groups.get(q.group, 0.0) + p.lots * q.contract * mid * q.fx
        if q.group == "gold":
            gold_lots += p.lots
    return groups, gold_lots


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

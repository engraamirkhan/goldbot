"""The round-trip commission the planned risk and the cost count (ADR 0075, demo non-gold fill audit 2026-11-03); moved
out of runner.py to keep it under its line cap."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine


def usd_per_oz(eng: Engine, price: float | None = None) -> float:
    """The round-trip commission per unit of the active book's contract, in its price units (the Intent's
    `commission_usd_per_oz`; demo non-gold fill audit 2026-11-03: the planned risk includes it). The cost table's
    measured per-lot figure, else the broker's prior (`commission_per_lot_side_usd`) for the classes brokers
    charge by the lot (gold, other metals, FX); index, energy, bond and soft CFDs are priced in their spread, 0.
    Per lot both sides over the contract size, as `CostTable.round_trip_usd_per_oz`. USD on a USD quote; a USD-base
    pair quoted in another currency (USDJPY, USDCAD, USDCHF) is converted at its price (`price`, else the last
    quote's mid; none known: NaN, so the gate refuses `invalid:commission_usd_per_oz`). Another non-USD quote
    (XAUEUR, XAUAUD) keeps the USD figure: a few percent of a term that is ~1% of a 100 oz stop."""
    spec = eng.book.spec.instrument if eng.book.spec is not None else None
    table = eng._cost_table()
    if table is not None:
        side, contract = table.commission_per_lot_side_usd, table.contract_size
    elif spec is None or spec.asset_class in ("metal", "fx"):
        side, contract = eng.cfg.commission_per_lot_side_usd, eng.cfg.contract_size
    else:
        return 0.0
    usd = 2 * max(side, 0.0) / contract
    if spec is None or spec.currency_profit == "USD" or not spec.key.startswith("USD") or usd == 0.0:
        return usd
    if price is None:
        t = eng._last_tick
        price = (t.bid + t.ask) / 2 if t is not None else math.nan
    return usd * price if math.isfinite(price) and price > 0 else math.nan


def floor_cost(eng: Engine, cost: float, atr_usd: float, ex_spread: bool) -> float:
    """Demo non-gold fill audit 2026-11-03 ("count spread (and commission) in the cost": a book without a table read
    0.10 R against a spread of 0.52 R): the cost in ATR is never below the live spread (the active book's
    `spread_points` in its own `point`; left out for ex_spread) plus the round-trip commission (`usd_per_oz`), over
    `atr_usd`. Only ever raises the cost; an unknown spread (a book not refreshed yet) adds nothing, its entries are
    refused `spread_too_wide` and `stale_data` anyway."""
    if not (math.isfinite(atr_usd) and atr_usd > 0):
        return cost
    spread = eng.state.spread_points * eng.cfg.point
    live = spread if not ex_spread and math.isfinite(spread) and spread > 0 else 0.0
    return max(cost, (live + eng._commission_usd_per_oz()) / atr_usd)

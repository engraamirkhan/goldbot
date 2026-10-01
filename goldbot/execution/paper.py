"""Paper broker: same protocol, live or replayed ticks, fills modelled slightly worse than reality:
market fills at the touch plus half the last minute's spread standard deviation, stop/target fills with
20 points adverse slippage, real commission."""
from __future__ import annotations

import itertools
from collections import deque

import numpy as np
import pandas as pd

from goldbot.execution.broker import AccountInfo, OrderIntent, OrderResult, Position, SymbolInfo, Tick


class PaperBroker:
    name = "paper"

    def __init__(self, symbol: str = "XAUUSD", equity: float = 10_000.0, commission_per_lot_side: float = 3.5,
                 adverse_slip_points: float = 20.0, point: float = 0.01, contract: float = 100.0):
        self.symbol = symbol
        self._equity = equity
        self._balance = equity
        self.commission = commission_per_lot_side
        self.slip = adverse_slip_points * point
        self.point = point
        self.contract = contract
        self._tick: Tick | None = None
        self._spreads: deque[float] = deque(maxlen=240)
        self._positions: dict[int, Position] = {}
        self._deals: list[dict] = []
        self._ids = itertools.count(1)
        self._pending_ids: set[str] = set()

    # ------------------------------------------------------------------ market data
    def on_tick(self, tick: Tick) -> None:
        self._tick = tick
        self._spreads.append(tick.ask - tick.bid)
        self._check_exits(tick)

    def last_tick(self, symbol: str) -> Tick:
        assert self._tick is not None, "no tick yet"
        return self._tick

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(symbol, 2, self.point, self.contract, 0.01, 0.01, 50.0, 0.0, ["IOC"], True,
                          commission_per_lot_side=self.commission)

    def account(self) -> AccountInfo:
        float_pl = sum(p.profit for p in self._positions.values())
        return AccountInfo(0, self._balance + float_pl, self._balance, 0.0, self._balance + float_pl, 20, "USD", "paper")

    def get_bars(self, symbol, tf, n):  # bars come from the store in paper mode
        return pd.DataFrame()

    async def stream_ticks(self, symbol):  # pragma: no cover - driven by on_tick in tests
        raise NotImplementedError

    # ------------------------------------------------------------------ orders
    def place_order(self, intent: OrderIntent) -> OrderResult:
        if intent.client_order_id in self._pending_ids:
            return OrderResult(False, 10027, None, None, 0.0, None, "duplicate client_order_id")
        t = self._tick
        half_sd = float(np.std(self._spreads)) / 2 if len(self._spreads) > 5 else 0.0
        price = (t.ask + half_sd) if intent.side > 0 else (t.bid - half_sd)
        pid = next(self._ids)
        self._positions[pid] = Position(pid, intent.symbol, intent.side, intent.lots, price, intent.sl, intent.tp,
                                        intent.magic, intent.comment, t.ts_utc, 0.0)
        self._balance -= self.commission * intent.lots
        self._pending_ids.add(intent.client_order_id)
        self._deals.append({"ts_utc": t.ts_utc, "position_id": pid, "type": "entry", "price": price, "lots": intent.lots,
                            "comment": intent.comment, "client_order_id": intent.client_order_id})
        return OrderResult(True, 10009, pid, pid, intent.lots, price, "filled")

    def modify(self, position_id, sl, tp):
        p = self._positions.get(position_id)
        if p is None:
            return OrderResult(False, 10013, None, position_id, 0.0, None, "no such position")
        p.sl, p.tp = (sl if sl is not None else p.sl), (tp if tp is not None else p.tp)
        return OrderResult(True, 10009, None, position_id, p.lots, None, "modified")

    def close(self, position_id, lots=None):
        p = self._positions.get(position_id)
        if p is None:
            return OrderResult(False, 10013, None, position_id, 0.0, None, "no such position")
        t = self._tick
        price = t.bid if p.side > 0 else t.ask
        return self._close_at(p, price, "close")

    def _close_at(self, p: Position, price: float, reason: str) -> OrderResult:
        pnl = p.side * (price - p.open_price) * p.lots * self.contract - self.commission * p.lots
        self._balance += pnl
        del self._positions[p.position_id]
        self._deals.append({"ts_utc": self._tick.ts_utc, "position_id": p.position_id, "type": reason, "price": price,
                            "lots": p.lots, "pnl": pnl, "comment": p.comment})
        return OrderResult(True, 10009, None, p.position_id, p.lots, price, reason)

    def _check_exits(self, t: Tick) -> None:
        for p in list(self._positions.values()):
            p.profit = p.side * ((t.bid if p.side > 0 else t.ask) - p.open_price) * p.lots * self.contract
            if p.side > 0:
                if p.sl is not None and t.bid <= p.sl:
                    self._close_at(p, p.sl - self.slip, "stop")
                elif p.tp is not None and t.bid >= p.tp:
                    self._close_at(p, p.tp, "target")
            else:
                if p.sl is not None and t.ask >= p.sl:
                    self._close_at(p, p.sl + self.slip, "stop")
                elif p.tp is not None and t.ask <= p.tp:
                    self._close_at(p, p.tp, "target")

    def positions(self, magic_prefix=None):
        ps = list(self._positions.values())
        if magic_prefix is not None:
            ps = [p for p in ps if str(p.magic).startswith(str(magic_prefix))]
        return ps

    def deals_since(self, since_utc):
        d = pd.DataFrame(self._deals)
        return d[d["ts_utc"] >= since_utc] if not d.empty else d

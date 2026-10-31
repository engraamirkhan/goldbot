"""Paper broker: same protocol, live or replayed ticks, fills modelled slightly worse than reality:
market fills at the touch plus half the last minute's spread standard deviation, stop/target fills with
20 points adverse slippage, real commission. Deals carry MT5's money columns (gross `profit`, `commission` as a negative
amount on each side, `swap`) next to `pnl` (net of that deal's commission), so the engine reads both brokers alike.
Swap is not modelled, so `swap` is None (unknown), never 0.0: a paper trade's record is gross (finance GAP-05).

Symbols (BACKLOG 62): the engine's symbol is fed by `on_tick`; any other symbol only through `set_quote`, so a test
can hold a manual trade on another symbol for the kill switch to close. Each position is priced with its own symbol's
quote; a symbol with no quote cannot fill (its `last_tick` raises, so a close is refused and retried) and its stops
wait for a quote. As on MT5, `positions()` lists the engine's symbol only and `account_positions()` every symbol.
Every symbol uses this broker's contract size and point (paper simplification)."""
from __future__ import annotations

import fnmatch
import itertools
from collections import deque
from collections.abc import AsyncIterator

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, TradingSession, resolve_server_sessions
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
        self._ticks: dict[str, Tick] = {}          # the last quote per symbol (on_tick: self.symbol; set_quote: any)
        self._spreads: deque[float] = deque(maxlen=240)
        self._positions: dict[int, Position] = {}
        self._deals: list[dict] = []
        self._ids = itertools.count(1)
        self._pending_ids: set[str] = set()

    # ------------------------------------------------------------------ market data
    def on_tick(self, tick: Tick) -> None:
        self._ticks[self.symbol] = tick
        self._spreads.append(tick.ask - tick.bid)
        self._check_exits()

    def set_quote(self, symbol: str, tick: Tick | None) -> None:
        """The quote of another symbol (an explicit input; `on_tick` keys to the engine's symbol). None withdraws it:
        the symbol then has no live price, as when its market is closed."""
        if tick is None:
            self._ticks.pop(symbol, None)
            return
        self._ticks[symbol] = tick
        self._check_exits()

    def last_tick(self, symbol: str) -> Tick:
        tick = self._ticks.get(symbol)
        if tick is None:
            raise LookupError(f"no quote for {symbol}")
        return tick

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(name=symbol, digits=2, point=self.point, contract_size=self.contract, volume_min=0.01, volume_step=0.01, volume_max=50.0, stops_level_points=0.0, filling_modes=["IOC"], trade_allowed=True,
                          commission_per_lot_side=self.commission)

    # ADR 0018: the balance is this simulator's own ledger, so a read never glitches and a rise in the high-water
    # mark needs no confirmation (the engine looks it up on the class)
    balance_exact = True

    def account(self) -> AccountInfo:
        float_pl = sum(p.profit for p in self._positions.values())
        return AccountInfo(login=0, equity=self._balance + float_pl, balance=self._balance, margin=0.0, margin_free=self._balance + float_pl, leverage=20, currency="USD", server="paper")

    def margin_required(self, symbol: str, side: int, lots: float, price: float) -> float | None:
        """Paper margin at the FCA retail cap for gold, 1:20 (the paper account's leverage)."""
        return lots * self.contract * price / 20.0

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        """The paper account is in USD and XAUUSD P&L is USD: distance x contract (conversion factor 1)."""
        return abs(distance) * self.contract

    def symbols_get(self, pattern: str = "*") -> list[str]:
        """The paper terminal lists its one symbol (ADR 0062 symbol check)."""
        return [self.symbol] if fnmatch.fnmatchcase(self.symbol, pattern) else []

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        """The static session table's hours for the week from the paper clock's server date (last tick, else now)."""
        own = self._ticks.get(self.symbol)
        now = own.ts_utc if own is not None else pd.Timestamp.now(tz="UTC")
        tz = DEFAULT_SESSIONS.server_tz
        return resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), tz, now.tz_convert(tz).date())

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:  # bars come from the store in paper mode
        return pd.DataFrame()

    def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]:  # pragma: no cover - driven by on_tick in tests
        raise NotImplementedError

    # ------------------------------------------------------------------ orders
    def place_order(self, intent: OrderIntent) -> OrderResult:
        if intent.client_order_id in self._pending_ids:
            return OrderResult(ok=False, retcode=10027, order_id=None, position_id=None, filled_lots=0.0, price=None, message="duplicate client_order_id")
        t = self.last_tick(intent.symbol)
        half_sd = float(np.std(self._spreads)) / 2 if len(self._spreads) > 5 else 0.0
        price = (t.ask + half_sd) if intent.side > 0 else (t.bid - half_sd)
        pid = next(self._ids)
        self._positions[pid] = Position(position_id=pid, symbol=intent.symbol, side=intent.side, lots=intent.lots, open_price=price, sl=intent.sl, tp=intent.tp,
                                        magic=intent.magic, comment=intent.comment, open_time_utc=t.ts_utc, profit=0.0)
        self._balance -= self.commission * intent.lots
        self._pending_ids.add(intent.client_order_id)
        self._deals.append({"ts_utc": t.ts_utc, "position_id": pid, "type": "entry", "price": price, "lots": intent.lots,
                            "comment": intent.comment, "client_order_id": intent.client_order_id,
                            "profit": 0.0, "commission": -self.commission * intent.lots, "swap": None})
        return OrderResult(ok=True, retcode=10009, order_id=pid, position_id=pid, filled_lots=intent.lots, price=price, message="filled")

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        p = self._positions.get(position_id)
        if p is None:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=position_id, filled_lots=0.0, price=None, message="no such position")
        p.sl, p.tp = (sl if sl is not None else p.sl), (tp if tp is not None else p.tp)
        return OrderResult(ok=True, retcode=10009, order_id=None, position_id=position_id, filled_lots=p.lots, price=None, message="modified")

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        p = self._positions.get(position_id)
        if p is None:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=position_id, filled_lots=0.0, price=None, message="no such position")
        t = self.last_tick(p.symbol)
        price = t.bid if p.side > 0 else t.ask
        if lots is not None and lots < p.lots - 1e-9:          # partial close: the rest stays open with its SL/TP
            gross = p.side * (price - p.open_price) * lots * self.contract
            pnl = gross - self.commission * lots
            self._balance += pnl
            p.lots = round(p.lots - lots, 8)
            self._deals.append({"ts_utc": t.ts_utc, "position_id": p.position_id, "type": "partial", "price": price,
                                "lots": lots, "pnl": pnl, "comment": p.comment, "profit": gross,
                                "commission": -self.commission * lots, "swap": None})
            return OrderResult(ok=True, retcode=10009, order_id=None, position_id=p.position_id, filled_lots=lots,
                               price=price, message="partial")
        return self._close_at(p, price, "close")

    def _close_at(self, p: Position, price: float, reason: str) -> OrderResult:
        at = self.last_tick(p.symbol).ts_utc           # read before any state change: a missing quote changes nothing
        gross = p.side * (price - p.open_price) * p.lots * self.contract
        pnl = gross - self.commission * p.lots
        self._balance += pnl
        del self._positions[p.position_id]
        self._deals.append({"ts_utc": at, "position_id": p.position_id, "type": reason, "price": price,
                            "lots": p.lots, "pnl": pnl, "comment": p.comment, "profit": gross,
                            "commission": -self.commission * p.lots, "swap": None})
        return OrderResult(ok=True, retcode=10009, order_id=None, position_id=p.position_id, filled_lots=p.lots, price=price, message=reason)

    def _check_exits(self) -> None:
        """Mark and stop/target-check every position against its own symbol's quote; a symbol without one waits."""
        for p in list(self._positions.values()):
            t = self._ticks.get(p.symbol)
            if t is None:
                continue
            p.profit = p.side * ((t.bid if p.side > 0 else t.ask) - p.open_price) * p.lots * self.contract
            if p.side > 0:
                if p.sl is not None and t.bid <= p.sl:      # a gap through the stop fills at the market, not the stop
                    self._close_at(p, min(p.sl, t.bid) - self.slip, "stop")
                elif p.tp is not None and t.bid >= p.tp:
                    self._close_at(p, p.tp, "target")
            else:
                if p.sl is not None and t.ask >= p.sl:
                    self._close_at(p, max(p.sl, t.ask) + self.slip, "stop")
                elif p.tp is not None and t.ask <= p.tp:
                    self._close_at(p, p.tp, "target")

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        """The engine's symbol only, as MT5Broker.positions (`positions_get(symbol=...)`)."""
        ps = [p for p in self._positions.values() if p.symbol == self.symbol]
        if magic_prefix is not None:
            ps = [p for p in ps if str(p.magic).startswith(str(magic_prefix))]
        return ps

    def account_positions(self) -> list[Position]:
        """Every position on the account, any symbol and magic (no swap: None)."""
        return list(self._positions.values())

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        d = pd.DataFrame(self._deals)
        return d[d["ts_utc"] >= since_utc] if not d.empty else d

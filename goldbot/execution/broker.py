"""Broker protocol. The engine never imports MetaTrader5 directly; every adapter maps broker-specific
names and units into these dataclasses (price in $/oz, volume in lots, times in UTC)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

import pandas as pd


@dataclass
class Tick:
    ts_utc: pd.Timestamp
    bid: float
    ask: float


@dataclass
class Bar:
    ts_utc: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread_points: float


@dataclass
class SymbolInfo:
    name: str
    digits: int
    point: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level_points: float
    filling_modes: list[str]
    trade_allowed: bool
    sessions: dict = field(default_factory=dict)
    commission_per_lot_side: float | None = None


@dataclass
class OrderIntent:
    client_order_id: str          # {account}-{bar_close_ts}-{intent_hash}, written to pending_orders BEFORE sending
    symbol: str
    side: int                     # +1 buy / -1 sell
    lots: float
    sl: float
    tp: float
    magic: int
    deviation_points: int = 30
    comment: str = ""


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    order_id: int | None
    position_id: int | None
    filled_lots: float
    price: float | None
    message: str = ""


@dataclass
class Position:
    position_id: int
    symbol: str
    side: int
    lots: float
    open_price: float
    sl: float | None
    tp: float | None
    magic: int
    comment: str
    open_time_utc: pd.Timestamp
    profit: float


@dataclass
class AccountInfo:
    login: int
    equity: float
    balance: float
    margin: float
    margin_free: float
    leverage: int
    currency: str
    server: str


class Broker(Protocol):
    name: str

    def symbol_info(self, symbol: str) -> SymbolInfo: ...
    def account(self) -> AccountInfo: ...
    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame: ...
    def last_tick(self, symbol: str) -> Tick: ...
    def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]: ...
    def place_order(self, intent: OrderIntent) -> OrderResult: ...
    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult: ...
    def close(self, position_id: int, lots: float | None = None) -> OrderResult: ...
    def positions(self, magic_prefix: int | None = None) -> list[Position]: ...
    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame: ...

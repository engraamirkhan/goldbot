"""Broker protocol. The engine never imports MetaTrader5 directly; every adapter maps broker-specific
names and units into these dataclasses (price in $/oz, volume in lots, times in UTC)."""
from __future__ import annotations

from typing import AsyncIterator, Protocol

import pandas as pd
from pydantic import Field

from goldbot.base import Record


class Tick(Record):
    ts_utc: pd.Timestamp
    bid: float
    ask: float
    flags: int = 0                # MT5 TICK_FLAG_* bits; 0 where the source has none (paper, replay, older payloads)


def tick_key(t: Tick) -> tuple[pd.Timestamp, float, float, int]:
    """The live collector's dedup key (design: Live collector "dedups on (time_msc, bid, ask, flags)"): two ticks in
    the same millisecond at the same prices but with different flags (e.g. a last/volume update) are both kept."""
    return t.ts_utc, t.bid, t.ask, t.flags


class Bar(Record):
    ts_utc: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread_points: float


class SymbolInfo(Record):
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
    sessions: dict = Field(default_factory=dict)
    commission_per_lot_side: float | None = None


class OrderIntent(Record):
    client_order_id: str          # {account}-{bar_close_ts}-{intent_hash}, written to pending_orders BEFORE sending
    symbol: str
    side: int                     # +1 buy / -1 sell
    lots: float
    sl: float
    tp: float
    magic: int
    deviation_points: int = 30
    comment: str = ""


class OrderResult(Record):
    ok: bool
    retcode: int
    order_id: int | None
    position_id: int | None
    filled_lots: float
    price: float | None
    message: str = ""


class Position(Record):
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


class AccountInfo(Record):
    login: int
    equity: float
    balance: float
    margin: float
    margin_free: float
    leverage: int
    currency: str
    server: str
    trade_mode: str | None = None   # demo | real | contest as the terminal reports it; None = not a broker terminal


class Broker(Protocol):
    name: str

    def symbol_info(self, symbol: str) -> SymbolInfo: ...
    def account(self) -> AccountInfo: ...
    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame: ...
    def last_tick(self, symbol: str) -> Tick: ...
    def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]: ...
    def margin_required(self, symbol: str, side: int, lots: float, price: float) -> float | None:
        """Margin in the account currency the broker would take for this order (MT5 `order_calc_margin`); None when
        the broker cannot say. Read-only; the RiskGate uses the larger of this and the 1:20 figure."""
        ...
    def place_order(self, intent: OrderIntent) -> OrderResult: ...
    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult: ...
    def close(self, position_id: int, lots: float | None = None) -> OrderResult: ...
    def positions(self, magic_prefix: int | None = None) -> list[Position]: ...
    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame: ...

"""Broker protocol. The engine never imports MetaTrader5 directly; every adapter maps broker-specific
names and units into these dataclasses (price in $/oz, volume in lots, times in UTC)."""
from __future__ import annotations

from typing import AsyncIterator, Protocol

import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.data.calendar import TradingSession

# ADR 0021: the bridge smoke's order (goldbot/ops/smoke.py) carries magic_base + this. No specialist family hashes to it
# (tests/test_magic.py); GuardedBroker refuses it unless the terminal reports a demo account (F4), and the broker ledger
# lists such positions apart from the RC-8 reconciliation (goldbot/ops/ledger.py)
SMOKE_MAGIC_OFFSET = 99
# the message prefix of an entry MT5Broker's per-order account check refused (retcode 10006, a definite reject): the
# terminal is no longer on the registered account. The engine alerts on it and the card says the engine refused it
ACCOUNT_REFUSAL = "terminal_account_changed"
# MT5Broker.place_order: the terminal's order_check refused the request or returned None, so order_send was never
# called. Negative (no MT5 trade-server code is) and distinct from -1 (a send that may have reached the server)
RETCODE_PRECHECK_FAILED = -2
PRECHECK_REFUSAL = "terminal_precheck_failed"
# The longest order comment the MT5 terminal accepts, measured 2026-10-20 on icm-probe (MetaTrader5 Python package
# 5.0.6231 under Wine): a comment of 30 characters or more makes `order_check` return None with
# `(-2, 'Invalid "comment" argument')`; 29 passes. Every comment the engine sends is at most this long.
MT5_COMMENT_MAX = 29
# the comment slice sent before 2026-10-20 (`cid[-31:]`): a position opened then still carries it
LEGACY_COMMENT_LEN = 31


def comment_keys(client_order_id: str) -> tuple[str, str, str]:
    """The MT5 comments an order `client_order_id` can carry: the whole id (paper, and ids of at most 29 characters),
    its last MT5_COMMENT_MAX characters (sent since 2026-10-20) and its last 31 (the legacy slice, kept so a position
    opened before the change is still found by reconciliation and the unconfirmed look-up)."""
    return client_order_id, client_order_id[-MT5_COMMENT_MAX:], client_order_id[-LEGACY_COMMENT_LEN:]

# MT5 trade-server retcodes that mean the order was definitely NOT placed, listed one by one (review LOW-1: only
# defined codes; 10037 does not exist): 10004 requote, 10006 reject, 10007 cancelled by trader, 10013-10022 invalid
# request / volume / price / stops, trade disabled, market closed, no money, prices changed, no quotes, invalid
# expiration, 10024-10030 too many requests, no changes, autotrading off (server, client), locked, frozen, invalid
# filling, 10032 only real accounts, 10033-10036 order and volume limits, invalid order type, position closed,
# 10038-10046 close volume, close order exists, position limit, reject cancel, long / short / close only, FIFO, hedge
# prohibited; the paper broker's duplicate refusal is 10027. Anything else that is not a fill (-1 send failed, 10008
# placed, 10010 partial, 10011 processing error, 10012 timeout, 10023 order state changed, 10031 no connection, any
# undefined or future code) is ambiguous: auto entries stop for the rest of that bar close (trading-safety review HIGH).
# RETCODE_PRECHECK_FAILED: the terminal's own order_check refused it or returned None, BEFORE any send (incident
# 2026-10-20, trading-safety ruling): definite, whatever MT5 code it came with; a None from order_send stays -1.
DEFINITE_REJECT_RETCODES = frozenset({
    RETCODE_PRECHECK_FAILED,
    10004, 10006, 10007,
    10013, 10014, 10015, 10016, 10017, 10018, 10019, 10020, 10021, 10022,
    10024, 10025, 10026, 10027, 10028, 10029, 10030,
    10032, 10033, 10034, 10035, 10036,
    10038, 10039, 10040, 10041, 10042, 10043, 10044, 10045, 10046,
})


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
    # ADR 0062 (the symbol check, goldbot/ops/symbol_check.py): the terminal's swap and margin terms as MT5 reports them
    # (swap_mode is MT5's SYMBOL_SWAP_MODE_*; swaps in that mode's unit; margin_* per lot, 0 = from the leverage).
    # Reporting only: no rule reads them, and None (an older bridge or the paper broker) is never taken as zero
    swap_mode: int | None = None
    swap_long: float | None = None
    swap_short: float | None = None
    margin_initial: float | None = None
    margin_maintenance: float | None = None


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
    # MT5 TradePosition.swap, account currency (profit excludes it); None = not reported (paper, a v4 bridge). Only the
    # equity cross-check reads it (ADR 0016); it never carries an outcome or a restriction (AGENT_STANDARDS S12)
    swap: float | None = None


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
    # hedging | netting | exchange (MT5 ACCOUNT_MARGIN_MODE_*); None = not reported (paper, or an older bridge)
    margin_mode: str | None = None
    # MT5 ACCOUNT_TRADE_ALLOWED: False on an investor (read-only) login (ADR 0063: an observe_only account must report
    # False, else its bridge serves nothing). None = not reported (paper, or a bridge older than v9); never a permission
    trade_allowed: bool | None = None


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
    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        """Account-currency loss of ONE lot of this side when the price moves `distance` against it from `price` (MT5
        `order_calc_profit`, else tick value / tick size); None when the broker cannot say. Read-only. The engine divides
        it by distance x contract to get the conversion the RiskGate sizes with (BACKLOG 28)."""
        ...
    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        """The symbol's trading sessions for SESSION_DAYS server days from today, each resolved to UTC for its own
        date (TRACEABILITY D13; `SessionTable.from_broker` builds the runtime table from it). Read-only."""
        ...
    def place_order(self, intent: OrderIntent) -> OrderResult: ...
    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult: ...
    def close(self, position_id: int, lots: float | None = None) -> OrderResult: ...
    def positions(self, magic_prefix: int | None = None) -> list[Position]: ...
    def account_positions(self) -> list[Position]: ...   # every symbol: the equity cross-check only (ADR 0016)
    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame: ...

"""MetaTrader 5 adapter (Windows only). Wraps the official `MetaTrader5` package, which binds one Python
process to one running terminal; each broker account gets its own portable terminal and engine process.

Everything broker-specific is read from symbol_info at startup: symbol suffix, digits, point, lot step,
stops level, filling modes. The adapter refuses to start if the symbol cannot be selected.
Times: MT5 returns server-clock epochs labelled UTC; converted here with the broker's IANA zone.

Measured costs (`broker_terms`, written by the engine to state/broker_terms_<account>.json for the nightly cost job):
* swap from symbol_info (swap_long/swap_short in the unit `swap_mode` names, triple day `swap_rollover3days`),
  converted to USD per lot per night by `swap_usd_per_lot`:
    0 disabled            -> 0 (no swap charged)
    1 points              -> swap x point x tick_value / tick_size   (tick_value is per lot in the deposit currency;
                             needs a USD deposit)
    2 base currency       -> as is when the base is USD, x price when the profit currency is USD (XAU -> USD)
    3 margin currency     -> as is when USD, x price when it is the base currency and the profit currency is USD
    4 deposit currency    -> as is with a USD deposit
    5 interest (current)  -> swap% / 100 / 360 x price x contract size (MT5 counts interest on a 360-day year)
    6 interest (open)     -> as 5 at the current price (a position's own open price is not known here; for a
                             trade of a few days the difference is the price change, negligible next to the rate)
    7/8 reopen modes, any other mode, or a non-USD deposit where one is needed: not converted. The swap stays None,
    the reason is logged and noted, and the cost table falls back to the settings prior (`costs.swap_*`).
  swap_rollover3days uses MT5's ENUM_DAY_OF_WEEK (0 = Sunday); it becomes the label's 0 = Monday weekday, and a
  weekend value is not used (the settings' triple day applies).
* commission from the deals of recent closed positions on the symbol: -(commission + fee) summed over every deal of
  each position that was opened and closed inside the window (reversals excluded), divided by the lots opened:
  USD per lot round trip. Swap and profit are not commission. Needs a USD deposit.
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

import pandas as pd

from goldbot.data.timeutil import server_to_utc
from goldbot.execution.broker import AccountInfo, OrderIntent, OrderResult, Position, SymbolInfo, Tick
from goldbot.execution.costs import BrokerTerms

try:  # pragma: no cover - Windows only
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

TF_MAP = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "1h": "TIMEFRAME_H1",
          "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1", "1w": "TIMEFRAME_W1"}
RETCODE_DONE = 10009
RETCODE_REQUOTE = 10004
RETCODE_REJECT = 10006

log = logging.getLogger("goldbot.mt5")

SWAP_MODES = {0: "disabled", 1: "points", 2: "base currency", 3: "margin currency", 4: "deposit currency",
              5: "interest on the current price", 6: "interest on the open price", 7: "reopen at close", 8: "reopen at bid"}
INTEREST_DAYS = 360
DEAL_TYPES = (0, 1)              # DEAL_TYPE_BUY, DEAL_TYPE_SELL (balance, credit, ... deals are not trades)
DEAL_ENTRY_IN, DEAL_ENTRY_INOUT = 0, 2


def swap_usd_per_lot(info: Any, *, account_currency: str, price: float) -> tuple[float | None, float | None, str]:
    """(long, short, note): the terminal's swap rates in USD per lot per night, broker sign; (None, None, reason) for
    a mode this conversion does not support (the caller keeps the settings prior)."""
    mode = int(info.swap_mode)
    name = SWAP_MODES.get(mode, f"unknown mode {mode}")
    lng, sht = float(info.swap_long), float(info.swap_short)
    usd_deposit = account_currency.upper() == "USD"
    base, profit, margin = (str(getattr(info, f"currency_{k}", "")).upper() for k in ("base", "profit", "margin"))
    factor: float | None = None
    if mode == 0:
        return 0.0, 0.0, "swap mode disabled: no swap charged"
    if mode == 1 and usd_deposit and info.trade_tick_size > 0:
        factor = float(info.point) * float(info.trade_tick_value) / float(info.trade_tick_size)
    elif mode in (2, 3):
        cur = base if mode == 2 else margin
        if cur == "USD":
            factor = 1.0
        elif cur == base and profit == "USD" and price > 0:
            factor = float(price)
    elif mode == 4 and usd_deposit:
        factor = 1.0
    elif mode in (5, 6) and profit == "USD" and price > 0:
        factor = float(price) * float(info.trade_contract_size) / 100.0 / INTEREST_DAYS
    if factor is None:
        return None, None, (f"swap mode {name} (deposit {account_currency}, base {base}, profit {profit}, margin {margin}) "
                            "is not converted: the settings prior applies")
    note = f"swap mode {name}" + (" (current price used for the open price)" if mode == 6 else "")
    return lng * factor, sht * factor, note


def triple_weekday(rollover3days: int) -> int | None:
    """MT5 ENUM_DAY_OF_WEEK (0 = Sunday) -> 0 = Monday; None for a weekend day."""
    v = int(rollover3days)
    return v - 1 if 1 <= v <= 5 else None


def commission_round_trip_per_lot(deals: pd.DataFrame, symbol: str) -> tuple[float | None, float, str]:
    """(USD per lot round trip, lots, note) from the deals of positions opened and closed inside the window."""
    need = {"position_id", "symbol", "type", "entry", "volume", "commission"}
    if deals.empty or not need <= set(deals.columns):
        return None, 0.0, "no deals in the window: commission from settings"
    d = deals[(deals["symbol"] == symbol) & deals["type"].isin(DEAL_TYPES)]
    fee = d["fee"].fillna(0.0) if "fee" in d else 0.0
    d = d.assign(cost=-(d["commission"].fillna(0.0) + fee))
    complete = [g for _, g in d.groupby("position_id")
                if (g["entry"] == DEAL_ENTRY_IN).any() and (g["entry"] != DEAL_ENTRY_IN).any()
                and not (g["entry"] == DEAL_ENTRY_INOUT).any()]
    lots = float(sum(g.loc[g["entry"] == DEAL_ENTRY_IN, "volume"].sum() for g in complete))
    if not complete or lots <= 0:
        return None, 0.0, "no closed positions in the window: commission from settings"
    cost = float(sum(g["cost"].sum() for g in complete))
    return cost / lots, lots, f"commission from {len(complete)} closed positions ({lots:g} lots)"


def measure_broker_terms(account_id: str, info: Any, *, account_currency: str, price: float, deals: pd.DataFrame,
                         now: pd.Timestamp) -> BrokerTerms:
    """The terminal's swap and recent commission as BrokerTerms; whatever cannot be measured stays None with a note
    (logged), so the cost table keeps the settings value for it."""
    lng, sht, swap_note = swap_usd_per_lot(info, account_currency=account_currency, price=price)
    notes = [swap_note]
    if lng is None:
        log.warning("%s: %s", account_id, swap_note)
    triple = triple_weekday(info.swap_rollover3days)
    if triple is None:
        notes.append(f"swap_rollover3days={info.swap_rollover3days} is a weekend day: the settings' triple day applies")
    if account_currency.upper() == "USD":
        rt, lots, c_note = commission_round_trip_per_lot(deals, str(info.name))
    else:
        rt, lots, c_note = None, 0.0, f"deposit currency {account_currency}: commission from settings"
    notes.append(c_note)
    return BrokerTerms(account_id=account_id, measured_utc=now, swap_long_usd_per_lot=lng, swap_short_usd_per_lot=sht,
                       swap_triple_weekday=triple, swap_mode=int(info.swap_mode),
                       commission_per_lot_round_trip_usd=rt, commission_lots=lots, notes=notes)


class MT5Broker:
    name = "mt5"

    def __init__(self, *, terminal_path: str, login: int, password: str, server: str, server_tz: str,
                 symbol: str, account_label: str):
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package is Windows-only; run this adapter on the VPS")
        self.server_tz = server_tz
        self.symbol = symbol
        self.account_label = account_label
        if not mt5.initialize(path=terminal_path, login=login, password=password, server=server):
            raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(symbol, True):
            mt5.shutdown()
            raise RuntimeError(f"symbol {symbol!r} not available in Market Watch")
        info = mt5.symbol_info(symbol)
        if info is None or not info.visible:
            mt5.shutdown()
            raise RuntimeError(f"symbol_info({symbol}) returned None; refusing to start")
        self._info = info

    # ------------------------------------------------------------------ info
    def symbol_info(self, symbol: str) -> SymbolInfo:
        i = mt5.symbol_info(symbol)
        modes = []
        if i.filling_mode & 1:
            modes.append("FOK")
        if i.filling_mode & 2:
            modes.append("IOC")
        modes.append("RETURN")
        return SymbolInfo(name=i.name, digits=i.digits, point=i.point, contract_size=i.trade_contract_size, volume_min=i.volume_min, volume_step=i.volume_step, volume_max=i.volume_max,
                          stops_level_points=i.trade_stops_level, filling_modes=modes, trade_allowed=i.trade_mode != 0)

    def account(self) -> AccountInfo:
        a = mt5.account_info()
        return AccountInfo(login=a.login, equity=a.equity, balance=a.balance, margin=a.margin, margin_free=a.margin_free, leverage=a.leverage, currency=a.currency, server=a.server)

    # ------------------------------------------------------------------ data
    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, TF_MAP[tf]), 0, n)
        df = pd.DataFrame(rates)
        df["ts_utc"] = server_to_utc(pd.to_datetime(df["time"], unit="s"), self.server_tz)
        return df.rename(columns={"tick_volume": "tick_count"})[["ts_utc", "open", "high", "low", "close", "tick_count", "spread"]]

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        # copy_ticks_from takes server time; convert the UTC request into server clock
        since_server = since_utc.tz_convert(self.server_tz).tz_localize(None)
        ticks = mt5.copy_ticks_from(symbol, since_server.to_pydatetime(), n, mt5.COPY_TICKS_ALL)
        df = pd.DataFrame(ticks)
        if df.empty:
            return df
        df["ts_utc"] = server_to_utc(pd.to_datetime(df["time_msc"], unit="ms"), self.server_tz)
        return df[["ts_utc", "bid", "ask", "flags"]].drop_duplicates(["ts_utc", "bid", "ask", "flags"])

    def last_tick(self, symbol: str) -> Tick:
        t = mt5.symbol_info_tick(symbol)
        ts = server_to_utc(pd.Series(pd.to_datetime([t.time_msc], unit="ms")), self.server_tz)[0]
        return Tick(ts_utc=ts, bid=t.bid, ask=t.ask)

    async def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]:  # pragma: no cover
        import asyncio
        last = None
        while True:
            t = self.last_tick(symbol)
            if last is None or (t.ts_utc, t.bid, t.ask) != last:
                last = (t.ts_utc, t.bid, t.ask)
                yield t
            await asyncio.sleep(0.25)

    # ------------------------------------------------------------------ orders
    def _filling(self) -> int:
        modes = self.symbol_info(self.symbol).filling_modes
        for pref, const in (("IOC", "ORDER_FILLING_IOC"), ("FOK", "ORDER_FILLING_FOK"), ("RETURN", "ORDER_FILLING_RETURN")):
            if pref in modes:
                return getattr(mt5, const)
        return mt5.ORDER_FILLING_RETURN

    def place_order(self, intent: OrderIntent) -> OrderResult:
        tick = mt5.symbol_info_tick(intent.symbol)
        price = tick.ask if intent.side > 0 else tick.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": intent.symbol, "volume": intent.lots,
            "type": mt5.ORDER_TYPE_BUY if intent.side > 0 else mt5.ORDER_TYPE_SELL, "price": price,
            "sl": intent.sl, "tp": intent.tp, "deviation": intent.deviation_points, "magic": intent.magic,
            "comment": intent.comment[:31], "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(),
        }
        for attempt in range(3):
            chk = mt5.order_check(req)
            if chk is None or chk.retcode not in (0, RETCODE_DONE):
                return OrderResult(ok=False, retcode=getattr(chk, "retcode", -1), order_id=None, position_id=None, filled_lots=0.0, price=None, message=f"order_check: {getattr(chk, 'comment', '')}")
            res = mt5.order_send(req)
            if res is not None and res.retcode == RETCODE_DONE:
                return OrderResult(ok=True, retcode=res.retcode, order_id=res.order, position_id=res.deal, filled_lots=res.volume, price=res.price, message=res.comment)
            if res is not None and res.retcode in (RETCODE_REQUOTE, RETCODE_REJECT) and attempt < 2:
                time.sleep(0.5)
                tick = mt5.symbol_info_tick(intent.symbol)
                req["price"] = tick.ask if intent.side > 0 else tick.bid
                continue
            return OrderResult(ok=False, retcode=getattr(res, "retcode", -1), order_id=None, position_id=None, filled_lots=0.0, price=None, message=getattr(res, "comment", "send failed"))
        return OrderResult(ok=False, retcode=-1, order_id=None, position_id=None, filled_lots=0.0, price=None, message="FAILED_EXEC")

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        pos = [p for p in mt5.positions_get() or [] if p.ticket == position_id]
        if not pos:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=position_id, filled_lots=0.0, price=None, message="no such position")
        p = pos[0]
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": position_id,
               "sl": sl if sl is not None else p.sl, "tp": tp if tp is not None else p.tp}
        res = mt5.order_send(req)
        return OrderResult(ok=res.retcode == RETCODE_DONE, retcode=res.retcode, order_id=None, position_id=position_id, filled_lots=p.volume, price=None, message=res.comment)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        pos = [p for p in mt5.positions_get() or [] if p.ticket == position_id]
        if not pos:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=position_id, filled_lots=0.0, price=None, message="no such position")
        p = pos[0]
        tick = mt5.symbol_info_tick(p.symbol)
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "position": position_id,
               "volume": lots or p.volume, "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "price": tick.bid if is_buy else tick.ask, "deviation": 30, "magic": p.magic,
               "comment": "close", "type_filling": self._filling()}
        res = mt5.order_send(req)
        return OrderResult(ok=res.retcode == RETCODE_DONE, retcode=res.retcode, order_id=res.order, position_id=position_id, filled_lots=res.volume, price=res.price, message=res.comment)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        out = []
        for p in mt5.positions_get(symbol=self.symbol) or []:
            if magic_prefix is not None and not str(p.magic).startswith(str(magic_prefix)):
                continue
            ts = server_to_utc(pd.Series(pd.to_datetime([p.time_msc], unit="ms")), self.server_tz)[0]
            out.append(Position(position_id=p.ticket, symbol=p.symbol, side=1 if p.type == mt5.POSITION_TYPE_BUY else -1, lots=p.volume, open_price=p.price_open,
                                sl=p.sl or None, tp=p.tp or None, magic=p.magic, comment=p.comment, open_time_utc=ts, profit=p.profit))
        return out

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        since_server = since_utc.tz_convert(self.server_tz).tz_localize(None).to_pydatetime()
        until_server = pd.Timestamp.now(self.server_tz).tz_localize(None) + pd.Timedelta(days=1)
        deals = mt5.history_deals_get(since_server, until_server.to_pydatetime())
        df = pd.DataFrame([d._asdict() for d in deals]) if deals else pd.DataFrame()
        if not df.empty:
            df["ts_utc"] = server_to_utc(pd.to_datetime(df["time_msc"], unit="ms"), self.server_tz)
        return df

    def broker_terms(self, account_id: str, since_utc: pd.Timestamp, now: pd.Timestamp) -> BrokerTerms:
        """Swap and commission as this terminal reports them (see the module docstring for the conversions)."""
        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        price = (float(tick.bid) + float(tick.ask)) / 2 if tick is not None else 0.0
        return measure_broker_terms(account_id, info, account_currency=str(mt5.account_info().currency), price=price,
                                    deals=self.deals_since(since_utc), now=now)

    def shutdown(self) -> None:  # pragma: no cover
        mt5.shutdown()

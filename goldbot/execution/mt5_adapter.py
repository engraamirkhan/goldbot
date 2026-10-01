"""MetaTrader 5 adapter (Windows only). Wraps the official `MetaTrader5` package, which binds one Python
process to one running terminal; each broker account gets its own portable terminal and engine process.

Everything broker-specific is read from symbol_info at startup: symbol suffix, digits, point, lot step,
stops level, filling modes. The adapter refuses to start if the symbol cannot be selected.
Times: MT5 returns server-clock epochs labelled UTC; converted here with the broker's IANA zone.
"""
from __future__ import annotations

import time
from typing import AsyncIterator

import pandas as pd

from goldbot.data.timeutil import server_to_utc
from goldbot.execution.broker import AccountInfo, Bar, OrderIntent, OrderResult, Position, SymbolInfo, Tick

try:  # pragma: no cover - Windows only
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

TF_MAP = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "1h": "TIMEFRAME_H1",
          "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1", "1w": "TIMEFRAME_W1"}
RETCODE_DONE = 10009
RETCODE_REQUOTE = 10004
RETCODE_REJECT = 10006


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
        return SymbolInfo(i.name, i.digits, i.point, i.trade_contract_size, i.volume_min, i.volume_step, i.volume_max,
                          i.trade_stops_level, modes, i.trade_mode != 0)

    def account(self) -> AccountInfo:
        a = mt5.account_info()
        return AccountInfo(a.login, a.equity, a.balance, a.margin, a.margin_free, a.leverage, a.currency, a.server)

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
        return Tick(ts, t.bid, t.ask)

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
                return OrderResult(False, getattr(chk, "retcode", -1), None, None, 0.0, None, f"order_check: {getattr(chk, 'comment', '')}")
            res = mt5.order_send(req)
            if res is not None and res.retcode == RETCODE_DONE:
                return OrderResult(True, res.retcode, res.order, res.deal, res.volume, res.price, res.comment)
            if res is not None and res.retcode in (RETCODE_REQUOTE, RETCODE_REJECT) and attempt < 2:
                time.sleep(0.5)
                tick = mt5.symbol_info_tick(intent.symbol)
                req["price"] = tick.ask if intent.side > 0 else tick.bid
                continue
            return OrderResult(False, getattr(res, "retcode", -1), None, None, 0.0, None, getattr(res, "comment", "send failed"))
        return OrderResult(False, -1, None, None, 0.0, None, "FAILED_EXEC")

    def modify(self, position_id: int, sl, tp) -> OrderResult:
        pos = [p for p in mt5.positions_get() or [] if p.ticket == position_id]
        if not pos:
            return OrderResult(False, 10013, None, position_id, 0.0, None, "no such position")
        p = pos[0]
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": position_id,
               "sl": sl if sl is not None else p.sl, "tp": tp if tp is not None else p.tp}
        res = mt5.order_send(req)
        return OrderResult(res.retcode == RETCODE_DONE, res.retcode, None, position_id, p.volume, None, res.comment)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        pos = [p for p in mt5.positions_get() or [] if p.ticket == position_id]
        if not pos:
            return OrderResult(False, 10013, None, position_id, 0.0, None, "no such position")
        p = pos[0]
        tick = mt5.symbol_info_tick(p.symbol)
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "position": position_id,
               "volume": lots or p.volume, "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "price": tick.bid if is_buy else tick.ask, "deviation": 30, "magic": p.magic,
               "comment": "close", "type_filling": self._filling()}
        res = mt5.order_send(req)
        return OrderResult(res.retcode == RETCODE_DONE, res.retcode, res.order, position_id, res.volume, res.price, res.comment)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        out = []
        for p in mt5.positions_get(symbol=self.symbol) or []:
            if magic_prefix is not None and not str(p.magic).startswith(str(magic_prefix)):
                continue
            ts = server_to_utc(pd.Series(pd.to_datetime([p.time_msc], unit="ms")), self.server_tz)[0]
            out.append(Position(p.ticket, p.symbol, 1 if p.type == mt5.POSITION_TYPE_BUY else -1, p.volume, p.price_open,
                                p.sl or None, p.tp or None, p.magic, p.comment, ts, p.profit))
        return out

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        since_server = since_utc.tz_convert(self.server_tz).tz_localize(None).to_pydatetime()
        deals = mt5.history_deals_get(since_server, pd.Timestamp.utcnow().to_pydatetime())
        df = pd.DataFrame([d._asdict() for d in deals]) if deals else pd.DataFrame()
        if not df.empty:
            df["ts_utc"] = server_to_utc(pd.to_datetime(df["time_msc"], unit="ms"), self.server_tz)
        return df

    def shutdown(self) -> None:  # pragma: no cover
        mt5.shutdown()

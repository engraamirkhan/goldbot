"""MetaTrader 5 adapter (Windows only). Wraps the official `MetaTrader5` package, which binds one Python
process to one running terminal; each broker account gets its own portable terminal and engine process.

Everything broker-specific is read from symbol_info at startup: symbol suffix, digits, point, lot step,
stops level, filling modes. The adapter refuses to start if the symbol cannot be selected.
Times: MT5 returns server-clock epochs labelled UTC; converted here with the account's server_tz (IANA or NY+7).

Measured costs (`broker_terms`, written by the engine to state/broker_terms_<account>.json for the nightly cost job):
* swap from symbol_info (swap_long/swap_short in the unit `swap_mode` names, triple day `swap_rollover3days`),
  converted to USD per lot per night by `swap_usd_per_lot`:
    0 disabled            -> 0 (no swap charged)
    1 points              -> swap x point x tick_value / tick_size / fx   (tick_value is per lot in the deposit
                             currency; fx below)
    2 base currency       -> as is when the base is USD, x price when the profit currency is USD (XAU -> USD)
    3 margin currency     -> as is when USD, x price when it is the base currency and the profit currency is USD
    4 deposit currency    -> swap / fx
    5 interest (current)  -> swap% / 100 / 360 x price x contract size (MT5 counts interest on a 360-day year)
    6 interest (open)     -> as 5 at the current price (a position's own open price is not known here; for a
                             trade of a few days the difference is the price change, negligible next to the rate)
    7/8 reopen modes, any other mode, or a deposit-currency amount without a usable fx: not converted. The swap stays
    None, the reason is logged and noted, and the cost table falls back to the settings prior (`costs.swap_*`).
  fx is the gate's conversion (BACKLOG 28; costs-binding-2026-10-18.md K1/K2): `sizing.account_fx` of the broker's
  account currency per USD of XAUUSD P&L (`loss_per_lot` over $1 / contract), so a USD account is 1 and a GBP account
  is the terminal's own GBPUSD figure, named in the note; a blank currency, a missing figure, or a USD figure more
  than 1% off 1 leaves the amount unconverted, as the gate refuses to size on it.
  swap_rollover3days uses MT5's ENUM_DAY_OF_WEEK (0 = Sunday); it becomes the label's 0 = Monday weekday, and a
  weekend value is not used (the settings' triple day applies).
* commission from the deals of recent closed positions on the symbol: -(commission + fee) summed over every deal of
  each position that was opened and closed inside the window (reversals excluded), divided by the lots opened:
  deposit currency per lot round trip, / fx for USD. Swap and profit are not commission. The deals carry no rate of
  their own: the measurement-time fx is used (commission is a fixed rate a lot; the GBPUSD move across the deal window
  is a few per cent of about 0.07 $/oz).

Trading sessions (TRACEABILITY D13): the MetaTrader5 Python package has no equivalent of MQL5's
SymbolInfoSessionTrade/SymbolInfoSessionQuote, and `symbol_info()` carries no schedule: `session_deals`,
`session_open`, `session_close` and the other `session_*` fields are statistics of the current session (deal count,
open/close prices, volumes), not its hours. `trading_sessions` therefore reports the broker's documented hours (the
static `SessionTable` defaults, IC Markets / Vantage XAUUSD) resolved to UTC per date with the server zone, and logs
that once. Holidays and early closes are not visible this way; reading the real schedule needs an MQL5 helper in the
terminal that writes SymbolInfoSessionTrade to a file (not built).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Any, AsyncIterator, Callable

import pandas as pd

from goldbot.data.calendar import SessionTable, TradingSession, resolve_server_sessions
from goldbot.data.timeutil import server_to_utc, server_wall
from goldbot.execution.broker import (
    ACCOUNT_REFUSAL,
    DEFINITE_REJECT_RETCODES,
    MT5_COMMENT_MAX,
    PRECHECK_REFUSAL,
    RETCODE_PRECHECK_FAILED,
    AccountInfo,
    OrderIntent,
    OrderResult,
    Position,
    SymbolInfo,
    Tick,
    tick_key,
)
from goldbot.execution.costs import BrokerTerms
from goldbot.risk.sizing import account_fx

try:  # pragma: no cover - Windows only
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

TF_MAP = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "1h": "TIMEFRAME_H1",
          "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1", "1w": "TIMEFRAME_W1"}
RETCODE_DONE = 10009
# initialize() attaches to the RUNNING terminal only with its path (Wine box, 16 Oct: without path= it failed with IPC
# timeout -10005); the timeout is passed explicitly so a package default never changes what was verified there.
MT5_INIT_TIMEOUT_MS = 60_000
RETCODE_REQUOTE = 10004
RETCODE_REJECT = 10006
RETCODE_INVALID_VOLUME = 10014
RETCODE_PRICE_OFF = 10021        # no quotes: a close the engine retries (not a market-closed backoff code)


def _positions_get(**kw: Any) -> Any:
    """mt5.positions_get, raising on None: None is a terminal error (disconnected, not initialised), never "no
    positions", so the engine cannot mistake a fault for a flat book and record closes that did not happen."""
    got = mt5.positions_get(**kw)
    if got is None:
        raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
    return got


def _position_ticket(res: Any) -> int:
    """The position a filled market order opened or joined (trading-safety MEDIUM-A). In MT5 a position's ticket is the
    ticket of the ORDER that opened it, never the deal's. `history_deals_get(ticket=X)` selects the deals of ORDER X
    (review MEDIUM-1: asking with the deal ticket would return an unrelated older order's deals), so the order's deals
    are read and the one whose ticket is the fill's (`res.deal`) names the position, which is also right on a netting
    account where the fill joins an existing position. When that deal cannot be read (None, empty, an error, not
    synced yet) the order ticket, which is the position's on a hedging account. Verify on the demo terminal."""
    pid = 0
    try:
        for d in mt5.history_deals_get(ticket=res.order) or ():
            if int(d.ticket) == int(res.deal):
                pid = int(d.position_id)
                break
    except Exception:                                   # the order ticket is still the right answer on hedging
        pid = 0
    return pid if pid > 0 else int(res.order)


def _send_failed(position_id: int) -> OrderResult:
    """order_send returned None (a terminal error): a refused result the engine retries, never a crash."""
    return OrderResult(ok=False, retcode=-1, order_id=None, position_id=position_id, filled_lots=0.0, price=None,
                       message=f"order_send failed: {mt5.last_error()}")

log = logging.getLogger("goldbot.mt5")


def _precheck_failed(why: str) -> OrderResult:
    """order_check refused the request (with a code that would read as ambiguous from order_send) or returned None:
    order_send was never called, so a definite reject, RETCODE_PRECHECK_FAILED."""
    return OrderResult(ok=False, retcode=RETCODE_PRECHECK_FAILED, order_id=None, position_id=None, filled_lots=0.0,
                       price=None, message=f"{PRECHECK_REFUSAL}: {why}")


def _request_types(req: dict[str, Any]) -> str:
    """`key=type` for every field of an order request, for a log line: the TYPES only (an argument of the wrong type is
    what makes order_check return None), never a value (the comment and magic identify the strategy and account)."""
    return " ".join(f"{k}={type(v).__name__}" for k, v in req.items())

SWAP_MODES = {0: "disabled", 1: "points", 2: "base currency", 3: "margin currency", 4: "deposit currency",
              5: "interest on the current price", 6: "interest on the open price", 7: "reopen at close", 8: "reopen at bid"}
INTEREST_DAYS = 360
DEAL_TYPES = (0, 1)              # DEAL_TYPE_BUY, DEAL_TYPE_SELL (balance, credit, ... deals are not trades)
DEAL_ENTRY_IN, DEAL_ENTRY_INOUT = 0, 2


def _fx_note(account_currency: str, fx: float) -> str:
    cur = account_currency.strip().upper()
    return "" if cur == "USD" else f" (converted at {fx:.4f} {cur} per USD, the broker's conversion)"


def swap_usd_per_lot(info: Any, *, account_currency: str, price: float,
                     acct_per_usd: float | None = None) -> tuple[float | None, float | None, str]:
    """(long, short, note): the terminal's swap rates in USD per lot per night, broker sign; (None, None, reason) for
    a mode this conversion does not support (the caller keeps the settings prior). `acct_per_usd`: the broker's account
    currency per USD (module docstring, fx), needed only where the rate is in the deposit currency (modes 1 and 4)."""
    mode = int(info.swap_mode)
    name = SWAP_MODES.get(mode, f"unknown mode {mode}")
    lng, sht = float(info.swap_long), float(info.swap_short)
    fx = account_fx(account_currency, acct_per_usd)
    base, profit, margin = (str(getattr(info, f"currency_{k}", "")).upper() for k in ("base", "profit", "margin"))
    factor: float | None = None
    fx_note = ""
    if mode == 0:
        return 0.0, 0.0, "swap mode disabled: no swap charged"
    if mode == 1 and fx is not None and info.trade_tick_size > 0:
        factor = float(info.point) * float(info.trade_tick_value) / float(info.trade_tick_size) / fx
        fx_note = _fx_note(account_currency, fx)
    elif mode in (2, 3):
        cur = base if mode == 2 else margin
        if cur == "USD":
            factor = 1.0
        elif cur == base and profit == "USD" and price > 0:
            factor = float(price)
    elif mode == 4 and fx is not None:
        factor = 1.0 / fx
        fx_note = _fx_note(account_currency, fx)
    elif mode in (5, 6) and profit == "USD" and price > 0:
        factor = float(price) * float(info.trade_contract_size) / 100.0 / INTEREST_DAYS
    if factor is None:
        return None, None, (f"swap mode {name} (deposit {account_currency}, base {base}, profit {profit}, margin {margin}) "
                            "is not converted: the settings prior applies")
    note = f"swap mode {name}" + (" (current price used for the open price)" if mode == 6 else "") + fx_note
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
                         now: pd.Timestamp, acct_per_usd: float | None = None) -> BrokerTerms:
    """The terminal's swap and recent commission as BrokerTerms; whatever cannot be measured stays None with a note
    (logged), so the cost table keeps the settings value for it. Deposit-currency amounts are converted with
    `acct_per_usd` through `sizing.account_fx`, as the gate converts the stop (module docstring)."""
    lng, sht, swap_note = swap_usd_per_lot(info, account_currency=account_currency, price=price,
                                           acct_per_usd=acct_per_usd)
    notes = [swap_note]
    if lng is None:
        log.warning("%s: %s", account_id, swap_note)
    triple = triple_weekday(info.swap_rollover3days)
    if triple is None:
        notes.append(f"swap_rollover3days={info.swap_rollover3days} is a weekend day: the settings' triple day applies")
    fx = account_fx(account_currency, acct_per_usd)
    if fx is not None:
        rt, lots, c_note = commission_round_trip_per_lot(deals, str(info.name))
        if rt is not None:
            rt, c_note = rt / fx, c_note + _fx_note(account_currency, fx)
    else:
        rt, lots, c_note = None, 0.0, (f"deposit currency {account_currency or 'unknown'} without the broker's "
                                       "conversion: commission from settings")
    notes.append(c_note)
    return BrokerTerms(account_id=account_id, measured_utc=now, swap_long_usd_per_lot=lng, swap_short_usd_per_lot=sht,
                       swap_triple_weekday=triple, swap_mode=int(info.swap_mode),
                       commission_per_lot_round_trip_usd=rt, commission_lots=lots, notes=notes)


def account_info_from_mt5(a: Any) -> AccountInfo:
    """mt5.account_info() as an AccountInfo, the shape accounts.verify_terminal_account checks (MT5Broker.account and
    the keyring login in goldbot/ops/login.py read it the same way)."""
    mode = {0: "demo", 1: "contest", 2: "real"}.get(int(getattr(a, "trade_mode", -1)))   # ACCOUNT_TRADE_MODE_*
    margin = {0: "netting", 1: "exchange", 2: "hedging"}.get(int(getattr(a, "margin_mode", -1)))  # ACCOUNT_MARGIN_MODE_*
    allowed = getattr(a, "trade_allowed", None)          # ACCOUNT_TRADE_ALLOWED: False on an investor login (ADR 0063)
    return AccountInfo(login=a.login, equity=a.equity, balance=a.balance, margin=a.margin, margin_free=a.margin_free,
                       leverage=a.leverage, currency=a.currency, server=a.server, trade_mode=mode, margin_mode=margin,
                       trade_allowed=None if allowed is None else bool(allowed))


class MT5Broker:
    """`verify` is the per-order account check (trading-safety MEDIUM; the bridge's GuardedBroker does the same): the
    engine passes `accounts.verify_terminal_account` for its account, and every `place_order` reads the terminal's
    account first and refuses the entry when the check names a reason (another login, another server, a real terminal
    under a demo account or the reverse, not hedging) or when the account cannot be read or checked. A refusal is a
    definite reject (RETCODE_REJECT, message "terminal_account_changed: <reason>", logged), never a raise and never a
    send. `close` and `modify` are never checked, exactly as the bridge: closing only reduces risk and the 12% kill
    switch closes everything on the account (design: Drawdown kill switch). There is no default: every caller decides;
    None only where nothing above it can be bypassed (the bridge, whose GuardedBroker verifies every order) or nothing
    is ever ordered (the scheduler's read-only broker)."""
    name = "mt5"

    def __init__(self, *, terminal_path: str, login: int | None, password: str | None, server: str, server_tz: str,
                 symbol: str, account_label: str, verify: Callable[[Any], str | None] | None):
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package is Windows-only; run this adapter on the VPS")
        self.server_tz = server_tz
        self.symbol = symbol
        self.account_label = account_label
        self._verify = verify
        # without a login the terminal's own saved account is used (bridge on Wine: no password leaves the terminal)
        creds = {} if login is None else {"login": login, "password": password, "server": server}
        if not mt5.initialize(path=terminal_path, timeout=MT5_INIT_TIMEOUT_MS, **creds):
            raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(symbol, True):
            mt5.shutdown()
            raise RuntimeError(f"symbol {symbol!r} not available in Market Watch")
        info = mt5.symbol_info(symbol)
        if info is None or not info.visible:
            mt5.shutdown()
            raise RuntimeError(f"symbol_info({symbol}) returned None; refusing to start")
        self._info = info
        self._sessions_noted = False
        self._unquoted: set[str] = set()     # symbols whose close was refused for want of a quote (logged once)

    # ------------------------------------------------------------------ info
    def terminal_info(self) -> Any:
        """mt5.terminal_info() as the package returns it (None when the terminal is gone or its IPC is dead). Read by
        GuardedBroker.terminal_state for the bridge's GET /health only; not an RPC method (METHODS)."""
        return mt5.terminal_info()

    def symbol_info(self, symbol: str) -> SymbolInfo:
        i = mt5.symbol_info(symbol)
        modes = []
        if i.filling_mode & 1:
            modes.append("FOK")
        if i.filling_mode & 2:
            modes.append("IOC")
        modes.append("RETURN")
        def opt(name: str) -> float | None:          # ADR 0062: reported when the package has the field
            v = getattr(i, name, None)
            return None if v is None else float(v)
        swap_mode = getattr(i, "swap_mode", None)
        return SymbolInfo(name=i.name, digits=i.digits, point=i.point, contract_size=i.trade_contract_size, volume_min=i.volume_min, volume_step=i.volume_step, volume_max=i.volume_max,
                          stops_level_points=i.trade_stops_level, filling_modes=modes, trade_allowed=i.trade_mode != 0,
                          swap_mode=None if swap_mode is None else int(swap_mode), swap_long=opt("swap_long"),
                          swap_short=opt("swap_short"), margin_initial=opt("margin_initial"),
                          margin_maintenance=opt("margin_maintenance"))

    def symbols_get(self, pattern: str = "*") -> list[str]:
        """The names of the terminal's symbols matching `pattern` (MT5 symbols_get group filter, e.g. "XAUUSD*"),
        read-only (ADR 0062: does the account's symbol exist on this terminal); an empty list when none or on error."""
        return [str(s.name) for s in (mt5.symbols_get(group=pattern) or ())]

    def account(self) -> AccountInfo:
        return account_info_from_mt5(mt5.account_info())

    # ------------------------------------------------------------------ data
    def _mt5_time(self, utc: pd.Timestamp) -> datetime:
        """A UTC instant as the datetime MT5 requests take: the server wall clock labelled UTC (tz-aware). MT5 reads a
        naive datetime in the HOST's local zone, so a naive value would be right only on a UTC host (Wine box)."""
        return server_wall(pd.Timestamp(utc), self.server_tz)[0].tz_localize("UTC").to_pydatetime()

    _BAR_COLS = ["ts_utc", "open", "high", "low", "close", "tick_count", "spread"]

    def _rates_frame(self, rates: Any) -> pd.DataFrame:
        """MT5 rates (server-clock epochs) as UTC bars; an empty frame when the terminal returned none (None or [])."""
        df = pd.DataFrame(rates) if rates is not None else pd.DataFrame()
        if df.empty:
            return pd.DataFrame(columns=self._BAR_COLS)
        df["ts_utc"] = server_to_utc(pd.to_datetime(df["time"], unit="s"), self.server_tz)
        return df.rename(columns={"tick_volume": "tick_count"})[self._BAR_COLS]

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        return self._rates_frame(mt5.copy_rates_from_pos(symbol, getattr(mt5, TF_MAP[tf]), 0, n))

    def get_bars_range(self, symbol: str, tf: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
        """Every bar the terminal holds with an open time in [start_utc, end_utc] (MT5 copy_rates_range, read-only;
        the UTC bounds converted to the server clock as copy_ticks does). The D12 reconcile reads its window with it,
        so a short history shows as missing minutes at the window's start rather than a silently shorter tail."""
        return self._rates_frame(mt5.copy_rates_range(symbol, getattr(mt5, TF_MAP[tf]), self._mt5_time(start_utc),
                                                      self._mt5_time(end_utc)))

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        # copy_ticks_from takes server time; convert the UTC request into server clock
        ticks = mt5.copy_ticks_from(symbol, self._mt5_time(since_utc), n, mt5.COPY_TICKS_ALL)
        df = pd.DataFrame(ticks)
        if df.empty:
            return df
        df["ts_utc"] = server_to_utc(pd.to_datetime(df["time_msc"], unit="ms"), self.server_tz)
        return df[["ts_utc", "bid", "ask", "flags"]].drop_duplicates(["ts_utc", "bid", "ask", "flags"])

    def last_tick(self, symbol: str) -> Tick:
        t = mt5.symbol_info_tick(symbol)
        ts = server_to_utc(pd.Series(pd.to_datetime([t.time_msc], unit="ms")), self.server_tz)[0]
        return Tick(ts_utc=ts, bid=t.bid, ask=t.ask, flags=int(getattr(t, "flags", 0)))

    async def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]:  # pragma: no cover
        import asyncio
        last = None
        while True:
            t = self.last_tick(symbol)
            if last is None or tick_key(t) != last:      # (time_msc, bid, ask, flags), design: Live collector
                last = tick_key(t)
                yield t
            await asyncio.sleep(0.25)

    def margin_required(self, symbol: str, side: int, lots: float, price: float) -> float | None:
        """`order_calc_margin` for a market order of this side and size at this price, in the account currency; None
        when the terminal cannot answer (the gate then falls back to the 1:20 figure and records why)."""
        action = mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL
        m = mt5.order_calc_margin(action, symbol, float(lots), float(price))
        if m is None:
            log.warning("order_calc_margin(%s, %s lots) returned None: %s", symbol, lots, mt5.last_error())
            return None
        return float(m)

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        """Account-currency loss of one lot of `side` moving `distance` against it from `price` (BACKLOG 28):
        `order_calc_profit` (the terminal converts USD P&L into the deposit currency), else the symbol's
        `trade_tick_value_loss` (or `trade_tick_value`) per `trade_tick_size`, both per lot in the deposit currency.
        None when neither gives a finite positive figure, so the gate refuses entries instead of guessing."""
        d = abs(float(distance))
        if d <= 0:
            return None
        action = mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL
        try:
            p = mt5.order_calc_profit(action, symbol, 1.0, float(price), float(price) - (1 if side > 0 else -1) * d)
            loss = -float(p) if p is not None else math.nan
            if math.isfinite(loss) and loss > 0:
                return loss
            log.warning("order_calc_profit(%s) gave %r: %s; trying tick value", symbol, p, mt5.last_error())
        except Exception as exc:                       # terminal fault: try the tick value, never fail open
            log.warning("order_calc_profit(%s) failed (%s); trying tick value", symbol, exc)
        try:
            i = mt5.symbol_info(symbol)
            size = float(i.trade_tick_size)
            value = float(getattr(i, "trade_tick_value_loss", 0.0) or 0.0)
            if not (math.isfinite(value) and value > 0):
                value = float(i.trade_tick_value)
        except Exception as exc:
            log.warning("symbol_info(%s) tick value unreadable (%s): no conversion", symbol, exc)
            return None
        if math.isfinite(size) and size > 0 and math.isfinite(value) and value > 0:
            return d / size * value
        log.warning("%s: no usable tick value (%r / %r): no conversion", symbol, value, size)
        return None

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        """The broker's documented hours for the week from today's server date, resolved to UTC per date (the Python
        package exposes no session schedule: see the module docstring)."""
        if not self._sessions_noted:
            log.info("%s: MetaTrader5 Python exposes no session schedule; using the broker's documented hours", symbol)
            self._sessions_noted = True
        today = server_wall(pd.Timestamp.now(tz="UTC"), self.server_tz)[0].date()
        return resolve_server_sessions(SessionTable(server_tz=self.server_tz).server_sessions(), self.server_tz, today)

    # ------------------------------------------------------------------ orders
    def _filling(self, symbol: str) -> int:
        """The fill mode `symbol` itself allows (IOC, else FOK, else RETURN): an order or close of another symbol (the
        kill switch closes every symbol, BACKLOG 62) never borrows the engine symbol's mode."""
        modes = self.symbol_info(symbol).filling_modes
        for pref, const in (("IOC", "ORDER_FILLING_IOC"), ("FOK", "ORDER_FILLING_FOK"), ("RETURN", "ORDER_FILLING_RETURN")):
            if pref in modes:
                return getattr(mt5, const)
        return mt5.ORDER_FILLING_RETURN

    def _account_refusal(self) -> str | None:
        """Why the terminal must not take an entry for this account now (`verify`), or None. Fail closed: an account
        that cannot be read, or a check that raises, is "unverifiable"."""
        if self._verify is None:
            return None
        try:                                           # account_info() None raises in account(): unverifiable
            return self._verify(self.account())
        except Exception as exc:                       # noqa: BLE001 - an IPC error or a malformed record: no send
            return f"unverifiable: {type(exc).__name__}"

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if (why := self._account_refusal()) is not None:
            log.warning("order %s refused: %s: %s", intent.client_order_id, ACCOUNT_REFUSAL, why)
            return OrderResult(ok=False, retcode=RETCODE_REJECT, order_id=None, position_id=None, filled_lots=0.0,
                               price=None, message=f"{ACCOUNT_REFUSAL}: {why}")
        if len(intent.comment) > MT5_COMMENT_MAX:
            # never cut it here: the caller's slice (runner, smoke: the id's tail) is the key every look-up matches, and
            # a head cut would file the position under the wrong key; a comment this long is a caller bug, refused
            # pre-send as a definite reject (trading-safety review LOW 4)
            log.warning("order %s refused: comment of %d characters exceeds the terminal's %d", intent.client_order_id,
                        len(intent.comment), MT5_COMMENT_MAX)
            return _precheck_failed(f"comment of {len(intent.comment)} characters exceeds MT5_COMMENT_MAX {MT5_COMMENT_MAX}")
        tick = mt5.symbol_info_tick(intent.symbol)
        price = tick.ask if intent.side > 0 else tick.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": intent.symbol, "volume": intent.lots,
            "type": mt5.ORDER_TYPE_BUY if intent.side > 0 else mt5.ORDER_TYPE_SELL, "price": price,
            "sl": intent.sl, "tp": intent.tp, "deviation": intent.deviation_points, "magic": intent.magic,
            "comment": intent.comment, "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(intent.symbol),
        }
        for attempt in range(3):
            chk = mt5.order_check(req)
            # nothing is sent when the check fails: a definite reject (trading-safety ruling, incident 2026-10-20)
            if chk is None:                            # MT5 says why only through last_error
                err = mt5.last_error()
                log.warning("order %s: order_check returned None: %s; request types: %s", intent.client_order_id,
                            err, _request_types(req))
                return _precheck_failed(f"order_check: none: {err}")
            if chk.retcode not in (0, RETCODE_DONE):
                why = f"order_check: {getattr(chk, 'comment', '')} (retcode {chk.retcode})"
                if chk.retcode in DEFINITE_REJECT_RETCODES:   # the broker's own code words the card (10027, 10019..)
                    return OrderResult(ok=False, retcode=chk.retcode, order_id=None, position_id=None,
                                       filled_lots=0.0, price=None, message=why)
                return _precheck_failed(why)
            res = mt5.order_send(req)
            if res is not None and res.retcode == RETCODE_DONE:
                return OrderResult(ok=True, retcode=res.retcode, order_id=res.order, position_id=_position_ticket(res), filled_lots=res.volume, price=res.price, message=res.comment)
            if res is not None and res.retcode in (RETCODE_REQUOTE, RETCODE_REJECT) and attempt < 2:
                time.sleep(0.5)
                tick = mt5.symbol_info_tick(intent.symbol)
                req["price"] = tick.ask if intent.side > 0 else tick.bid
                continue
            return OrderResult(ok=False, retcode=getattr(res, "retcode", -1), order_id=None, position_id=None, filled_lots=0.0, price=None, message=getattr(res, "comment", "send failed"))
        return OrderResult(ok=False, retcode=-1, order_id=None, position_id=None, filled_lots=0.0, price=None, message="FAILED_EXEC")

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        pos = [p for p in _positions_get() if p.ticket == position_id]
        if not pos:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=position_id, filled_lots=0.0, price=None, message="no such position")
        p = pos[0]
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": position_id,
               "sl": sl if sl is not None else p.sl, "tp": tp if tp is not None else p.tp}
        res = mt5.order_send(req)
        if res is None:
            return _send_failed(position_id)
        return OrderResult(ok=res.retcode == RETCODE_DONE, retcode=res.retcode, order_id=None, position_id=position_id, filled_lots=p.volume, price=None, message=res.comment)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        """Close a position of any symbol at market (design: "12%: close everything at market immediately"; BACKLOG 62)
        with that symbol's own quote and fill mode. The symbol is selected into Market Watch before its tick is read
        (a manual trade's symbol may not be shown); no symbol info or no tick is a refused close (10021, no quotes),
        never a raise, so the engine retries it and one symbol never stops the others. A full close sends the
        position's own volume as the terminal reports it (no rounding); a partial close of another symbol is never sent
        (the engine scales out only its own symbol, whose volume step it knows)."""
        pos = [p for p in _positions_get() if p.ticket == position_id]
        if not pos:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=position_id, filled_lots=0.0, price=None, message="no such position")
        p = pos[0]
        if lots is not None and p.symbol != self.symbol:
            return OrderResult(ok=False, retcode=RETCODE_INVALID_VOLUME, order_id=None, position_id=position_id,
                               filled_lots=0.0, price=None, message=f"partial close of {p.symbol} not sent")
        mt5.symbol_select(p.symbol, True)
        info, tick = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
        if info is None or tick is None:
            missing = "symbol info" if info is None else "tick"
            if p.symbol not in self._unquoted:              # once per streak: the engine retries it with a backoff
                self._unquoted.add(p.symbol)
                log.warning("close of position %s refused: no %s for %s (%s); retried", position_id, missing,
                            p.symbol, mt5.last_error())
            return OrderResult(ok=False, retcode=RETCODE_PRICE_OFF, order_id=None, position_id=position_id,
                               filled_lots=0.0, price=None, message=f"no {missing} for {p.symbol}")
        self._unquoted.discard(p.symbol)
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "position": position_id,
               "volume": lots or p.volume, "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "price": tick.bid if is_buy else tick.ask, "deviation": 30, "magic": p.magic,
               "comment": "close", "type_filling": self._filling(p.symbol)}
        res = mt5.order_send(req)
        if res is None:
            return _send_failed(position_id)
        return OrderResult(ok=res.retcode == RETCODE_DONE, retcode=res.retcode, order_id=res.order, position_id=position_id, filled_lots=res.volume, price=res.price, message=res.comment)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return [self._position(p) for p in _positions_get(symbol=self.symbol)
                if magic_prefix is None or str(p.magic).startswith(str(magic_prefix))]

    def account_positions(self) -> list[Position]:
        """Every position on the account, any symbol (positions_get without a filter): the equity cross-check
        (ADR 0016) compares the equity with the balance plus all of their profit and swap. Read-only."""
        return [self._position(p) for p in _positions_get()]

    def _position(self, p: Any) -> Position:
        ts = server_to_utc(pd.Series(pd.to_datetime([p.time_msc], unit="ms")), self.server_tz)[0]
        swap = getattr(p, "swap", None)
        return Position(position_id=p.ticket, symbol=p.symbol, side=1 if p.type == mt5.POSITION_TYPE_BUY else -1,
                        lots=p.volume, open_price=p.price_open, sl=p.sl or None, tp=p.tp or None, magic=p.magic,
                        comment=p.comment, open_time_utc=ts, profit=p.profit,
                        swap=None if swap is None else float(swap))

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        until = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
        deals = mt5.history_deals_get(self._mt5_time(since_utc), self._mt5_time(until))
        if deals is None:             # an error, not an empty history: never read as "no deals" (no fake closes)
            raise RuntimeError(f"history_deals_get failed: {mt5.last_error()}")
        df = pd.DataFrame([d._asdict() for d in deals]) if deals else pd.DataFrame()
        if not df.empty:
            df["ts_utc"] = server_to_utc(pd.to_datetime(df["time_msc"], unit="ms"), self.server_tz)
        return df

    def broker_terms(self, account_id: str, since_utc: pd.Timestamp, now: pd.Timestamp) -> BrokerTerms:
        """Swap and commission as this terminal reports them (see the module docstring for the conversions). The
        account conversion is the gate's: the broker's loss on one lot over $1 (`loss_per_lot`) per contract ounce."""
        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        price = (float(tick.bid) + float(tick.ask)) / 2 if tick is not None else 0.0
        return measure_broker_terms(account_id, info, account_currency=str(mt5.account_info().currency), price=price,
                                    deals=self.deals_since(since_utc), now=now,
                                    acct_per_usd=self._acct_per_usd(info, price))

    def _acct_per_usd(self, info: Any, price: float) -> float | None:
        """Account currency per USD of XAUUSD P&L for the cost terms; None when the terminal cannot say (the terms then
        keep the settings values), never a guess and never a failed reading."""
        try:
            contract = float(info.trade_contract_size)
            loss = self.loss_per_lot(self.symbol, 1, price, 1.0) if price > 0 else None
        except Exception as exc:                                    # noqa: BLE001 - a terminal fault: no conversion
            log.warning("%s: no account conversion for the cost terms (%s)", self.symbol, exc)
            return None
        return loss / contract if loss is not None and contract > 0 else None

    def shutdown(self) -> None:  # pragma: no cover
        mt5.shutdown()

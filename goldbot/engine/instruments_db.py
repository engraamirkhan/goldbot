"""state/instruments.db: each non-gold instrument's lot and swap terms per (account, broker symbol).

Multi-instrument demo plan (2026-11-02, lane L3): "costs/swap/lot terms per (account, symbol) in state/instruments.db
(SQLite WAL)". The engine writes a row from the terminal's symbol_info for every non-gold book (`refresh`, on the
broker-terms cadence) and reads it back before its entries (`load`). The rules:

* Gold never reads or writes it: XAUUSD keeps costs_<account>.json and broker_terms_<account>.json ("XAUUSD paths
  byte-identical").
* Swap is money in the symbol's profit currency per lot per night, broker sign, converted from symbol_info only where
  the unit is known: mode 0 (disabled) is 0, mode 1 (points) is swap x point x contract size, modes 2 (base currency)
  and 3 (margin currency) as is when that currency is the profit currency (index CFDs: incident 2026-11-02) and x
  price when it is the base currency of a symbol quoted in the profit currency; modes 5 and 6 (interest: annual % of
  the current or the open price, 360-day bank year; Vantage BTCUSD, refusals 2026-11-10) are rate / 100 / 360 x price
  x contract size, the open price of an entry being the current one. Any other mode or currency is stored NULL, and a NULL swap refuses that symbol's entries (`swap_unknown`): a cost the engine cannot price is never 0.
* Fail closed: a file that cannot be opened, migrated or read (corrupt, a newer schema, a full disk) raises
  `InstrumentsDbError`; the engine refuses that book's entries (`instruments_db_unreadable`) and every exit still
  runs, since no exit reads this file.
* `instrument_sessions` (schema 2, additive; incident 2026-11-02): each non-gold book's broker session table, a 24 h
  cache read and written only by goldbot/engine/session_cache.py. A cache, not state: an unreadable row costs a read.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp, load_record
from goldbot.db.connection import Database, StateDbError, db_path

MT5_SWAP_DISABLED, MT5_SWAP_POINTS, MT5_SWAP_BASE_CCY, MT5_SWAP_MARGIN_CCY = 0, 1, 2, 3
MT5_SWAP_INTEREST_CURRENT, MT5_SWAP_INTEREST_OPEN = 5, 6   # annual % of the current / the open price
INTEREST_DAYS = 360                                         # MT5: "standard bank year is 360 days"
_COLUMNS = ("account_id", "symbol", "store_symbol", "contract_size", "digits", "point", "volume_min", "volume_step",
            "volume_max", "currency_profit", "swap_long_per_lot", "swap_short_per_lot", "swap_triple_weekday",
            "swap_note", "updated_utc")


class InstrumentsDbError(RuntimeError):
    """instruments.db cannot be opened or read: the books that need it refuse entries (exits never read it)."""


class InstrumentTerms(FrozenRecord):
    account_id: str
    symbol: str                                  # the broker's symbol
    store_symbol: str
    contract_size: float = Field(gt=0)
    digits: int = Field(ge=0)
    point: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    currency_profit: str | None = None
    swap_long_per_lot: float | None = None       # profit currency per lot per night, broker sign; None = unknown
    swap_short_per_lot: float | None = None
    swap_triple_weekday: int | None = Field(None, ge=0, le=6)   # MT5 numbering: 0 = Sunday
    swap_note: str = ""
    updated_utc: UtcTimestamp


def _ccy(info: Any, field: str) -> str | None:
    v = getattr(info, field, None)
    return str(v).strip().upper() or None if v is not None else None


MT5_CALC_FOREX = 0                  # SYMBOL_CALC_MODE_FOREX
PRICE_IS_A_RATE_GROUPS = frozenset({"fx", "metals"})   # registry groups whose price is profit ccy per unit of base


def swap_per_lot(info: Any, price: float | None = None,
                 group: str | None = None) -> tuple[float | None, float | None, str]:
    """(long, short, note): symbol_info's swap as profit currency per lot per night, broker sign; (None, None, why)
    for a mode or currency this rule does not convert. `price`: the symbol's price (profit currency per unit of its
    base), needed only for a mode 2/3 amount in a base currency other than the profit currency. That "x price" holds
    only for a pair quoted as base/profit (trading-safety review of #319, F5): MT5 calc mode FOREX, or registry
    `group` fx or metals. For an index or a commodity the price is not an exchange rate: unknown (entries refused).
    Modes 5/6 (interest, Vantage BTCUSD 2026-11-10): "annual interest from the instrument price (current / position
    open price), standard bank year 360 days" -> rate / 100 / 360 x price x contract size, in the profit currency
    (the price is quoted in it), broker sign kept; an entry opens at the current price, so 6 is priced as 5. The price
    is the latest tick at the terms refresh (point in time) and must be finite and > 0, else unknown. BACKLOG 154: any
    reported profit currency is priced, the amount staying in it like every other mode here; the RiskGate converts it
    with the point-in-time `acct_per_profit` (`books.swap_spec`, `gate.profit_fx`; unknown refuses the entry). An
    unreported profit currency is unknown (entries refused)."""
    mode = getattr(info, "swap_mode", None)
    lng, sht = getattr(info, "swap_long", None), getattr(info, "swap_short", None)
    if mode is None or lng is None or sht is None:
        return None, None, "swap not reported by the terminal"
    if int(mode) == MT5_SWAP_DISABLED:
        return 0.0, 0.0, "swap mode disabled"
    if int(mode) == MT5_SWAP_POINTS:
        k = float(info.point) * float(info.contract_size)
        return float(lng) * k, float(sht) * k, "swap mode points: x point x contract size"
    if int(mode) in (MT5_SWAP_INTEREST_CURRENT, MT5_SWAP_INTEREST_OPEN):
        profit = _ccy(info, "currency_profit")
        if profit is None:
            return None, None, (f"swap mode {int(mode)} (interest) without a profit currency: "
                                "entries refused (swap_unknown)")
        if price is None or not math.isfinite(price) or price <= 0:
            return None, None, f"swap mode {int(mode)} (interest) without a price: entries refused (swap_unknown)"
        k = float(price) * float(info.contract_size) / 100.0 / INTEREST_DAYS
        return float(lng) * k, float(sht) * k, \
            f"swap mode {int(mode)}: annual % x price {float(price):g} x contract size / {INTEREST_DAYS} in {profit}"
    if int(mode) in (MT5_SWAP_BASE_CCY, MT5_SWAP_MARGIN_CCY):
        unit = _ccy(info, "currency_base" if int(mode) == MT5_SWAP_BASE_CCY else "currency_margin")
        profit, base = _ccy(info, "currency_profit"), _ccy(info, "currency_base")
        if unit is not None and unit == profit:
            return float(lng), float(sht), f"swap mode {int(mode)}: in the profit currency {profit}"
        rate = getattr(info, "trade_calc_mode", None) == MT5_CALC_FOREX or group in PRICE_IS_A_RATE_GROUPS
        if rate and unit is not None and unit == base and profit is not None and price is not None \
                and math.isfinite(price) and price > 0:
            return float(lng) * price, float(sht) * price, f"swap mode {int(mode)}: {unit} x price into {profit}"
    return None, None, f"swap mode {int(mode)} not converted: entries refused (swap_unknown)"


def terms_from_info(account_id: str, symbol: str, store_symbol: str, info: Any, now: pd.Timestamp,
                    price: float | None = None, group: str | None = None) -> InstrumentTerms:
    lng, sht, note = swap_per_lot(info, price, group)
    triple = getattr(info, "swap_rollover3days", None)
    return InstrumentTerms(account_id=account_id, symbol=symbol, store_symbol=store_symbol,
                           contract_size=float(info.contract_size), digits=int(info.digits), point=float(info.point),
                           volume_min=float(info.volume_min), volume_step=float(info.volume_step),
                           volume_max=float(info.volume_max), currency_profit=getattr(info, "currency_profit", None),
                           swap_long_per_lot=lng, swap_short_per_lot=sht,
                           swap_triple_weekday=None if triple is None else int(triple), swap_note=note,
                           updated_utc=now)


class InstrumentsDb:
    """<state_dir>/instruments.db. Opened (and migrated) on first use; every failure is `InstrumentsDbError`."""

    def __init__(self, state_dir: str | Path):
        self.path = db_path(state_dir, "instruments")
        self._db: Database | None = None

    def _database(self) -> Database:
        if self._db is None:
            try:
                self._db = Database(self.path, "instruments")
            except (sqlite3.Error, StateDbError, OSError) as exc:
                raise InstrumentsDbError(f"instruments.db unusable ({type(exc).__name__})") from exc
        return self._db

    def save(self, t: InstrumentTerms) -> None:
        row = t.model_dump(mode="json")
        marks = ", ".join("?" * len(_COLUMNS))
        try:
            with self._database().write() as conn:
                conn.execute(f"INSERT OR REPLACE INTO instrument_terms ({', '.join(_COLUMNS)}) VALUES ({marks})",
                             tuple(row[c] for c in _COLUMNS))
        except (sqlite3.Error, StateDbError, OSError) as exc:
            raise InstrumentsDbError(f"instruments.db not written ({type(exc).__name__})") from exc

    def save_sessions(self, account_id: str, symbol: str, read_utc: pd.Timestamp, body: dict[str, Any]) -> None:
        """The book's session table as read from the broker (`calendar.session_table_cache`), for the 24 h cache."""
        try:
            with self._database().write() as conn:
                conn.execute("INSERT OR REPLACE INTO instrument_sessions (account_id, symbol, read_utc, body) "
                             "VALUES (?, ?, ?, ?)", (account_id, symbol, pd.Timestamp(read_utc).isoformat(),
                                                     json.dumps(body, sort_keys=True)))
        except (sqlite3.Error, StateDbError, OSError, TypeError, ValueError) as exc:
            raise InstrumentsDbError(f"instruments.db sessions not written ({type(exc).__name__})") from exc

    def load_sessions(self, account_id: str, symbol: str) -> tuple[pd.Timestamp, dict[str, Any]] | None:
        """(read_utc, body) of the cached session table, or None; raises InstrumentsDbError when unreadable."""
        try:
            with self._database().connection() as conn:
                got = conn.execute("SELECT read_utc, body FROM instrument_sessions WHERE account_id = ? AND "
                                   "symbol = ?", (account_id, symbol)).fetchone()
            if got is None:
                return None
            read_utc, body = pd.Timestamp(got[0]), json.loads(got[1])
        except (sqlite3.Error, StateDbError, OSError, TypeError, ValueError) as exc:
            raise InstrumentsDbError(f"instruments.db sessions unreadable ({type(exc).__name__})") from exc
        if read_utc.tzinfo is None or not isinstance(body, dict):
            raise InstrumentsDbError("instruments.db sessions row invalid")
        return read_utc, body

    def load(self, account_id: str, symbol: str) -> InstrumentTerms | None:
        """The row, or None when there is none yet; raises InstrumentsDbError when the file cannot be read."""
        try:
            with self._database().connection() as conn:
                got = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM instrument_terms WHERE account_id = ? AND "
                                   "symbol = ?", (account_id, symbol)).fetchone()
        except (sqlite3.Error, StateDbError, OSError) as exc:
            raise InstrumentsDbError(f"instruments.db unreadable ({type(exc).__name__})") from exc
        if got is None:
            return None
        try:
            return load_record(InstrumentTerms, dict(zip(_COLUMNS, got)), source="instruments.db")
        except ValueError as exc:
            raise InstrumentsDbError(f"instruments.db row for {symbol} invalid ({type(exc).__name__})") from exc

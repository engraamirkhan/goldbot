"""state/instruments.db: each non-gold instrument's lot and swap terms per (account, broker symbol).

Multi-instrument demo plan (2026-11-02, lane L3): "costs/swap/lot terms per (account, symbol) in state/instruments.db
(SQLite WAL)". The engine writes a row from the terminal's symbol_info for every non-gold book (`refresh`, on the
broker-terms cadence) and reads it back before its entries (`load`). The rules:

* Gold never reads or writes it: XAUUSD keeps costs_<account>.json and broker_terms_<account>.json ("XAUUSD paths
  byte-identical").
* Swap is money in the symbol's profit currency per lot per night, broker sign, converted from symbol_info only where
  the unit is known: mode 0 (disabled) is 0, mode 1 (points) is swap x point x contract size, modes 2 (base currency)
  and 3 (margin currency) as is when that currency is the profit currency (index CFDs: incident 2026-11-02) and x
  price when it is the base currency of a symbol quoted in the profit currency. Any other mode or currency is stored
  NULL, and a NULL swap refuses that symbol's entries (`swap_unknown`): a cost the engine cannot price is never 0.
* Fail closed: a file that cannot be opened, migrated or read (corrupt, a newer schema, a full disk) raises
  `InstrumentsDbError`; the engine refuses that book's entries (`instruments_db_unreadable`) and every exit still
  runs, since no exit reads this file.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp, load_record
from goldbot.db.connection import Database, StateDbError, db_path

MT5_SWAP_DISABLED, MT5_SWAP_POINTS, MT5_SWAP_BASE_CCY, MT5_SWAP_MARGIN_CCY = 0, 1, 2, 3
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


def swap_per_lot(info: Any, price: float | None = None) -> tuple[float | None, float | None, str]:
    """(long, short, note): symbol_info's swap as profit currency per lot per night, broker sign; (None, None, why)
    for a mode or currency this rule does not convert. `price`: the symbol's price (profit currency per unit of its
    base), needed only for a mode 2/3 amount in a base currency other than the profit currency."""
    mode = getattr(info, "swap_mode", None)
    lng, sht = getattr(info, "swap_long", None), getattr(info, "swap_short", None)
    if mode is None or lng is None or sht is None:
        return None, None, "swap not reported by the terminal"
    if int(mode) == MT5_SWAP_DISABLED:
        return 0.0, 0.0, "swap mode disabled"
    if int(mode) == MT5_SWAP_POINTS:
        k = float(info.point) * float(info.contract_size)
        return float(lng) * k, float(sht) * k, "swap mode points: x point x contract size"
    if int(mode) in (MT5_SWAP_BASE_CCY, MT5_SWAP_MARGIN_CCY):
        unit = _ccy(info, "currency_base" if int(mode) == MT5_SWAP_BASE_CCY else "currency_margin")
        profit, base = _ccy(info, "currency_profit"), _ccy(info, "currency_base")
        if unit is not None and unit == profit:
            return float(lng), float(sht), f"swap mode {int(mode)}: in the profit currency {profit}"
        if unit is not None and unit == base and profit is not None and price is not None and math.isfinite(price) \
                and price > 0:
            return float(lng) * price, float(sht) * price, f"swap mode {int(mode)}: {unit} x price into {profit}"
    return None, None, f"swap mode {int(mode)} not converted: entries refused (swap_unknown)"


def terms_from_info(account_id: str, symbol: str, store_symbol: str, info: Any, now: pd.Timestamp,
                    price: float | None = None) -> InstrumentTerms:
    lng, sht, note = swap_per_lot(info, price)
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

"""The engine's round-trip cost (`Engine._cost_atr`, moved out of runner.py) and the versioned engine cost of record
it reads first (finance audit 2026-11-20 N10 / P5; `goldbot.research.realised_costs`; ADR: engine cost of record from
realised fills).

Order, per decision: the cell of the version settings name (`costs.engine_record` -> `EngineConfig.cost_record`) for
this account x session x timeframe (the timeframe's own cell, else the account x session pooled cell), gold's book
only; else the nightly legacy table (state/costs_<account>.json, `CostTable`); else the configured `cost_atr`. Every
answer keeps the floor of the live spread plus commission (`commission.floor_cost`, ADR 0075). A record that cannot be
read is logged once and treated as absent: the legacy table charges (it is the dearer basis, so a broken file can never
cheapen a decision). The version a decision was charged on (`version`) is stamped on its exploration row, so a later
read prices each row on its own basis (K / holdout honesty)."""
from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING

import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.engine import commission
from goldbot.research.realised_costs import CellCost, EngineCostRecord

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger("goldbot.engine")
FALLBACK = "fallback"            # neither a record cell nor a legacy table: the configured cost_atr


@functools.lru_cache(maxsize=8)
def _load(path: str) -> EngineCostRecord | None:
    """One version's record (immutable files: cached by path for the process); None, logged once, when unreadable."""
    try:
        return EngineCostRecord.load(path)
    except Exception as exc:     # noqa: BLE001 - any unreadable record falls back to the dearer legacy table
        log.error("engine cost of record %s unreadable (%s): the legacy cost table charges", path, type(exc).__name__)
        return None


def _session(ts: pd.Timestamp) -> str:
    return str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([ts]))[0])


def cell(eng: Engine, ts: pd.Timestamp, tf: str | None) -> tuple[EngineCostRecord, CellCost] | None:
    """The record and its cell for this decision; None off gold's book, without a record or without a cell."""
    path = eng.cfg.cost_record
    # a live (real-money) account always keeps the legacy table, whatever hosts a record version lists (safety review)
    if not path or eng.book is not eng.primary or eng.cfg.mode == "live":
        return None
    rec = _load(str(path))
    c = rec.cell(eng.cfg.account_id, _session(ts), tf) if rec is not None else None
    return (rec, c) if rec is not None and c is not None else None


def cost_atr(eng: Engine, atr_usd: float, ts: pd.Timestamp, ex_spread: bool = False, tf: str | None = None) -> float:
    """Round-trip cost in ATR (`Engine._cost_atr`). ex_spread: slippage and commission only, for the probability
    threshold and size multiplier (the model's p is trained on labels that already pay the spread): from a record cell
    its charge less its mean spread, never below its commission; the fallback stays the full configured cost."""
    found = cell(eng, ts, tf)
    c: float | None
    if found is not None and atr_usd > 0:
        cc = found[1]
        usd = max(cc.charged_usd - cc.spread_usd, cc.commission_usd) if ex_spread else cc.charged_usd
        c = usd / atr_usd
    else:
        table = eng._cost_table()
        if table is None:
            c = eng.cfg.cost_atr
        else:
            session = _session(ts)
            c = table.round_trip_ex_spread_atr(session, atr_usd) if ex_spread else table.round_trip_atr(session, atr_usd)
    return commission.floor_cost(eng, c if c is not None else eng.cfg.cost_atr, atr_usd, ex_spread)


def version(eng: Engine, ts: pd.Timestamp, tf: str | None) -> str:
    """The basis `cost_atr` charges this decision on: the record's version, `table:<built day>` for the legacy
    table, or `fallback` for the configured cost."""
    found = cell(eng, ts, tf)
    if found is not None:
        return found[0].version
    table = eng._cost_table()
    return f"table:{table.built_utc:%Y-%m-%d}" if table is not None else FALLBACK

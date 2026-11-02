"""`GET /api/key-levels`: each account's current key zones and the recent bars of the chart's timeframe (owner
2026-11-02: "identify key levels, support and resistance on all time frames"). Read-only: nothing here writes, sizes
or orders.

* Zones: engine_<account>.json `key_zones` (goldbot/engine/key_zones.py: at most 3 strong zones above and below at the
  last 15m close). An engine without them, or with a book this release cannot read, is left out (logged), never a
  failed view. `stale` when they are over STALE_AFTER old.
* Bars: the last N_BARS bars of the timeframe for the asked account (else the first with zones), from its broker's
  minutes, else the reference feed (trade_chart.recent_bars, the trade chart's feeds).
* Another instrument (`symbol`, multi-instrument demo; UI spec 2026-11-02 "round zones hidden off gold"): its zones
  from engine_<account>.json `key_zones_by_symbol[<broker or instrument symbol>]` (same shape, lane L3), its own bars
  (never the gold reference feed), and a round-number zone (tfs ["round"]: gold's $50/$100) dropped. An account
  without that book is left out, so the dashboard says "No levels yet for X".
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pydantic import ValidationError

from goldbot.api import trade_chart
from goldbot.api.instruments_view import GOLD, instrument_key
from goldbot.api.schema import AccountKeyLevels, KeyLevelsView, KeyLevelTf
from goldbot.base import load_record

log = logging.getLogger(__name__)

__all__ = ["N_BARS", "STALE_AFTER", "key_levels_view"]

N_BARS = 120                        # about a day and a quarter of 15m bars, five days of 1h, four months of 1d
STALE_AFTER = pd.Timedelta(minutes=30)   # zones are rebuilt every 15m close: two missed builds read stale


def _book(e: dict[str, Any], symbol: str) -> Any:
    if symbol == GOLD:
        return e.get("key_zones")
    by = e.get("key_zones_by_symbol")
    if not isinstance(by, dict):
        return None
    return next((v for k, v in by.items() if instrument_key(k) == symbol), None)


def _account(e: dict[str, Any], now: pd.Timestamp, symbol: str = GOLD) -> AccountKeyLevels | None:
    kz = _book(e, symbol)
    if not isinstance(kz, dict):
        return None
    try:
        acc = load_record(AccountKeyLevels, {"account_id": str(e.get("account")), **{
            k: kz[k] for k in ("as_of", "price", "atr_1d", "history_days", "pct_ready", "zones_total", "above",
                               "below") if k in kz}}, source="engine_state.key_zones")
        update: dict[str, Any] = {"stale": now - pd.Timestamp(acc.as_of) > STALE_AFTER, "symbol": symbol}
        if symbol != GOLD:                   # gold's round numbers mean nothing on another instrument
            update["above"] = [z for z in acc.above if "round" not in z.tfs]
            update["below"] = [z for z in acc.below if "round" not in z.tfs]
        return acc.model_copy(update=update)
    except ValidationError:
        log.warning("key zones of %s unreadable: left out", e.get("account"))
        return None


def key_levels_view(engines: list[dict[str, Any]], store: Any, tf: KeyLevelTf, now: pd.Timestamp,
                    account: str | None = None, symbol: str = GOLD) -> KeyLevelsView:
    symbol = instrument_key(symbol)
    accounts = [a for a in (_account(e, now, symbol) for e in engines if isinstance(e, dict)) if a is not None]
    pick = next((a.account_id for a in accounts if a.account_id == account), None) or \
        (accounts[0].account_id if accounts else account)
    bars, feed = trade_chart.recent_bars(store, pick, tf, N_BARS, now, symbol) if store is not None else ([], None)
    return KeyLevelsView(timeframe=tf, account_id=pick, feed=feed, bars=bars, accounts=accounts, symbol=symbol)

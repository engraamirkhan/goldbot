"""`GET /api/key-levels`: each account's current key zones and the recent bars of the chart's timeframe (owner
2026-11-02: "identify key levels, support and resistance on all time frames"). Read-only: nothing here writes, sizes
or orders.

* Zones: engine_<account>.json `key_zones` (goldbot/engine/key_zones.py: at most 3 strong zones above and below at the
  last 15m close). An engine without them, or with a book this release cannot read, is left out (logged), never a
  failed view. `stale` when they are over STALE_AFTER old.
* Bars: the last N_BARS bars of the timeframe for the asked account (else the first with zones), from its broker's
  minutes, else the reference feed (trade_chart.recent_bars, the trade chart's feeds).
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pydantic import ValidationError

from goldbot.api import trade_chart
from goldbot.api.schema import AccountKeyLevels, KeyLevelsView, KeyLevelTf
from goldbot.base import load_record

log = logging.getLogger(__name__)

__all__ = ["N_BARS", "STALE_AFTER", "key_levels_view"]

N_BARS = 120                        # about a day and a quarter of 15m bars, five days of 1h, four months of 1d
STALE_AFTER = pd.Timedelta(minutes=30)   # zones are rebuilt every 15m close: two missed builds read stale


def _account(e: dict[str, Any], now: pd.Timestamp) -> AccountKeyLevels | None:
    kz = e.get("key_zones")
    if not isinstance(kz, dict):
        return None
    try:
        acc = load_record(AccountKeyLevels, {"account_id": str(e.get("account")), **{
            k: kz[k] for k in ("as_of", "price", "atr_1d", "history_days", "pct_ready", "zones_total", "above",
                               "below") if k in kz}}, source="engine_state.key_zones")
        return acc.model_copy(update={"stale": now - pd.Timestamp(acc.as_of) > STALE_AFTER})
    except ValidationError:
        log.warning("key zones of %s unreadable: left out", e.get("account"))
        return None


def key_levels_view(engines: list[dict[str, Any]], store: Any, tf: KeyLevelTf, now: pd.Timestamp,
                    account: str | None = None) -> KeyLevelsView:
    accounts = [a for a in (_account(e, now) for e in engines if isinstance(e, dict)) if a is not None]
    pick = next((a.account_id for a in accounts if a.account_id == account), None) or \
        (accounts[0].account_id if accounts else account)
    bars, feed = trade_chart.recent_bars(store, pick, tf, N_BARS, now) if store is not None else ([], None)
    return KeyLevelsView(timeframe=tf, account_id=pick, feed=feed, bars=bars, accounts=accounts)

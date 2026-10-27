"""One trade on its bars (docs/design/trades-ux-2026-10-27.md §3.3, item 133): `GET /api/trades/{trade_id}/bars`.
Read-only: nothing here writes, sizes or orders.

* Bars: the trade's decision timeframe (a cost probe, which has none, on 1m), from BARS_BEFORE bars before the entry's
  bar to BARS_AFTER after the exit's (to now while open, never past it). More than MAX_BARS steps up to the next
  coarser timeframe, so a long hold still fits one picture. Bid prices, as the broker's chart and a long's stop.
* Feed: the trade's own broker first (its M1 as the terminal reports it, `bars_1m_broker`; the engine's minutes from
  its ticks, `bars_1m_live`; `bars_1m` of the account), else the reference feed (Dukascopy) and the response says
  which. None stored: no bars and `missing` in words; the levels are still given (spec: "bars missing = 'Bars for
  this period aren't stored yet' with the levels still listed").
* Levels: the trade's own record (entry, the stop at entry, target, exit) and each stop move the engine journalled
  for its position (store `decisions`, action `trail`, accepted by the broker), in time order.
* The row comes from trades_ledger.detail: the trade's own result, whatever its arm's read (ADR 0050, 0053).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

import pandas as pd

from goldbot.api.schema import ChartBar, StopMove, TradeBars, TradeRow
from goldbot.config import tf_seconds
from goldbot.data.resample import BAR_COLUMNS, resample_bars

log = logging.getLogger(__name__)

__all__ = ["BARS_AFTER", "BARS_BEFORE", "MAX_BARS", "MISSING", "REFERENCE", "trade_bars"]

BARS_BEFORE = 30                    # spec §3.3: "30 bars before entry to 5 after exit"
BARS_AFTER = 5
MAX_BARS = 400                      # past this the next coarser timeframe (a fixed-size SVG: ~1 px a candle on a phone)
LADDER = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
REFERENCE = "dukascopy"             # data/release.py load_into_store: the research feed
MISSING = "Bars for this period aren't stored yet"


def _timeframe(row: TradeRow, lo: pd.Timestamp, hi: pd.Timestamp) -> str:
    tf = row.timeframe if row.timeframe in LADDER else "1m"
    for cand in LADDER[LADDER.index(tf):]:
        tf = cand
        if (hi - lo).total_seconds() / tf_seconds(cand) + BARS_BEFORE + BARS_AFTER + 1 <= MAX_BARS:
            break
    return tf


def _window(row: TradeRow, tf: str, now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    step = pd.Timedelta(seconds=tf_seconds(tf))
    opened = pd.Timestamp(row.opened_utc)
    end = pd.Timestamp(row.exit_utc) if row.exit_utc is not None else now
    lo = opened.floor(step) - BARS_BEFORE * step if tf != "1d" else opened.floor("D") - BARS_BEFORE * step
    hi = end.floor(step) + (BARS_AFTER + 1) * step if tf != "1d" else end.floor("D") + (BARS_AFTER + 1) * step
    return lo, min(hi, now.ceil("min"))


def _broker(account: str) -> str | None:
    try:
        from goldbot.ops.accounts import load_accounts
        acc = load_accounts(logins=False).get(account)
        return acc.broker if acc is not None else None
    except Exception:                      # noqa: BLE001 - the engine's own minutes are one of several feeds
        return None


def _sources(account: str | None) -> list[tuple[str, str, Literal["broker", "reference"]]]:
    out: list[tuple[str, str, Literal["broker", "reference"]]] = []
    if account:
        out.append(("bars_1m_broker", account, "broker"))
        broker = _broker(account)
        if broker:
            out.append(("bars_1m_live", broker, "broker"))
        out.append(("bars_1m", account, "broker"))
    out.append(("bars_1m", REFERENCE, "reference"))
    return out


def _bars(store: Any, account: str | None, tf: str, lo: pd.Timestamp,
          hi: pd.Timestamp) -> tuple[list[ChartBar], Literal["broker", "reference"] | None]:
    # a daily bar opens at the previous settlement (resample_bars): read a day either side
    pad = pd.Timedelta(days=1) if tf == "1d" else pd.Timedelta(0)
    for table, source, feed in _sources(account):
        try:
            m1 = store.read(table, source=source, start=lo - pad, end=hi + pad)
        except Exception as exc:          # noqa: BLE001 - one unreadable feed is skipped, never a failed chart
            log.warning("trade chart: %s/%s unreadable (%s)", table, source, type(exc).__name__)
            continue
        if m1 is None or m1.empty or not set(BAR_COLUMNS) <= set(m1.columns):
            continue
        m1 = m1.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")[BAR_COLUMNS]
        b = m1 if tf == "1m" else resample_bars(m1.reset_index(drop=True), tf)
        ts = pd.to_datetime(b["ts_utc"], utc=True)
        b = b[(ts >= lo) & (ts < hi)]
        if b.empty:
            continue
        return [ChartBar(t=t, o=round(o, 2), h=round(h, 2), l=round(lo_, 2), c=round(c, 2))
                for t, o, h, lo_, c in zip(pd.to_datetime(b["ts_utc"], utc=True), b["bid_open"], b["bid_high"],
                                           b["bid_low"], b["bid_close"], strict=True)], feed
    return [], None


def _stop_moves(store: Any, row: TradeRow, now: pd.Timestamp) -> list[StopMove]:
    """The accepted `trail` moves of the trade's position while it was open, in time order."""
    if row.account is None or row.position_id is None:
        return []
    end = pd.Timestamp(row.exit_utc) if row.exit_utc is not None else now
    try:
        df = store.read("decisions", source=row.account, start=pd.Timestamp(row.opened_utc), end=end + pd.Timedelta(minutes=1))
    except Exception as exc:              # noqa: BLE001 - the chart keeps its other levels
        log.warning("trade chart: decisions unreadable (%s)", type(exc).__name__)
        return []
    if df is None or df.empty or "action" not in df.columns:
        return []
    out = []
    for at, detail in zip(df.loc[df["action"] == "trail", "ts_utc"], df.loc[df["action"] == "trail", "detail"],
                          strict=True):
        try:
            d = json.loads(detail) if isinstance(detail, str) else {}
        except ValueError:
            continue
        if d.get("position") == row.position_id and d.get("ok") is True and isinstance(d.get("sl"), (int, float)):
            out.append(StopMove(at=pd.Timestamp(at).to_pydatetime(), price=float(d["sl"])))
    return sorted(out, key=lambda m: m.at)


def trade_bars(row: TradeRow, store: Any, now: pd.Timestamp) -> TradeBars:
    """The chart of one trade (module docstring). `row` is the ledger's detail row."""
    end = pd.Timestamp(row.exit_utc) if row.exit_utc is not None else now
    tf = _timeframe(row, pd.Timestamp(row.opened_utc), end)
    lo, hi = _window(row, tf, now)
    bars, feed = _bars(store, row.account, tf, lo, hi)
    return TradeBars(trade_id=row.trade_id, timeframe=tf, feed=feed, bars=bars, missing=None if bars else MISSING,
                     side=row.side, entry=row.entry, entry_utc=row.opened_utc, stop=row.stop,
                     stop_moves=_stop_moves(store, row, now), target=row.target, exit=row.exit, exit_utc=row.exit_utc)

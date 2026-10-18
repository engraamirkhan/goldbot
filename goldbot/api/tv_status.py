"""`GET /api/tradingview`: is every indicator alert still arriving? Read-only, built from the `tv_indicators` dataset.

Per indicator x timeframe: the last bar received, alerts (bars) in the last 24 h, the plots seen, and the gaps: bar
slots missing between consecutive received bars in the last 24 h (a market-closed stretch counts as missing slots; the
screen says so). Nothing here writes, and no TradingView value is shown, only the counts, so an invite-only
indicator's output is not exposed to a viewer.
"""
from __future__ import annotations

import pandas as pd

from goldbot.api.schema import TvIndicatorStatus, TvStatusView
from goldbot.data.store import Store
from goldbot.webhook.app import tf_seconds
from goldbot.webhook.inbox import TABLE

WINDOW = pd.Timedelta(hours=24)
HISTORY = pd.Timedelta(days=30)


def _gaps(bars: list[pd.Timestamp], sec: int) -> int:
    if sec <= 0 or len(bars) < 2:
        return 0
    steps = [(b - a).total_seconds() / sec for a, b in zip(bars, bars[1:])]
    return int(sum(max(round(s) - 1, 0) for s in steps))


def tv_status(store: Store, now: pd.Timestamp) -> TvStatusView:
    now = now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC")
    tdir = store.table_dir(TABLE)
    if not tdir.exists() or not any(tdir.rglob("*.parquet")):
        return TvStatusView(now=now.isoformat(), indicators=[])
    since = (now - HISTORY).to_pydatetime()
    df = store.sql(
        "SELECT source, symbol, tf, bar_time, plot, intrabar FROM tv_indicators "
        f"WHERE ts_utc >= TIMESTAMPTZ '{since.isoformat()}'")
    rows: list[TvIndicatorStatus] = []
    if df.empty:
        return TvStatusView(now=now.isoformat(), indicators=rows)
    df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True)
    for (source, symbol, tf), g in df[~df["intrabar"].astype(bool)].groupby(["source", "symbol", "tf"]):
        bars = sorted(g["bar_time"].unique())
        recent = [b for b in bars if b >= now - WINDOW]
        indicator, _, version = str(source).partition("@")
        rows.append(TvIndicatorStatus(
            indicator=indicator, version=version, symbol=str(symbol), tf=str(tf),
            last_bar_time=pd.Timestamp(bars[-1]).isoformat(), alerts_24h=len(recent),
            plots_seen=sorted(p for p in g["plot"].unique() if p), gaps_24h=_gaps(recent, tf_seconds(str(tf)))))
    rows.sort(key=lambda r: (r.indicator, r.tf))
    return TvStatusView(now=now.isoformat(), indicators=rows)

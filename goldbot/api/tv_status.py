"""`GET /api/tradingview`: is every indicator alert still arriving? Read-only, built from the `tv_indicators` dataset.

Per indicator x timeframe: the last bar received, alerts (bars) in the last 24 h, the plots seen, and the gaps: bar
slots missing between consecutive received bars in the last 24 h (a market-closed stretch counts as missing slots; the
screen says so). Chart-export (CSV) history is counted apart (`csv_bars`, `csv_rows`) with its repaint status:
"unvalidated" until goldbot/research/tv_repaint.py has flagged every CSV row of that indicator x timeframe; it is never
counted as a live alert. Nothing here writes, and no TradingView value is shown, only the counts, so an invite-only
indicator's output is not exposed to a viewer.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd

from goldbot.api.schema import TvIndicatorStatus, TvStatusView
from goldbot.data.store import Store
from goldbot.webhook.app import tf_seconds
from goldbot.webhook.inbox import TABLE

WINDOW = pd.Timedelta(hours=24)
HISTORY = pd.Timedelta(days=30)
Status = Literal["none", "unvalidated", "validated"]
Key = tuple[str, str, str]


def _gaps(bars: list[pd.Timestamp], sec: int) -> int:
    if sec <= 0 or len(bars) < 2:
        return 0
    steps = [(b - a).total_seconds() / sec for a, b in zip(bars, bars[1:])]
    return int(sum(max(round(s) - 1, 0) for s in steps))


def _csv_counts(store: Store, cols: set[str]) -> dict[Key, tuple[int, int, Status]]:
    """(source, symbol, tf) -> (CSV bars, CSV plot rows, repaint status), over every CSV row ever imported."""
    if "origin" not in cols or "repaint_validated" not in cols:
        return {}
    df = store.sql(
        "SELECT source, symbol, tf, COUNT(DISTINCT bar_time) AS bars, COUNT(*) AS n, "
        "BOOL_AND(COALESCE(repaint_validated, false)) AS ok FROM tv_indicators WHERE origin = 'csv' "
        "GROUP BY source, symbol, tf")
    return {(str(src), str(sym), str(tf)): (int(bars), int(n), "validated" if bool(ok) else "unvalidated")
            for src, sym, tf, bars, n, ok in df.itertuples(index=False, name=None)}


def tv_status(store: Store, now: pd.Timestamp) -> TvStatusView:
    now = now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC")
    tdir = store.table_dir(TABLE)
    if not tdir.exists() or not any(tdir.rglob("*.parquet")):
        return TvStatusView(now=now.isoformat(), indicators=[])
    cols = set(store.sql("DESCRIBE SELECT * FROM tv_indicators")["column_name"])
    csv = _csv_counts(store, cols)
    since = (now - HISTORY).to_pydatetime()
    live_only = "origin IS NULL AND " if "origin" in cols else ""
    df = store.sql(
        "SELECT source, symbol, tf, bar_time, plot, intrabar FROM tv_indicators "
        f"WHERE {live_only}ts_utc >= TIMESTAMPTZ '{since.isoformat()}'")
    rows: list[TvIndicatorStatus] = []
    seen: set[Key] = set()
    if not df.empty:
        df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True)
        for (source, symbol, tf), g in df[~df["intrabar"].astype(bool)].groupby(["source", "symbol", "tf"]):
            key = (str(source), str(symbol), str(tf))
            seen.add(key)
            bars = sorted(g["bar_time"].unique())
            recent = [b for b in bars if b >= now - WINDOW]
            indicator, _, version = key[0].partition("@")
            nbars, nrows, status = csv.get(key, (0, 0, "none"))
            rows.append(TvIndicatorStatus(
                indicator=indicator, version=version, symbol=key[1], tf=key[2],
                last_bar_time=pd.Timestamp(bars[-1]).isoformat(), alerts_24h=len(recent),
                plots_seen=sorted(p for p in g["plot"].unique() if p), gaps_24h=_gaps(recent, tf_seconds(key[2])),
                csv_bars=nbars, csv_rows=nrows, repaint_status=status))
    for key, (nbars, nrows, status) in csv.items():
        if key not in seen:
            indicator, _, version = key[0].partition("@")
            rows.append(TvIndicatorStatus(
                indicator=indicator, version=version, symbol=key[1], tf=key[2], last_bar_time="", alerts_24h=0,
                plots_seen=[], gaps_24h=0, csv_bars=nbars, csv_rows=nrows, repaint_status=status))
    rows.sort(key=lambda r: (r.indicator, r.tf))
    return TvStatusView(now=now.isoformat(), indicators=rows)

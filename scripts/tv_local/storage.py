"""Parquet/JSON outputs under ~/tmp/tv (never the repo). Closed bars only.

Availability contract (what the as-of join may use):
* bars/plots from a history pull: `available_utc = bar_time + tf` (the bar's nominal close; an upper bound of the
  real close, so never early), `origin = "history_bar_close"`. A daily bar's real session close is earlier than
  open + 24 h, so this is conservative.
* a bar whose nominal close is after the receive time is still forming and is DROPPED (closed bars only).
* plots are TradingView's values computed now over history: a repainting script's past values may differ from what
  was visible live. They are marked `origin = "history_recomputed"` so research can treat them as such.
Every timestamp column is UTC; `*_ns` int64 twins come from goldbot.data.timeutil.epoch_ns (unit-safe).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.timeutil import epoch_ns

from .pull import TF_SECONDS, SeriesResult

OUT = Path.home() / "tmp" / "tv"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def _times(sec: pd.Series) -> pd.Series:
    return pd.to_datetime(sec.astype("float64") * 1e9, unit="ns", utc=True)


def closed_bars(res: SeriesResult) -> pd.DataFrame:
    tf_s = TF_SECONDS[res.tf]
    rows = sorted(res.bars.items())
    df = pd.DataFrame([[t, *v] for t, v in rows], columns=["t", "open", "high", "low", "close", "volume"])
    df = df[df["t"] + tf_s <= res.received_at].reset_index(drop=True)
    df.insert(0, "bar_time", _times(df.pop("t")))
    df["available_utc"] = df["bar_time"] + pd.Timedelta(seconds=tf_s)
    df["received_utc"] = pd.Timestamp(res.received_at, unit="s", tz="UTC")
    df["origin"] = "history_bar_close"
    df["symbol"] = res.symbol
    df["tf"] = res.tf
    df["source"] = f"tradingview:{res.symbol}"
    df["bar_time_ns"] = epoch_ns(df["bar_time"])
    df["available_utc_ns"] = epoch_ns(df["available_utc"])
    return df


def plots_long(res: SeriesResult) -> pd.DataFrame:
    tf_s = TF_SECONDS[res.tf]
    recs: list[tuple[Any, ...]] = []
    for st in res.studies:
        ids = [p for p in st.meta.plots]
        for t, vals in st.values.items():
            if t + tf_s > res.received_at or t not in res.bars:     # closed bars we hold only
                continue
            for k, v in enumerate(vals):
                pid = ids[k]["id"] if k < len(ids) else f"plot_{k}"
                title = ids[k]["title"] if k < len(ids) else pid
                recs.append((t, st.meta.pine_id, st.meta.pine_version, st.meta.short, pid, title, v))
    df = pd.DataFrame(recs, columns=["t", "pine_id", "pine_version", "study", "plot_id", "plot_title", "value"])
    df.insert(0, "bar_time", _times(df.pop("t")))
    df["available_utc"] = df["bar_time"] + pd.Timedelta(seconds=tf_s)
    df["received_utc"] = pd.Timestamp(res.received_at, unit="s", tz="UTC")
    df["origin"] = "history_recomputed"
    df["symbol"] = res.symbol
    df["tf"] = res.tf
    df["bar_time_ns"] = epoch_ns(df["bar_time"])
    df["available_utc_ns"] = epoch_ns(df["available_utc"])
    return df.sort_values(["pine_id", "plot_id", "bar_time"]).reset_index(drop=True)


def write(res: SeriesResult, out: Path = OUT, tag: str = "") -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    stem = _safe(f"{tag + '_' if tag else ''}{res.symbol}_{res.tf}")
    bars = closed_bars(res)
    bars.to_parquet(out / f"bars_{stem}.parquet", index=False)
    summary: dict[str, Any] = {"symbol": res.symbol, "tf": res.tf, "closed_bars": len(bars),
                               "raw_bars": len(res.bars), "requests": res.requests, "data_end": res.data_end,
                               "error": res.error,
                               "first": str(bars["bar_time"].iloc[0]) if len(bars) else None,
                               "last": str(bars["bar_time"].iloc[-1]) if len(bars) else None}
    if res.studies:
        plots = plots_long(res)
        plots.to_parquet(out / f"plots_{stem}.parquet", index=False)
        meta = [{**s.meta.public(), "study_error": s.error, "points": len(s.values), "study_loaded": s.loaded}
                for s in res.studies]
        (out / f"studies_{stem}.json").write_text(json.dumps(meta, indent=1, default=str))
        summary["studies"] = [{"pine_id": s.meta.pine_id, "points": len(s.values), "error": s.error}
                              for s in res.studies]
    return summary

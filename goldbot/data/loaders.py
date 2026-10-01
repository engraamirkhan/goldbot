"""Loaders for the two price sources. Both emit the same tick/bar schema, tagged by `source`.

* MT5 export (Mac interim path): View -> Symbols -> Bars -> Export. Server-time stamps, converted
  to UTC per bar with the broker's IANA zone. Only bid OHLC is exported, so ask is reconstructed
  as bid + spread (the export's `<SPREAD>` is in points; 1 point = 0.01 for XAUUSD on most brokers).
* Dukascopy via `dukascopy-node` CSV: `npx dukascopy-node -i xauusd -from 2003-05-04 -to 2026-10-01 -t tick -f csv`
  columns: timestamp(ms), askPrice, bidPrice, askVolume, bidVolume. Already UTC.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from goldbot.data.timeutil import server_to_utc


def load_mt5_bars_csv(path: str | Path, *, server_tz: str, point: float = 0.01) -> pd.DataFrame:
    """Parse an MT5 'Bars' export into 1m bars in the store schema."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip("<>").lower() for c in df.columns]
    if "time" in df.columns:
        stamp = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    else:
        stamp = pd.to_datetime(df["date"])
    ts = server_to_utc(stamp, server_tz)
    spread = df.get("spread", pd.Series(0, index=df.index)).astype(float) * point
    out = pd.DataFrame({
        "ts_utc": ts,
        "bid_open": df["open"].astype(float), "bid_high": df["high"].astype(float),
        "bid_low": df["low"].astype(float), "bid_close": df["close"].astype(float),
    })
    for c in ("open", "high", "low", "close"):
        out[f"ask_{c}"] = out[f"bid_{c}"] + spread
    out["tick_count"] = df.get("tickvol", df.get("vol", pd.Series(0, index=df.index))).astype(int)
    out["spread_mean"] = spread
    out["spread_max"] = spread
    out["visible_at"] = out["ts_utc"] + pd.Timedelta(minutes=1)
    out = out.dropna(subset=["ts_utc"]).sort_values("ts_utc").reset_index(drop=True)
    return out


def load_dukascopy_ticks_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    ts_col = cols.get("timestamp", cols.get("time"))
    ts = pd.to_datetime(df[ts_col], unit="ms", utc=True) if pd.api.types.is_numeric_dtype(df[ts_col]) \
        else pd.to_datetime(df[ts_col], utc=True)
    out = pd.DataFrame({
        "ts_utc": ts,
        "bid": df[cols.get("bidprice", cols.get("bid"))].astype(float),
        "ask": df[cols.get("askprice", cols.get("ask"))].astype(float),
    })
    return out.sort_values("ts_utc").reset_index(drop=True)


def load_generic_ticks_csv(path: str | Path, *, tz: str | None = None) -> pd.DataFrame:
    """Any CSV with columns time/bid/ask. `tz` given -> stamps are naive local in that zone."""
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    tcol = "ts_utc" if "ts_utc" in df.columns else "time"
    ts = server_to_utc(pd.to_datetime(df[tcol]), tz) if tz else pd.to_datetime(df[tcol], utc=True)
    return pd.DataFrame({"ts_utc": ts, "bid": df["bid"].astype(float), "ask": df["ask"].astype(float)})

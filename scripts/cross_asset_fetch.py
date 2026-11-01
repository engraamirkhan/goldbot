"""Bid 1m bars for the cross-asset lead-lag screen (docs/research/prereg-2026-11-01-cross-asset.md), local only.

Reads Dukascopy's free bi5 archive (the same source and record layout as scripts/dukascopy_year.py), bid side only,
one instrument-year per task, up to 4 worker processes, backing off on HTTP 429/503. Output goes to raw/xasset/
(git-ignored); market data is never committed or published (data-local-only).

  uv run python scripts/cross_asset_fetch.py [--years 2010-2017] [--workers 4]
"""
from __future__ import annotations

import argparse
import datetime as dt
import lzma
import time
import urllib.error
import urllib.request
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "raw" / "xasset"
DATAFEED = "https://datafeed.dukascopy.com/datafeed"
# bi5 prices are integers in 1/point of the quote currency
POINT: dict[str, float] = {"XAGUSD": 1000.0, "EURUSD": 100000.0, "USDJPY": 1000.0}
BAND: dict[str, tuple[float, float]] = {"XAGUSD": (5.0, 60.0), "EURUSD": (0.9, 1.7), "USDJPY": (70.0, 130.0)}
BI5_M1 = np.dtype([("sec", ">i4"), ("open", ">i4"), ("close", ">i4"), ("low", ">i4"), ("high", ">i4"),
                   ("volume", ">f4")])


def _get(url: str) -> bytes | None:
    """Body, b"" for 404 (no file), None after the retries are spent."""
    for k in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "goldbot-data/1"})
            with urllib.request.urlopen(req, timeout=40) as r:  # noqa: S310 (fixed https host)
                return bytes(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
        except OSError:
            pass
        time.sleep(2.0 * 2 ** min(k, 5))
    return None


def decode_day(raw: bytes, day: dt.date, point: float) -> pd.DataFrame:
    """One day's bi5 m1 candles as ts_utc/open/high/low/close/volume; zero-volume minutes dropped."""
    data = lzma.decompress(raw)
    rec = np.frombuffer(data[: len(data) // 24 * 24], dtype=BI5_M1)
    f = pd.DataFrame({k: rec[k].astype("float64") for k in ("sec", "open", "close", "low", "high", "volume")})
    f = f[f["volume"] > 0].copy()
    f["ts_utc"] = pd.Timestamp(day, tz="UTC") + pd.to_timedelta(f["sec"].astype("int64"), unit="s")
    for c in ("open", "high", "low", "close"):
        f[c] = f[c] / point
    return f[["ts_utc", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def fetch_year(arg: tuple[str, int]) -> str:
    code, year = arg
    out = OUT / f"{code.lower()}_1m_bid_{year}.parquet"
    if out.exists():
        return f"{code} {year} cached"
    frames, missed = [], 0
    d = dt.date(year, 1, 1)
    while d.year == year:
        if d.weekday() != 5:
            raw = _get(f"{DATAFEED}/{code}/{d.year:04d}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5")
            if raw is None:
                missed += 1
            elif raw:
                f = decode_day(raw, d, POINT[code])
                if not f.empty:
                    frames.append(f)
            time.sleep(0.15)
        d += dt.timedelta(days=1)
    df = pd.concat(frames).sort_values("ts_utc").reset_index(drop=True)
    med = float(df["close"].median())
    lo, hi = BAND[code]
    if not lo <= med <= hi:
        return f"{code} {year} SCALE median={med} not written"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return f"{code} {year} rows={len(df)} missed_days={missed} median={med:.4f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2010-2017")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    tasks = [(c, y) for y in range(y0, y1 + 1) for c in POINT]
    with Pool(min(a.workers, 4)) as p:
        for r in p.imap_unordered(fetch_year, tasks):
            print(r, flush=True)


if __name__ == "__main__":
    main()

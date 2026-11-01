"""Bid 1m bars for the cross-asset lead-lag screen (docs/research/prereg-2026-11-01-cross-asset.md), local only.

Default source (2026-11-01): HistData's free M1 ASCII archive, one pair-year per request, serial, through the site's
own download form; its stamps are EST without DST and are shifted to UTC (HD_OFFSET; checked against Dukascopy on
overlapping months before use, see cross-asset-screen-2026-11.md). The Dukascopy bi5 route below throttled this host
to ~1 month of three pairs per 10 minutes (6-75 s per day file, then dropped connections), about 40 h for 2010-2017.

Dukascopy route (--source dukascopy): Dukascopy's free bi5 archive (the same source and record layout as scripts/dukascopy_year.py), bid side only,
one instrument-month per task (cached, resumable), up to 4 worker processes x 2 day threads, backing off on HTTP 429/503. Output goes to raw/xasset/
(git-ignored); market data is never committed or published (data-local-only).

  uv run python scripts/cross_asset_fetch.py [--years 2010-2017] [--workers 4]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import lzma
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from multiprocessing import Pool
from multiprocessing.pool import ThreadPool
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "raw" / "xasset"
DATAFEED = "https://datafeed.dukascopy.com/datafeed"
# bi5 prices are integers in 1/point of the quote currency
POINT: dict[str, float] = {"XAGUSD": 1000.0, "EURUSD": 100000.0, "USDJPY": 1000.0}
BAND: dict[str, tuple[float, float]] = {"XAGUSD": (5.0, 60.0), "EURUSD": (0.9, 1.7), "USDJPY": (70.0, 130.0)}
THREADS = 2   # concurrent day files per worker process (I/O bound; 4 workers -> 8 in flight)
PAUSE = 0.0   # politeness pause per day file; the archive throttles (HTTP 429/503, then dropped connections) under load


def _set(threads: int, pause: float) -> None:
    global THREADS, PAUSE
    THREADS, PAUSE = threads, pause
BI5_M1 = np.dtype([("sec", ">i4"), ("open", ">i4"), ("close", ">i4"), ("low", ">i4"), ("high", ">i4"),
                   ("volume", ">f4")])


def _get(url: str) -> bytes | None:
    """Body, b"" for 404 (no file), None after the retries are spent."""
    for k in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "goldbot-data/1"})
            with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (fixed https host)
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


def _day(arg: tuple[str, dt.date]) -> tuple[pd.DataFrame | None, bool]:
    code, d = arg
    time.sleep(PAUSE)
    raw = _get(f"{DATAFEED}/{code}/{d.year:04d}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5")
    if raw is None:
        return None, True
    return (decode_day(raw, d, POINT[code]) if raw else None), False


def fetch_month(arg: tuple[str, int, int]) -> str:
    """One instrument-month, cached as its own Parquet so an interrupted run resumes; days fetched THREADS at a
    time (the archive answers in ~6-15 s per file, so a strictly serial pull of 2010-2017 would take a day)."""
    code, year, month = arg
    out = OUT / "months" / f"{code.lower()}_{year}_{month:02d}.parquet"
    if out.exists():
        return f"{code} {year}-{month:02d} cached"
    d, days = dt.date(year, month, 1), []
    while d.month == month:
        if d.weekday() != 5:
            days.append((code, d))
        d += dt.timedelta(days=1)
    with ThreadPool(THREADS) as tp:
        res = tp.map(_day, days)
    missed = sum(m for _, m in res)
    frames = [f for f, _ in res if f is not None and not f.empty]
    if missed:
        return f"{code} {year}-{month:02d} INCOMPLETE missed_days={missed} (not written, rerun)"
    df = pd.concat(frames).sort_values("ts_utc").reset_index(drop=True)
    med = float(df["close"].median())
    lo, hi = BAND[code]
    if not lo <= med <= hi:
        return f"{code} {year}-{month:02d} SCALE median={med} not written"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return f"{code} {year}-{month:02d} rows={len(df)} median={med:.4f}"


def combine(code: str, year: int) -> str:
    parts = sorted((OUT / "months").glob(f"{code.lower()}_{year}_*.parquet"))
    if len(parts) != 12:
        return f"{code} {year}: {len(parts)}/12 months, year file not written"
    df = pd.concat([pd.read_parquet(p) for p in parts]).sort_values("ts_utc").reset_index(drop=True)
    df.to_parquet(OUT / f"{code.lower()}_1m_bid_{year}.parquet")
    return f"{code} {year}: rows={len(df)}"


HISTDATA = "https://www.histdata.com"
HD_PAGE = HISTDATA + "/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair}/{year}"
HD_OFFSET = pd.Timedelta(hours=5)   # HistData ASCII stamps are EST without daylight saving (UTC-5 all year)


def parse_histdata(text: str) -> pd.DataFrame:
    """HistData ASCII M1 rows `YYYYMMDD HHMMSS;open;high;low;close;volume` (bid, bar open, EST no DST) -> UTC."""
    rows = [r.split(";") for r in text.strip().splitlines() if r.strip()]
    f = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        f[c] = f[c].astype(float)
    f["ts_utc"] = pd.to_datetime(f["t"], format="%Y%m%d %H%M%S").dt.tz_localize("UTC") + HD_OFFSET
    return f[["ts_utc", "open", "high", "low", "close", "volume"]].sort_values("ts_utc").reset_index(drop=True)


def fetch_histdata(arg: tuple[str, int]) -> str:
    """One pair-year from HistData's free M1 ASCII archive, through the site's own download form (page token, POST)."""
    code, year = arg
    out = OUT / "hd" / f"{code.lower()}_1m_bid_{year}.parquet"
    if out.exists():
        return f"histdata {code} {year} cached"
    page = HD_PAGE.format(pair=code.lower(), year=year)
    html = _get(page)
    if not html:
        return f"histdata {code} {year}: page failed"
    m = re.search(rb'name="tk" id="tk" value="([0-9a-f]+)"', html)
    if not m:
        return f"histdata {code} {year}: no form token"
    body = urllib.parse.urlencode({"tk": m.group(1).decode(), "date": year, "datemonth": year, "platform": "ASCII",
                                   "timeframe": "M1", "fxpair": code}).encode()
    req = urllib.request.Request(HISTDATA + "/get.php", data=body,
                                 headers={"User-Agent": "goldbot-data/1", "Referer": page})
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 (fixed https host)
        blob = bytes(r.read())
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        df = parse_histdata(z.read(name).decode())
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    time.sleep(2.0)
    return f"histdata {code} {year} rows={len(df)} median={df['close'].median():.4f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2010-2017")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--threads", type=int, default=THREADS, help="day files in flight per worker")
    ap.add_argument("--pause", type=float, default=PAUSE, help="seconds between day files per thread")
    ap.add_argument("--source", choices=["dukascopy", "histdata"], default="histdata")
    ap.add_argument("--pairs", default="XAGUSD,EURUSD,USDJPY")
    a = ap.parse_args()
    _set(a.threads, a.pause)
    if a.source == "histdata":
        y0, y1 = (int(x) for x in a.years.split("-"))
        for code in a.pairs.split(","):
            for y in range(y0, y1 + 1):
                print(fetch_histdata((code, y)), flush=True)
        return
    y0, y1 = (int(x) for x in a.years.split("-"))
    tasks = [(c, y, m) for y in range(y0, y1 + 1) for m in range(1, 13) for c in POINT]
    with Pool(min(a.workers, 4), initializer=_set, initargs=(a.threads, a.pause)) as p:
        for r in p.imap_unordered(fetch_month, tasks):
            print(r, flush=True)
    for y in range(y0, y1 + 1):
        for c in POINT:
            print(combine(c, y), flush=True)


if __name__ == "__main__":
    main()

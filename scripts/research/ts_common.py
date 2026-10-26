"""Shared loaders for the time-structure slice C study (docs/research/time-structure/fib-wavelets-cycles.md).

Reads the local Dukascopy 1m Parquet (scripts/fetch_data_release.py layout), only the discovery half 2010-2017
(2018+ is never loaded), builds mid OHLC and the 15m/1h/4h/1d bars through goldbot.data.resample, and
caches them. Research-only; imports nothing that touches the broker or the store.

Usage: uv run --with pywavelets --with scipy --with statsmodels python scripts/research/ts_common.py --raw DIR --out DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402

DISCOVERY_END = pd.Timestamp("2018-01-01", tz="UTC")   # discovery half 2010-2017 only (time-structure README 4.1)
FIRST_YEAR = 2010
LAST_YEAR = 2017
TFS = ("15m", "1h", "4h", "1d")


def load_1m(raw: Path) -> pd.DataFrame:
    parts = [pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(FIRST_YEAR, LAST_YEAR + 1)]
    d = pd.concat(parts, ignore_index=True)
    d = d[d["ts_utc"] < DISCOVERY_END].sort_values("ts_utc").reset_index(drop=True)
    return d


def build(raw: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    d = load_1m(raw)
    m1 = mid(d)
    m1["ts_ns"] = epoch_ns(pd.DatetimeIndex(m1["ts_utc"]))
    m1.to_parquet(out / "mid_1m.parquet")
    for tf in TFS:
        b = mid(resample_bars(d, tf))
        b["ts_ns"] = epoch_ns(pd.DatetimeIndex(b["ts_utc"]))
        b["avail_ns"] = epoch_ns(pd.DatetimeIndex(b["visible_at"]))
        b.to_parquet(out / f"mid_{tf}.parquet")
        print(tf, len(b), b["ts_utc"].min(), b["ts_utc"].max())


def load_tf(out: Path, tf: str) -> pd.DataFrame:
    return pd.read_parquet(out / f"mid_{tf}.parquet")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    build(a.raw, a.out)

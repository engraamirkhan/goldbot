"""Daily inputs of the position engine, cut at the holdout before a row is materialised.

W3.2 clock: "The decision is made on the 1d bar that closes at 21:00 UTC, with bars built from mid 1m data. The fill
is at the next available 1m open." Daily bar D holds the 1m bars in [D-1 21:00, D 21:00) UTC (`resample_bars` with
the feature day on UTC at 21:00), so its open is the first 1m open after the previous decision: the fill.

Sources (all local, never committed):
* gold: `raw/xauusd_1m_dukascopy_<year>.parquet`; files of years after 2025 are never opened, the 2025 file is read
  with a pushed-down filter `ts_utc < 2025-10-01`, and the partial bar of 2025-10-01 is dropped;
* other markets: `raw/daily/<symbol>.parquet` with date, open, high, low, close (the data lane's daily files, the same
  21:00 UTC close), read with `date < 2025-10-01`;
* DTB3: `financing.rate_frame` (date, rate_pct).
A daily frame is indexed by `date` (naive day label) with mid open, high, low, close and `close_utc` (the decision
instant, 21:00 UTC of that date).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.resample import resample_bars
from goldbot.data.timeutil import utc_index
from goldbot.research.position.windows import HOLDOUT_FROM, HOLDOUT_FROM_UTC, assert_before_holdout

DAY_TZ = "UTC"
DAY_CLOSE = "21:00"
DAILY_COLUMNS = ("open", "high", "low", "close")
_YEAR = re.compile(r"_(\d{4})\.parquet$")
_1M_COLUMNS = ["ts_utc", "visible_at", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
               "ask_low", "ask_close", "tick_count", "spread_mean", "spread_max"]


def _close_utc(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    hh, mm = (int(x) for x in DAY_CLOSE.split(":"))
    return (pd.DatetimeIndex(dates) + pd.Timedelta(hours=hh, minutes=mm)).tz_localize("UTC")


def read_1m_files(raw_dir: Path | str, prefix: str = "xauusd") -> pd.DataFrame:
    """The `<prefix>_1m_dukascopy_<year>.parquet` files up to 2025, each filtered `ts_utc < 2025-10-01` as it is read.
    A later year's file is never opened."""
    files = sorted(Path(raw_dir).glob(f"{prefix}_1m_dukascopy_*.parquet"))
    keep = [f for f in files if (m := _YEAR.search(f.name)) and int(m.group(1)) <= HOLDOUT_FROM_UTC.year]
    if not keep:
        raise FileNotFoundError(f"no {prefix} 1m files up to {HOLDOUT_FROM_UTC.year} in {raw_dir}")
    parts = [pd.read_parquet(f, columns=_1M_COLUMNS, filters=[("ts_utc", "<", HOLDOUT_FROM_UTC)]) for f in keep]
    out = pd.concat(parts, ignore_index=True).sort_values("ts_utc").reset_index(drop=True)
    assert_before_holdout(utc_index(out["ts_utc"]), what="1m bars")
    return out


def mid_daily(store_daily: pd.DataFrame) -> pd.DataFrame:
    """Daily store bars (bid/ask, `visible_at` = the 21:00 UTC close) -> the engine's daily mid frame."""
    close_utc = utc_index(store_daily["visible_at"])
    dates = pd.DatetimeIndex(close_utc.tz_convert(None).normalize(), name="date")
    out = pd.DataFrame({c: ((store_daily[f"bid_{c}"] + store_daily[f"ask_{c}"]) / 2.0).to_numpy() for c in DAILY_COLUMNS},
                       index=dates)
    out["close_utc"] = close_utc
    return out


def fold_weekends(store_daily: pd.DataFrame) -> pd.DataFrame:
    """Fold a Saturday- or Sunday-dated bar into the next weekday bar. In northern winter gold trades until 22:00 UTC
    on Friday, so the 21:00 UTC close leaves a Friday hour labelled Saturday: it is the next 1m open after Friday's
    decision (W3.2's fill), so it opens Monday's bar and widens its range. A trailing weekend bar is dropped."""
    s = store_daily.reset_index(drop=True)
    day = utc_index(s["visible_at"]).tz_convert(None).normalize()
    weekend = np.asarray(day.dayofweek >= 5)
    if not weekend.any():
        return store_daily
    rows = []
    carry: pd.Series | None = None
    for i in range(len(s)):
        r = s.iloc[i].copy()
        if carry is not None:
            for c in ("bid", "ask"):
                r[f"{c}_open"] = carry[f"{c}_open"]
                r[f"{c}_high"] = max(r[f"{c}_high"], carry[f"{c}_high"])
                r[f"{c}_low"] = min(r[f"{c}_low"], carry[f"{c}_low"])
            r["ts_utc"], r["tick_count"] = carry["ts_utc"], r["tick_count"] + carry["tick_count"]
            r["spread_max"] = max(r["spread_max"], carry["spread_max"])
            carry = None
        if weekend[i]:
            carry = r                                  # already holds an earlier weekend bar, if any
            continue
        rows.append(r)
    return pd.DataFrame(rows).reset_index(drop=True)


def daily_from_1m(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """Daily mid bars closing 21:00 UTC (weekend stubs folded forward); a bar dated on or after the holdout (the
    partial 2025-10-01) is dropped."""
    store = fold_weekends(resample_bars(bars_1m, "1d", feature_day_tz=DAY_TZ, feature_day_anchor=DAY_CLOSE))
    out = mid_daily(store)
    out = out[out.index < HOLDOUT_FROM]
    assert_before_holdout(out.index, what="daily bars")
    return out


def load_xau_daily(raw_dir: Path | str) -> pd.DataFrame:
    return daily_from_1m(read_1m_files(raw_dir, "xauusd"))


def load_daily_file(path: Path | str) -> pd.DataFrame:
    """A data-lane daily file (date, open, high, low, close at the 21:00 UTC close), read with `date < 2025-10-01`."""
    df = pd.read_parquet(path, columns=["date", *DAILY_COLUMNS], filters=[("date", "<", HOLDOUT_FROM)])
    dates = pd.DatetimeIndex(pd.to_datetime(df["date"]), name="date")
    dates = dates.tz_convert(None) if dates.tz is not None else dates
    out = pd.DataFrame({c: df[c].to_numpy(dtype=float) for c in DAILY_COLUMNS}, index=dates.normalize()).sort_index()
    out["close_utc"] = _close_utc(pd.DatetimeIndex(out.index))
    assert_before_holdout(out.index, what="daily bars")
    return out


def load_panel(raw_dir: Path | str, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
    """XAUUSD from the 1m files, every other symbol from `raw/daily/<symbol>.parquet` (missing: FileNotFoundError, so
    the manifest lists the dropped market before any run, POS-1 section 1)."""
    raw = Path(raw_dir)
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        daily_file = raw / "daily" / f"{s}.parquet"
        if s == "XAUUSD":
            out[s] = load_xau_daily(raw)
        elif daily_file.exists():
            out[s] = load_daily_file(daily_file)
        else:
            raise FileNotFoundError(f"no daily file for {s} at {daily_file}")
    return out

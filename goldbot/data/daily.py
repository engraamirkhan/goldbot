"""Daily mid bars and daily FRED series for the multi-asset position research (POS-1), local only.

Market data never enters the repository or a release (ADR 0048, data-local-only): the files live in the git-ignored
`raw/daily/` and are built by `scripts/daily_fetch.py`. This module holds only the conventions and the loaders.

Bar convention (docs/research/pos1/prereg.md, section 1): a daily bar closes at 21:00 UTC. Bar D holds every hour after
the previous trading bar's close up to D 21:00 UTC, so the Sunday reopen and a winter Friday 21:00-22:00 FX hour belong
to Monday (`session_date`). `open` is therefore the first traded price after the previous close, i.e. the "next 1m open"
fill of the prereg. Prices are mid ((bid + ask) / 2 per OHLC field); `spread_close` is ask - bid at the close.

Columns of raw/daily/<symbol>.parquet:
  date (datetime64, naive, the session date), open, high, low, close (mid), spread_close, n_hours (hourly candles with
  volume in the bar; Dukascopy hourly route) or n_minutes (1m route, XAUUSD), visible_at (UTC; D 21:00, when the
  close became knowable), source.
FRED files raw/daily/fred_<ID>.parquet follow goldbot.data.macro.fred_frame (value_date, value, vintage, available_utc).

Hard cutoff (`CUTOFF`): nothing at or after 2025-10-01 00:00 UTC is fetched, stored or returned; the loaders assert it,
so a file written by a careless tool fails loudly instead of leaking the holdout.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

CUTOFF = pd.Timestamp("2025-10-01", tz="UTC")
LAST_MONTH = dt.date(2025, 9, 1)          # the last month any fetch may request
CLOSE_HOUR_UTC = 21
DAILY_ROOT = Path(os.environ.get("GOLDBOT_DAILY_ROOT") or Path(__file__).resolve().parents[2] / "raw" / "daily")
DAILY_COLUMNS = ("date", "open", "high", "low", "close", "spread_close", "visible_at", "source")


class DailySpec(FrozenRecord):
    """One Dukascopy instrument on the hourly route. point: bi5 prices are integers in 1/point; price_band: the range the
    median close of a month must lie in (a factor-of-10 point error fails it); hourly_start: the first month the
    archive serves hourly candles (dukascopy-node metadata, checked against the archive on 2026-11-02)."""

    symbol: str
    code: str
    point: float
    price_band: tuple[float, float]
    hourly_start: dt.date
    group: str

    def price_ok(self, price: float) -> bool:
        return self.price_band[0] <= price <= self.price_band[1]


def _s(symbol: str, code: str, point: float, band: tuple[float, float], start: dt.date, group: str) -> DailySpec:
    return DailySpec(symbol=symbol, code=code, point=point, price_band=band, hourly_start=start, group=group)


_FX0 = dt.date(2003, 5, 1)
_FX1 = dt.date(2003, 8, 1)
_IDX = dt.date(2011, 9, 1)
# POS-1 U_16 minus XAUUSD (built from the XAUUSD 1m lane's files, see load_daily)
POS1_SYMBOLS = ("XAGUSD", "XPTUSD", "WTI", "US500", "US100", "GER40", "JP225", "UK100",
                "EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
SPECS: dict[str, DailySpec] = {s.symbol: s for s in (
    _s("XAGUSD", "XAGUSD", 1e3, (2.0, 200.0), _FX0, "metals"),
    _s("XPTUSD", "XPTCMDUSD", 1e3, (300.0, 3000.0), dt.date(2021, 11, 1), "metals"),
    _s("XPDUSD", "XPDCMDUSD", 1e3, (300.0, 5000.0), dt.date(2021, 7, 1), "metals"),
    _s("XAUEUR", "XAUEUR", 1e3, (500.0, 10_000.0), dt.date(2023, 3, 1), "metals"),
    _s("XAUAUD", "XAUAUD", 1e3, (500.0, 15_000.0), dt.date(2024, 5, 1), "metals"),
    _s("WTI", "LIGHTCMDUSD", 1e3, (-50.0, 200.0), dt.date(2011, 9, 1), "energy"),
    _s("BRENT", "BRENTCMDUSD", 1e3, (5.0, 200.0), dt.date(2010, 12, 1), "energy"),
    _s("NATGAS", "GASCMDUSD", 1e4, (0.5, 20.0), dt.date(2012, 9, 1), "energy"),
    _s("COPPER", "COPPERCMDUSD", 1e4, (1.0, 10.0), dt.date(2012, 3, 1), "metals"),
    _s("US500", "USA500IDXUSD", 1e3, (500.0, 10_000.0), _IDX, "indices"),
    _s("US100", "USATECHIDXUSD", 1e3, (1000.0, 40_000.0), _IDX, "indices"),
    _s("GER40", "DEUIDXEUR", 1e3, (3000.0, 40_000.0), dt.date(2013, 9, 1), "indices"),
    _s("JP225", "JPNIDXJPY", 1e3, (5000.0, 80_000.0), _IDX, "indices"),
    _s("UK100", "GBRIDXGBP", 1e3, (3000.0, 15_000.0), _IDX, "indices"),
    _s("SOYBEAN", "SOYBEANCMDUSX", 1e3, (300.0, 3000.0), dt.date(2017, 12, 1), "agri"),
    _s("SUGAR", "SUGARCMDUSD", 1e3, (100.0, 1500.0), dt.date(2017, 10, 1), "agri"),
    _s("COFFEE", "COFFEECMDUSX", 1e3, (30.0, 600.0), dt.date(2017, 12, 1), "agri"),
    _s("COCOA", "COCOACMDUSD", 1e3, (500.0, 15_000.0), dt.date(2017, 10, 1), "agri"),
    _s("COTTON", "COTTONCMDUSX", 1e3, (30.0, 250.0), dt.date(2017, 12, 1), "agri"),
    _s("BUND", "BUNDTREUR", 1e3, (100.0, 250.0), dt.date(2016, 5, 1), "rates"),
    _s("USTBOND", "USTBONDTRUSD", 1e3, (80.0, 250.0), dt.date(2018, 12, 1), "rates"),
    _s("EURUSD", "EURUSD", 1e5, (0.8, 1.8), _FX0, "fx"),
    _s("GBPUSD", "GBPUSD", 1e5, (1.0, 2.2), _FX0, "fx"),
    _s("USDJPY", "USDJPY", 1e3, (70.0, 170.0), _FX0, "fx"),
    _s("AUDUSD", "AUDUSD", 1e5, (0.45, 1.2), _FX1, "fx"),
    _s("USDCAD", "USDCAD", 1e5, (0.9, 1.7), _FX1, "fx"),
    _s("USDCHF", "USDCHF", 1e5, (0.7, 1.9), _FX0, "fx"),
    _s("NZDUSD", "NZDUSD", 1e5, (0.4, 1.0), _FX1, "fx"),
)}
FRED_DAILY = ("DTB3", "DFII10", "DTWEXBGS", "DTWEXB", "T10YIE")
USD_BROAD_SPLICE = pd.Timestamp("2006-01-02")   # prereg: DTWEXB before, DTWEXBGS from this date on


def assert_before_cutoff(when: pd.Timestamp | dt.date | dt.datetime, what: str = "timestamp") -> None:
    """Raise if `when` (naive = UTC) is at or after CUTOFF. Fetch code calls this before building any URL."""
    t = pd.Timestamp(when)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if t >= CUTOFF:
        raise ValueError(f"{what} {t} is at or after the research cutoff {CUTOFF:%Y-%m-%d}")


def months(start: dt.date, last: dt.date = LAST_MONTH) -> list[dt.date]:
    """First days of every month from start's month through `last` (never past LAST_MONTH)."""
    last = min(last, LAST_MONTH)
    out, d = [], dt.date(start.year, start.month, 1)
    while d <= last:
        out.append(d)
        d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def session_date(ts: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The 21:00-UTC session a bar *starting* at `ts` (UTC) belongs to: [D-1 21:00, D 21:00) -> D, and a weekend
    label rolls forward to Monday (a bar closes at 21:00 on a weekday; hours after Friday's close open Monday's bar)."""
    t = pd.DatetimeIndex(pd.to_datetime(ts, utc=True))
    d = (t + pd.Timedelta(hours=24 - CLOSE_HOUR_UTC)).tz_localize(None).normalize()
    wd = d.dayofweek
    roll = np.where(wd == 5, 2, np.where(wd == 6, 1, 0))
    return pd.DatetimeIndex(d + pd.to_timedelta(roll, unit="D"))


def close_utc(date: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    """When the bar dated `date` closes, i.e. becomes knowable: date 21:00 UTC."""
    return (pd.DatetimeIndex(pd.to_datetime(date)).normalize() + pd.Timedelta(hours=CLOSE_HOUR_UTC)).tz_localize("UTC")


def bars_to_daily(bars: pd.DataFrame, count_col: str, source: str) -> pd.DataFrame:
    """Intraday mid bars (ts_utc = bar start, open/high/low/close, spread) -> one row per session_date.

    Rows whose session closes at or after CUTOFF are dropped (the 2025-09-30 21:00-24:00 hours would open a 10-01 bar
    nobody may see)."""
    if bars.empty:
        return pd.DataFrame(columns=[*DAILY_COLUMNS[:6], count_col, *DAILY_COLUMNS[6:]])
    b = bars.sort_values("ts_utc")
    key = session_date(b["ts_utc"])
    g = b.groupby(key, sort=True)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
        "spread_close": g["spread"].last(), count_col: g["close"].size().astype("int64"),
    })
    out.index.name = "date"
    out = out.reset_index()
    out["visible_at"] = close_utc(out["date"])
    out = out[out["visible_at"] < CUTOFF].reset_index(drop=True)
    out["source"] = source
    return out[[*DAILY_COLUMNS[:6], count_col, *DAILY_COLUMNS[6:]]]


def check_daily(df: pd.DataFrame, symbol: str) -> None:
    """The contract every daily file and loader result meets; raises ValueError naming the first breach."""
    if df.empty:
        raise ValueError(f"{symbol}: no rows")
    missing = [c for c in DAILY_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: missing columns {missing}")
    d = pd.DatetimeIndex(df["date"])
    if not d.is_monotonic_increasing or d.has_duplicates:
        raise ValueError(f"{symbol}: dates not strictly increasing")
    vis = pd.DatetimeIndex(pd.to_datetime(df["visible_at"], utc=True))
    if (vis >= CUTOFF).any() or d.max() >= CUTOFF.tz_localize(None):
        raise ValueError(f"{symbol}: rows at or after the cutoff {CUTOFF:%Y-%m-%d}")
    if not (vis == close_utc(d)).all():
        raise ValueError(f"{symbol}: visible_at is not the 21:00 UTC close of date")
    bad = ~((df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9) & (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9))
    if bad.any():
        raise ValueError(f"{symbol}: {int(bad.sum())} rows with high/low not bracketing open/close")


def load_daily(symbol: str, root: Path | None = None) -> pd.DataFrame:
    """raw/daily/<symbol>.parquet (lower-case file name), checked against the contract and the cutoff."""
    path = (root or DAILY_ROOT) / f"{symbol.lower()}.parquet"
    df = pd.read_parquet(path)
    df["date"] = pd.DatetimeIndex(pd.to_datetime(df["date"])).as_unit("ns")
    df["visible_at"] = pd.DatetimeIndex(pd.to_datetime(df["visible_at"], utc=True)).as_unit("ns")
    check_daily(df, symbol)
    return df.reset_index(drop=True)


def load_fred(series_id: str, root: Path | None = None) -> pd.DataFrame:
    """raw/daily/fred_<ID>.parquet; asserts every value_date and available_utc is before the cutoff."""
    df = pd.read_parquet((root or DAILY_ROOT) / f"fred_{series_id}.parquet")
    av = pd.DatetimeIndex(pd.to_datetime(df["available_utc"], utc=True)).as_unit("ns")
    vd = pd.DatetimeIndex(pd.to_datetime(df["value_date"])).as_unit("ns")
    if len(df) and ((av >= CUTOFF).any() or vd.max() >= CUTOFF.tz_localize(None)):
        raise ValueError(f"fred_{series_id}: rows at or after the cutoff {CUTOFF:%Y-%m-%d}")
    if (av <= vd.tz_localize("UTC")).any():
        raise ValueError(f"fred_{series_id}: a value available on or before its own value_date")
    return df.assign(available_utc=av, value_date=vd).sort_values("available_utc").reset_index(drop=True)


def usd_broad(root: Path | None = None) -> pd.DataFrame:
    """The prereg's USD broad series: DTWEXB before 2006-01-02, DTWEXBGS from then on (value_date, value,
    available_utc, series_id).

    The two indices have different bases, so a plain splice puts a fake jump into every change that straddles
    2006-01-02. DTWEXB is ratio-chained instead: multiplied by DTWEXBGS / DTWEXB on the splice date. The factor is one
    constant, so every percentage change of the old segment is exactly the original one (no look-ahead enters a
    return), and the 20-day changes across the splice are continuous."""
    old = load_fred("DTWEXB", root)
    new = load_fred("DTWEXBGS", root)
    o = old.set_index("value_date")["value"]
    n = new.set_index("value_date")["value"]
    both = o.index.intersection(n.index)
    both = both[both >= USD_BROAD_SPLICE]
    if both.empty:
        raise ValueError("DTWEXB and DTWEXBGS share no date on or after the splice; cannot chain")
    factor = float(n[both[0]] / o[both[0]])
    old = old[old["value_date"] < USD_BROAD_SPLICE].assign(value=lambda f: f["value"] * factor)
    out = pd.concat([old, new[new["value_date"] >= USD_BROAD_SPLICE]])
    return out[["value_date", "value", "available_utc", "series_id"]].sort_values("value_date").reset_index(drop=True)


BI5_CANDLE = np.dtype([("sec", ">i4"), ("open", ">i4"), ("close", ">i4"), ("low", ">i4"), ("high", ">i4"),
                       ("volume", ">f4")])


def decode_hourly(data: bytes, month: dt.date, point: float) -> pd.DataFrame:
    """One month of Dukascopy hourly candles (lzma-decompressed bi5: seconds from the month start, open, close, low,
    high in 1/point, volume) -> ts_utc (hour start), open, high, low, close, volume. Hours without volume (market
    closed; the archive fills them with the last price) are dropped, and so is anything at or after CUTOFF."""
    rec = np.frombuffer(data[: len(data) // 24 * 24], dtype=BI5_CANDLE)
    f = pd.DataFrame({k: rec[k].astype("float64") for k in ("sec", "open", "close", "low", "high", "volume")})
    f = f[f["volume"] > 0]
    ts = pd.Timestamp(month, tz="UTC") + pd.to_timedelta(f["sec"].astype("int64").to_numpy(), unit="s")
    out = pd.DataFrame({"ts_utc": pd.DatetimeIndex(ts).as_unit("ns")})
    for c in ("open", "high", "low", "close"):
        out[c] = f[c].to_numpy() / point
    out["volume"] = f["volume"].to_numpy()
    return out[out["ts_utc"] < CUTOFF].reset_index(drop=True)


def _bracket(b: pd.DataFrame) -> pd.DataFrame:
    # the mid of two highs can sit below a mid open/close (and the low above); widen to keep OHLC consistent
    b["high"] = b[["open", "high", "close"]].max(axis=1)
    b["low"] = b[["open", "low", "close"]].min(axis=1)
    return b


def hourly_mid(bid: pd.DataFrame, ask: pd.DataFrame) -> pd.DataFrame:
    """Bid and ask hourly candles -> mid candles with the close spread, on hours both sides traded."""
    m = bid.merge(ask, on="ts_utc", suffixes=("_bid", "_ask"), how="inner")
    out = pd.DataFrame({"ts_utc": m["ts_utc"]})
    for c in ("open", "high", "low", "close"):
        out[c] = (m[f"{c}_bid"] + m[f"{c}_ask"]) / 2.0
    out["spread"] = m["close_ask"] - m["close_bid"]
    return _bracket(out).sort_values("ts_utc").reset_index(drop=True)


def minutes_to_daily(bars: pd.DataFrame, source: str = "dukascopy_1m") -> pd.DataFrame:
    """goldbot 1m bars (bid_*/ask_* columns, the XAUUSD lane's yearly files) -> daily mid bars with n_minutes. Rows
    flagged as errors are expected to be quarantined already; warnings are kept (they are flags, not drops)."""
    b = pd.DataFrame({"ts_utc": pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)).as_unit("ns")})
    for c in ("open", "high", "low", "close"):
        b[c] = (bars[f"bid_{c}"].to_numpy() + bars[f"ask_{c}"].to_numpy()) / 2.0
    b["spread"] = (bars["ask_close"] - bars["bid_close"]).to_numpy()
    return bars_to_daily(_bracket(b), "n_minutes", source)

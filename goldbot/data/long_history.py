"""Long daily history before our Dukascopy start, stitched to the Dukascopy daily bars. Local research only.

Market data never enters the repository or a release (ADR 0048, data-local-only): sources are cached under the
git-ignored `raw/daily/_long_cache/`, outputs go to `raw/daily/` (`scripts/gold_history.py`,
`scripts/long_daily_history.py`). This module holds the pure parts: loaders/parsers, the point-in-time stamps,
the stitch and its checks, so they are unit-tested without a network.

Row contract (LONG_COLUMNS): date (naive session date), open/high/low/close (NaN where a source has only a fixing),
source (`<provider>:<series>`), available_utc (tz-aware UTC; when the value became knowable). One row per date,
dates strictly increasing, available_utc strictly increasing, on or after the date's 00:00 UTC and before the next
day's end, and before the research cutoff (goldbot.data.daily.CUTOFF: the holdout stays sealed).

Stitch rule: sources are ranked, best first (Dukascopy). A lower-ranked source contributes only the dates strictly
before the first date already in the series, so a source never fills holes inside a better one and every switch
of source happens once, at a single cut-over. Prices are not level-adjusted: the overlap level gap at each
cut-over is measured and reported (`OverlapStat`); `source` lets a study drop or adjust a segment.

Point-in-time stamps (conservative: late is safe, early is a leak):
  LBMA gold PM price (World Gold Council Goldhub)   15:30 Europe/London on the date (PM fix 15:00, auction may run on)
  Frankfurt fixings (Deutsche Bundesbank BBEX3)    17:00 Europe/Berlin (fixings ~13:00; the exchange published by close)
  ECB euro reference rates                         16:00 Europe/Berlin (published ~16:00 CET)
  EIA spot prices, Yahoo US futures/indices        23:00 UTC (after every US settlement, both DST regimes; repo rule)
  Yahoo ^GSPC                                      21:15 UTC (the POS-1 prereg stamp)
  Dukascopy daily bars                             their visible_at (21:00 UTC close)
"""
from __future__ import annotations

import io
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.daily import CLOSE_HOUR_UTC, CUTOFF

LONG_COLUMNS = ("date", "open", "high", "low", "close", "source", "available_utc")
TROY_OZ_PER_KG = 32.1507466
DEM_PER_EUR = 1.95583          # the irrevocable conversion rate fixed on 1998-12-31
EpochUnit = Literal["s", "ms", "us", "ns"]


class OverlapStat(FrozenRecord):
    """Level agreement between a lower-ranked source and the series it hands over to, on their common dates."""

    lower: str
    upper: str
    n_common: int
    median_abs_pct: float | None       # median |lower / upper - 1| in %
    median_signed_pct: float | None    # median (lower / upper - 1) in %: the level offset at the cut-over
    cutover: str                       # first date of the higher-ranked part (the lower source stops before it)
    rows_added: int


# ---------------------------------------------------------------------------------------------------------- units


def infer_epoch_unit(values: Sequence[float] | np.ndarray | pd.Series) -> EpochUnit:
    """Unit of epoch integers from their magnitude (any date 1973-5138 is unambiguous): seconds < 1e11 <= ms < 1e14
    <= us < 1e17 <= ns."""
    arr = np.abs(np.asarray(values, dtype="float64"))
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        raise ValueError("no finite epoch values to infer a unit from")
    m = float(arr.max())
    return "s" if m < 1e11 else "ms" if m < 1e14 else "us" if m < 1e17 else "ns"


def epoch_to_utc(values: pd.Series) -> pd.DatetimeIndex:
    """Epoch numbers in any unit -> tz-aware UTC timestamps (ns resolution)."""
    unit = infer_epoch_unit(values)
    return pd.DatetimeIndex(pd.to_datetime(values.astype("int64"), unit=unit, utc=True)).as_unit("ns")


def local_stamp(dates: pd.Series | pd.DatetimeIndex, tz: str, hour: int, minute: int = 0) -> pd.DatetimeIndex:
    """`date hh:mm` wall clock in `tz`, as UTC; DST resolved per date (no fixed offset is ever assumed)."""
    d = pd.DatetimeIndex(pd.to_datetime(dates)).as_unit("ns").normalize()
    wall = d + pd.Timedelta(hours=hour, minutes=minute)
    return wall.tz_localize(tz, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC")


def utc_stamp(dates: pd.Series | pd.DatetimeIndex, hour: int, minute: int = 0) -> pd.DatetimeIndex:
    return local_stamp(dates, "UTC", hour, minute)


# -------------------------------------------------------------------------------------------------------- loaders


def load_goldhub(path: str | Path, sheet: str | None = None) -> pd.DataFrame:
    """World Gold Council Goldhub prices workbook -> [date, close] (USD/oz, the LBMA PM price).

    The sheet is the first whose name contains "daily" unless given. The header row is the first of the top 50 rows
    holding a cell exactly "USD"; the date column is the column left of it that parses as dates most often."""
    xls = pd.ExcelFile(path)
    names = [str(s) for s in xls.sheet_names]
    if sheet is None:
        daily = [s for s in names if "daily" in s.lower()]
        if not daily:
            raise ValueError(f"no 'Daily' sheet found; sheets: {names}")
        sheet = daily[0]
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)
    header_row: int | None = None
    usd_col: int | None = None
    for i in range(min(50, len(raw))):
        for j in range(raw.shape[1]):
            val = raw.iat[i, j]
            if isinstance(val, str) and val.strip().upper() == "USD":
                header_row, usd_col = i, j
                break
        if header_row is not None:
            break
    if header_row is None or usd_col is None:
        raise ValueError("no 'USD' column header in the first 50 rows")
    if usd_col == 0:
        raise ValueError("the 'USD' header is in the first column: no date column to its left")
    body = raw.iloc[header_row + 1:]

    def n_dates(c: int) -> int:
        return int(pd.to_datetime(body.iloc[:, c], errors="coerce").notna().sum())

    date_col = max(range(usd_col), key=n_dates)
    df = pd.DataFrame({"date": pd.to_datetime(body.iloc[:, date_col], errors="coerce"),
                       "close": pd.to_numeric(body.iloc[:, usd_col], errors="coerce")}).dropna()
    df = df[df["close"] > 0]
    df["date"] = pd.DatetimeIndex(df["date"]).as_unit("ns").normalize()
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def goldhub_frame(gh: pd.DataFrame) -> pd.DataFrame:
    """Goldhub [date, close] -> the long-row contract (fix only: no OHLC)."""
    out = gh[["date", "close"]].copy()
    out["available_utc"] = local_stamp(out["date"], "Europe/London", 15, 30)
    return to_long(out, "wgc_goldhub:LBMA_PM_USD")


def load_dukascopy_daily(path: str | Path) -> pd.DataFrame:
    """Our Dukascopy daily file (Parquet from scripts/daily_fetch.py / build_bars.py, or a CSV with a `timestamp`
    column in any epoch unit or ISO text) -> the long-row contract. Bars with high <= low (flat holiday stubs, or
    corrupt) are dropped, as in the owner's original script."""
    p = Path(path)
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
    df.columns = [str(c).lower() for c in df.columns]
    if "date" in df.columns:
        dates = pd.DatetimeIndex(pd.to_datetime(df["date"])).as_unit("ns")
        if dates.tz is not None:
            dates = dates.tz_convert("UTC").tz_localize(None)
    elif "timestamp" in df.columns:
        ts = df["timestamp"]
        if pd.api.types.is_numeric_dtype(ts):
            dates = epoch_to_utc(ts).tz_localize(None)
        else:
            dates = pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).as_unit("ns").tz_localize(None)
    else:
        raise ValueError(f"{p}: neither a 'date' nor a 'timestamp' column")
    out = pd.DataFrame({"date": dates.normalize()})
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(df[c], errors="coerce").to_numpy()
    vis = next((c for c in ("visible_at", "available_utc") if c in df.columns), None)
    if vis is not None:
        out["available_utc"] = pd.DatetimeIndex(pd.to_datetime(df[vis], utc=True)).as_unit("ns")
    else:
        out["available_utc"] = utc_stamp(out["date"], CLOSE_HOUR_UTC)
    src = str(df["source"].iloc[0]) if "source" in df.columns and len(df) else "dukascopy"
    out = out[out["high"] > out["low"]]
    return to_long(out, src if src.startswith(("dukascopy", "synthetic")) else f"dukascopy:{src}")


def to_long(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Normalise any frame with date, close, available_utc (+ optional open/high/low) to LONG_COLUMNS, one row per
    date, sorted, nothing at or after the cutoff."""
    out = df.copy()
    out["date"] = pd.DatetimeIndex(pd.to_datetime(out["date"])).as_unit("ns").normalize()
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64") if c in out.columns else np.nan
    # a non-positive or missing open/high/low is "no intraday range": keep the close, blank the range
    rng_bad = ~((out["open"] > 0) & (out["high"] > 0) & (out["low"] > 0))
    out.loc[rng_bad & (out["close"] > 0), ["open", "high", "low"]] = np.nan
    out["available_utc"] = pd.DatetimeIndex(pd.to_datetime(out["available_utc"], utc=True)).as_unit("ns")
    out["source"] = source
    out = out[np.isfinite(out["close"].to_numpy()) & (out["available_utc"] < CUTOFF)]
    out = out.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return out[list(LONG_COLUMNS)]


# -------------------------------------------------------------------------------------------------------- parsers

_DATE_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}),([^,]*)")


def parse_bbk_csv(text: str) -> pd.Series:
    """Deutsche Bundesbank time-series CSV (one series; metadata rows, then `YYYY-MM-DD,value,flag`; "." = no value)
    -> float Series indexed by date."""
    dates: list[str] = []
    vals: list[float] = []
    for line in text.lstrip("﻿").splitlines():
        m = _DATE_ROW.match(line.strip())
        if not m:
            continue
        v = m.group(2).strip().strip('"')
        if v in ("", "."):
            continue
        dates.append(m.group(1))
        vals.append(float(v))
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).as_unit("ns")
    return pd.Series(vals, index=idx, dtype="float64").sort_index()


def parse_ecb_csv(text: str) -> pd.Series:
    """ECB data API `format=csvdata` (TIME_PERIOD, OBS_VALUE columns) -> float Series indexed by date."""
    df = pd.read_csv(io.StringIO(text))
    s = pd.Series(pd.to_numeric(df["OBS_VALUE"], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(df["TIME_PERIOD"])).as_unit("ns"), dtype="float64")
    return s.dropna().sort_index()


def parse_eia_xls(path: str | Path) -> pd.Series:
    """EIA petroleum/gas spot history workbook (`hist_xls/<ID>d.xls`, sheet "Data 1": 2 title rows, a header row,
    then Date | value) -> float Series indexed by date. Needs xlrd."""
    raw = pd.read_excel(path, sheet_name="Data 1", header=None, skiprows=3)
    d = pd.to_datetime(raw.iloc[:, 0], errors="coerce")
    v = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
    s = pd.Series(v.to_numpy(), index=pd.DatetimeIndex(d).as_unit("ns"), dtype="float64")
    return s[s.index.notna()].dropna().sort_index()


def fixing_frame(close: pd.Series, source: str, tz: str, hour: int, minute: int = 0) -> pd.DataFrame:
    """A close-only fixing Series (date index) -> long rows stamped `date hh:mm tz`."""
    s = close.dropna()
    s = s[s.index.notna()]
    df = pd.DataFrame({"date": s.index, "close": s.to_numpy()})
    df["available_utc"] = local_stamp(df["date"], tz, hour, minute)
    return to_long(df, source)


# -------------------------------------------------------------------------------------------------------- stitch


def overlap_stat(lower: pd.DataFrame, upper: pd.DataFrame) -> tuple[int, float | None, float | None]:
    m = lower[["date", "close"]].merge(upper[["date", "close"]], on="date", suffixes=("_lo", "_up"))
    m = m[(m["close_lo"] > 0) & (m["close_up"] > 0)]
    if m.empty:
        return 0, None, None
    r = (m["close_lo"] / m["close_up"] - 1.0) * 100.0
    return len(m), float(r.abs().median()), float(r.median())


def stitch(segments: Sequence[pd.DataFrame]) -> tuple[pd.DataFrame, list[OverlapStat]]:
    """Ranked long-row frames, best first -> one series and the overlap statistic at every hand-over.

    Each lower-ranked frame adds only its dates strictly before the series' current first date."""
    parts = [s for s in segments if s is not None and not s.empty]
    if not parts:
        return pd.DataFrame(columns=list(LONG_COLUMNS)), []
    out = parts[0]
    stats: list[OverlapStat] = []
    for seg in parts[1:]:
        first = out["date"].min()
        n, mabs, msig = overlap_stat(seg, out)
        add = seg[seg["date"] < first]
        upper_src = str(out.loc[out["date"] == first, "source"].iloc[0])
        stats.append(OverlapStat(lower=str(seg["source"].iloc[0]), upper=upper_src, n_common=n,
                                 median_abs_pct=mabs, median_signed_pct=msig,
                                 cutover=f"{first:%Y-%m-%d}", rows_added=len(add)))
        if not add.empty:
            out = pd.concat([add, out], ignore_index=True)
    out = out.sort_values("date").reset_index(drop=True)
    validate_long(out)
    return out, stats


def validate_long(df: pd.DataFrame) -> None:
    """The row contract, raised on the first breach (a stitched file that fails is not written)."""
    if list(df.columns) != list(LONG_COLUMNS):
        raise ValueError(f"columns {list(df.columns)} != {list(LONG_COLUMNS)}")
    if df.empty:
        return
    d = pd.DatetimeIndex(df["date"])
    if not d.is_unique:
        raise ValueError("duplicate dates")
    if not d.is_monotonic_increasing:
        raise ValueError("dates not increasing")
    a = pd.DatetimeIndex(df["available_utc"])
    if a.tz is None:
        raise ValueError("available_utc must be tz-aware UTC")
    if not (a[1:] > a[:-1]).all():
        raise ValueError("available_utc not strictly increasing")
    day0 = d.tz_localize("UTC")
    if not ((a >= day0) & (a < day0 + pd.Timedelta(days=2))).all():
        raise ValueError("available_utc outside [date, date + 2 days)")
    if (a >= CUTOFF).any():
        raise ValueError(f"rows at or after the research cutoff {CUTOFF}")
    if not np.isfinite(df["close"].to_numpy(dtype="float64")).all():
        raise ValueError("non-finite close")


def gap_report(df: pd.DataFrame, min_weekdays: int = 5) -> list[tuple[str, str, int]]:
    """Runs of at least `min_weekdays` missing weekdays between consecutive rows: (last date, next date, weekdays)."""
    d = pd.DatetimeIndex(df["date"])
    gaps: list[tuple[str, str, int]] = []
    if len(d) < 2:
        return gaps
    a = d[:-1].to_numpy().astype("datetime64[D]")
    b = d[1:].to_numpy().astype("datetime64[D]")
    missing = np.busday_count(a, b) - 1
    for i in (int(k) for k in np.flatnonzero(missing >= min_weekdays)):
        gaps.append((f"{d[i]:%Y-%m-%d}", f"{d[i + 1]:%Y-%m-%d}", int(missing[i])))
    return gaps


def summary(df: pd.DataFrame) -> dict[str, object]:
    """Rows, range, rows per source and the weekday coverage of the stitched series."""
    d = pd.DatetimeIndex(df["date"])
    wk = int(np.busday_count(d.min().to_numpy().astype("datetime64[D]"),
                             (d.max() + pd.Timedelta(days=1)).to_numpy().astype("datetime64[D]"))) if len(d) else 0
    by_src = df.groupby("source", sort=False)["date"].agg(["min", "max", "count"])
    return {
        "rows": len(df),
        "first": f"{d.min():%Y-%m-%d}" if len(d) else None,
        "last": f"{d.max():%Y-%m-%d}" if len(d) else None,
        "weekday_coverage_pct": round(100.0 * len(df) / wk, 1) if wk else None,
        "sources": {str(k): (f"{v['min']:%Y-%m-%d}", f"{v['max']:%Y-%m-%d}", int(v["count"]))
                    for k, v in by_src.iterrows()},
        "gaps_5plus_weekdays": gap_report(df),
    }

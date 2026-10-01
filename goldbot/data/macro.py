"""Macro and fundamental series with point-in-time columns.

Every row has value_date (what the number is about), available_utc (when the system could have
known it) and vintage (the realtime_start FRED reports, or the publication time for COT/GLD).
Features only ever join on available_utc via `store.asof_join`.
"""
from __future__ import annotations

import io
import zipfile
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

FRED_SERIES = {
    "DTWEXBGS": "broad_dollar",
    "DFII10": "real_yield_10y",
    "DGS10": "nominal_10y",
    "DGS2": "nominal_2y",
    "DFF": "fed_funds_effective",
    "T10YIE": "breakeven_10y",
}
# H.15 series are published the next business day ~16:15 ET; use 16:30 ET as the availability stamp.
FRED_PUBLISH_ET = time(16, 30)
ET = ZoneInfo("America/New_York")


def _next_business_day_at(dates: pd.Series, t: time, tz: ZoneInfo) -> pd.Series:
    d = pd.to_datetime(dates) + pd.offsets.BDay(1)
    local = pd.DatetimeIndex(d) + pd.Timedelta(hours=t.hour, minutes=t.minute)
    return pd.Series(local.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC"))


def fetch_fred(series_id: str, api_key: str, start: str = "2003-01-01") -> pd.DataFrame:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": start,
              "realtime_start": start, "realtime_end": "9999-12-31"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    obs = pd.DataFrame(r.json()["observations"])
    obs = obs[obs["value"] != "."]
    out = pd.DataFrame({
        "series": FRED_SERIES.get(series_id, series_id),
        "value_date": pd.to_datetime(obs["date"]),
        "value": obs["value"].astype(float),
        "vintage": pd.to_datetime(obs["realtime_start"]),
    })
    # available when the vintage was published (next business day after value_date, 16:30 ET),
    # and never earlier than the vintage date itself.
    avail = _next_business_day_at(out["value_date"], FRED_PUBLISH_ET, ET)
    vint_utc = pd.DatetimeIndex(out["vintage"]).tz_localize("UTC") + pd.Timedelta(hours=21)
    out["available_utc"] = pd.Series(pd.DatetimeIndex(avail).where(pd.DatetimeIndex(avail) >= vint_utc, vint_utc))
    out["ts_utc"] = out["available_utc"]
    return out.sort_values("available_utc").reset_index(drop=True)


def fetch_cot_gold(year: int) -> pd.DataFrame:
    """CFTC disaggregated futures-only, COMEX gold (088691). Report date Tuesday, published Friday 15:30 ET."""
    url = f"https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = [n for n in z.namelist() if n.lower().endswith(".txt")][0]
    df = pd.read_csv(z.open(name), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    g = df[df["CFTC_Contract_Market_Code"].astype(str).str.strip() == "088691"].copy()
    report = pd.to_datetime(g["Report_Date_as_YYYY-MM-DD"])
    publish = (report + pd.offsets.Day(3)).dt.normalize() + pd.Timedelta(hours=15, minutes=30)
    avail = pd.DatetimeIndex(publish).tz_localize(ET, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC")
    out = pd.DataFrame({
        "series": "cot_mm_net",
        "value_date": report,
        "value": g["M_Money_Positions_Long_All"].astype(float) - g["M_Money_Positions_Short_All"].astype(float),
        "open_interest": g["Open_Interest_All"].astype(float),
        "vintage": report,
        "available_utc": avail,
    })
    out["ts_utc"] = out["available_utc"]
    return out.sort_values("available_utc").reset_index(drop=True)


def gld_holdings_from_csv(path: str) -> pd.DataFrame:
    """SPDR Gold Shares historical data CSV (Date, Tonnes). Published next business day ~06:30 NY."""
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    date = pd.to_datetime(df[cols["date"]])
    tonnes = df[[c for c in df.columns if "tonne" in c.lower()][0]].astype(float)
    avail = _next_business_day_at(date, time(6, 30), ET)
    out = pd.DataFrame({"series": "gld_tonnes", "value_date": date, "value": tonnes, "vintage": date,
                        "available_utc": avail})
    out["ts_utc"] = out["available_utc"]
    return out


def macro_wide(macro: pd.DataFrame) -> pd.DataFrame:
    """Long -> wide on available_utc, forward-filling each series so asof_join can take one row."""
    if macro.empty:
        return pd.DataFrame(columns=["available_utc"])
    w = macro.pivot_table(index="available_utc", columns="series", values="value", aggfunc="last").sort_index().ffill()
    return w.reset_index()

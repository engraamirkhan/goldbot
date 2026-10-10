"""Macro and fundamental series with point-in-time columns.

Every row has value_date (what the number is about), available_utc (when the system could have
known it) and vintage (the realtime_start FRED reports, the retrieval date for the public fredgraph CSV that the
data-macro workflow publishes on release `macro-v1`, or the publication time for COT/GLD).
Features only ever join on available_utc via `store.asof_join`.
"""
from __future__ import annotations

import io
import zipfile
from datetime import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

FRED_SERIES = {
    "DTWEXBGS": "broad_dollar",
    "DFII10": "real_yield_10y",
    "DGS10": "nominal_10y",
    "DGS2": "nominal_2y",
    "DFF": "fed_funds_effective",
    "T10YIE": "breakeven_10y",
    "GVZCLS": "gold_vix",
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


# --------------------------------------------------------------------------- macro-v1 release (data-macro.yml)
# The series the data-macro workflow publishes: gold's best-documented macro drivers (docs/TRADER_LIFECYCLE.md).
MACRO_RELEASE_SERIES = ("DFII10", "T10YIE", "DTWEXBGS", "GVZCLS", "DGS2")
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"   # public, no API key; latest vintage only
# H.10 (broad dollar) is released weekly, on Monday ~16:15 ET, for the week to the previous Friday.
WEEKLY_H10 = frozenset({"DTWEXBGS"})
# Availability stamp (point in time). A daily FRED value for date D is not known on D:
# * H.15 rates (DFII10, DGS2, and T10YIE derived from them) are published the next business day at ~16:15 ET, which is
#   21:15 UTC in winter (20:15 in summer). 23:00 UTC on the next US business day is after that in both seasons, with
#   margin for FRED's own posting delay. (21:00 UTC would be 16:00 ET in winter: before the release.)
# * GVZCLS is CBOE's 16:15 ET close of day D; FRED posts it the next business day, so the same stamp is conservative.
# * DTWEXBGS: 23:00 UTC on the first business day after the Monday that follows D's week (Tuesday; Wednesday after a
#   Monday holiday), so the weekly release, even when a holiday pushes it to Tuesday afternoon, is always earlier.
# Business days skip US federal holidays (no H.15/H.10 release on them). The cost is at most a day of staleness on
# features that are 20-observation changes; the gain is never seeing a number before it was public.
AVAILABLE_AT_UTC = pd.Timedelta(hours=23)
US_BDAY = CustomBusinessDay(calendar=USFederalHolidayCalendar())


def fred_available_utc(series_id: str, value_date: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Conservative time (UTC) at which the value of `series_id` for each `value_date` was public (see above)."""
    d = pd.DatetimeIndex(pd.to_datetime(value_date)).tz_localize(None).normalize()
    if series_id in WEEKLY_H10:
        d = d + pd.to_timedelta(7 - d.dayofweek, unit="D")        # the Monday after D's week
    day = pd.DatetimeIndex([x + US_BDAY for x in d])
    return (day + AVAILABLE_AT_UTC).tz_localize("UTC")


def parse_fredgraph_csv(text: str, series_id: str) -> pd.DataFrame:
    """fredgraph.csv -> value_date, value. Missing days ('.' or empty, e.g. holidays) are dropped. The date column is
    `observation_date` (current format) or `DATE` (older)."""
    raw = pd.read_csv(io.StringIO(text))
    if series_id not in raw.columns:
        raise ValueError(f"fredgraph CSV for {series_id} has columns {list(raw.columns)}")
    out = pd.DataFrame({"value_date": pd.to_datetime(raw.iloc[:, 0]),
                        "value": pd.to_numeric(raw[series_id], errors="coerce")})
    return out.dropna().reset_index(drop=True)


def fred_frame(series_id: str, obs: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    """Rows for the macro table: series, series_id, value_date, value, vintage (the date the value was retrieved),
    available_utc (fred_available_utc) and ts_utc (= available_utc; the store partitions on it)."""
    v = pd.Timestamp(vintage)
    out = pd.DataFrame({
        "series": FRED_SERIES.get(series_id, series_id),
        "series_id": series_id,
        "value_date": pd.DatetimeIndex(obs["value_date"]).tz_localize(None).normalize(),
        "value": obs["value"].astype(float).to_numpy(),
        "vintage": (v.tz_convert("UTC").tz_localize(None) if v.tzinfo else v).normalize(),
    })
    out["available_utc"] = fred_available_utc(series_id, out["value_date"])
    out["ts_utc"] = out["available_utc"]
    return out


def fetch_fred_csv(series_id: str, start: str = "2003-01-01", *, retries: int = 3,
                   session: requests.Session | None = None) -> pd.DataFrame:
    """One series from the public fredgraph CSV (value_date, value), retried with backoff."""
    import time as _time
    get = session.get if session is not None else requests.get
    last: Exception | None = None
    for i in range(retries):
        try:
            r = get(FRED_CSV_URL, params={"id": series_id, "cosd": start}, timeout=60)
            r.raise_for_status()
            return parse_fredgraph_csv(r.text, series_id)
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if i + 1 < retries:
                _time.sleep(10 * (i + 1))
    raise RuntimeError(f"FRED {series_id}: {last}")


def merge_vintages(prev: pd.DataFrame | None, fresh: pd.DataFrame) -> pd.DataFrame:
    """Add this week's download to the published history without rewriting it.

    fredgraph.csv serves only the latest vintage. A value first seen now keeps its publication-rule available_utc (it
    was public then); a value that differs from the last one published for the same (series, value_date) is a
    revision and becomes a new row with vintage = today and available_utc no earlier than today 23:00 UTC, so the
    revised number is never visible before we could have seen it. Unchanged values add nothing. History before the
    first download carries that download's vintage (for these series, market rates and an index, revisions are rare)."""
    if prev is None or prev.empty:
        return fresh.sort_values(["series", "value_date"]).reset_index(drop=True)
    last = (prev.sort_values("available_utc").drop_duplicates(["series", "value_date"], keep="last")
            [["series", "value_date", "value"]].rename(columns={"value": "prev_value"}))
    m = fresh.merge(last, on=["series", "value_date"], how="left")
    new = m["prev_value"].isna().to_numpy()
    revised = ~new & ~np.isclose(m["value"].to_numpy(float), m["prev_value"].to_numpy(float), rtol=0, atol=1e-9)
    keep = new | revised
    add = m[keep].drop(columns=["prev_value"]).reset_index(drop=True)
    floor = pd.DatetimeIndex(pd.to_datetime(add["vintage"])).tz_localize("UTC") + AVAILABLE_AT_UTC
    rule = pd.DatetimeIndex(pd.to_datetime(add["available_utc"], utc=True))
    add["available_utc"] = rule.where(new[keep] | (rule >= floor), floor)
    add["ts_utc"] = add["available_utc"]
    out = pd.concat([prev, add], ignore_index=True)
    return out.sort_values(["series", "value_date", "available_utc"]).reset_index(drop=True)


# --------------------------------------------------------------------------- derived driver features
# Computed on each series' own observations (20 observations = about four weeks of business days, 252 = a year),
# then joined to bars as-of the derived row's availability. Each series uses its first release per value_date only,
# so a later revision never changes what an earlier bar saw.
DRIVERS: dict[str, tuple[str, str, int]] = {
    # column: (series, transform, window)
    "macro_real_yield_chg20": ("real_yield_10y", "diff", 20),
    "macro_real_yield_z252": ("real_yield_10y", "zscore", 252),
    "macro_dollar_chg20": ("broad_dollar", "pct", 20),
    "macro_gvz": ("gold_vix", "level", 0),
    "macro_gvz_chg20": ("gold_vix", "diff", 20),
}


def first_release(macro: pd.DataFrame, series: str) -> pd.DataFrame:
    """One row per value_date of `series` (the earliest available), in value_date order. available_utc is the running
    maximum: a value computed over earlier observations is known only once all of them are."""
    s = macro[macro["series"] == series]
    if s.empty:
        return pd.DataFrame({"value_date": pd.Series(dtype="datetime64[ns]"), "value": pd.Series(dtype=float),
                             "available_utc": pd.Series(dtype="datetime64[ns, UTC]")})
    s = s.assign(available_utc=pd.DatetimeIndex(pd.to_datetime(s["available_utc"], utc=True)).as_unit("ns"))
    s = s.sort_values("available_utc").drop_duplicates("value_date", keep="first").sort_values("value_date")
    out = s[["value_date", "value", "available_utc"]].reset_index(drop=True)
    out["available_utc"] = out["available_utc"].cummax()
    return out


def driver_frames(macro: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """column -> DataFrame(available_utc, column): each derived driver on its own availability timeline."""
    out: dict[str, pd.DataFrame] = {}
    for col, (series, how, w) in DRIVERS.items():
        s = first_release(macro, series)
        v = s["value"].astype(float)
        if how == "diff":
            x = v.diff(w)
        elif how == "pct":
            x = v.pct_change(w, fill_method=None)
        elif how == "zscore":
            r = v.rolling(w, min_periods=int(w * 0.8))
            x = (v - r.mean()) / r.std()
        else:
            x = v
        f = pd.DataFrame({"available_utc": s["available_utc"], col: x.to_numpy()})
        # several observations can become public at once (a week of H.10 dollar values on one Tuesday): the latest
        # observation is the current value, so keep one row per stamp (an as-of join would pick among ties arbitrarily)
        out[col] = f.drop_duplicates("available_utc", keep="last").dropna().reset_index(drop=True)
    return out


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

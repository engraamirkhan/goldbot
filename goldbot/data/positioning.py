"""Positioning and flow data for research, point in time: CFTC Commitments of Traders for COMEX gold and SPDR Gold
Shares (GLD) holdings. The data-positioning workflow builds the file and publishes it on release `positioning-v1`
(Claude sandboxes cannot reach cftc.gov or spdrgoldshares.com; GitHub's runners can).

Every row: series, series_id, value_date (what the number is about), value, vintage (the date it was retrieved),
available_utc (a conservative time it was public), ts_utc (= available_utc) and source (`cftc` or `spdr`). Features join
only on available_utc via `store.asof_join` (goldbot/features/positioning.py).

COT (source cftc, series_id 088691 = GOLD - COMMODITY EXCHANGE INC.): the disaggregated futures-only report,
  https://www.cftc.gov/files/dea/history/fut_disagg_txt_<year>.zip (2017 on) and fut_disagg_txt_hist_2006_2016.zip.
  Series: cot_mm_long, cot_mm_short, cot_mm_net (managed money), cot_open_interest, cot_comm_net (producer/merchant +
  swap dealers, the disaggregated report's commercials). Positions are as of Tuesday; the report is released Friday at
  15:30 ET. `cot_available_utc` stamps that Friday (20:30 UTC, 19:30 UTC in US daylight time, via zoneinfo). In a week
  with a US federal holiday (Monday of the report week to the Friday) the CFTC releases later, so the stamp moves to
  the next US business day after the Friday, 15:30 ET (conservative: the actual release is never later than that in a
  normal holiday week). Unscheduled federal closures (national days of mourning, CLOSURE_DAYS) count as holidays too;
  a closure the calendar misses only risks a stamp that is too early, so the list errs on the side of including one.
  Government shutdowns stopped the report for weeks; SHUTDOWN_FLOORS holds conservative
  not-before times for those report dates, and `merge_releases` catches any future delay (a row that should have been
  in an earlier download but was not is stamped no earlier than the download that first saw it).
  Schedule assumption: the Saturday 05:23 UTC workflow run expects the annual zip (fut_disagg_txt_<year>.zip) to already
  carry Friday's report (in a holiday week the report may come after the run and is picked up a week later). If the
  CFTC updates the zip later than that, the report is missing from that run and the next run treats it as late
  (`merge_releases`: its rule stamp is before the previous successful download) and stamps it no earlier than the
  download that first saw it. A lag therefore only makes the features staler; it never moves a value earlier.

GLD (source spdr): the issuer's public historical archive,
  https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv (no key). Series gld_tonnes: the trust's
  gold in tonnes at 4:15 pm NYT on value_date. Stamped the next US business day at 14:00 UTC (conservative). The site
  sits behind bot protection and has served HTML instead of CSV to scripted clients; `fetch_gld` raises
  GldSourceUnavailable in that case (no scraping, no browser emulation) and the workflow keeps the published rows.
"""
from __future__ import annotations

import io
import json
import time as _time
import zipfile
from datetime import time
from typing import Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

ET = ZoneInfo("America/New_York")
COLUMNS = ("series", "series_id", "value_date", "value", "vintage", "available_utc", "ts_utc", "source")

# --------------------------------------------------------------------------------------------------- CFTC COT
COT_GOLD_CODE = "088691"
COT_FIRST_YEAR = 2006                       # the disaggregated report (managed money) starts in June 2006
COT_HIST_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_hist_2006_2016.zip"
COT_YEAR_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
COT_RELEASE_ET = time(15, 30)
COT_SERIES = ("cot_mm_long", "cot_mm_short", "cot_mm_net", "cot_open_interest", "cot_comm_net")
# Unscheduled federal closures (executive orders; federal offices and the CFTC closed, markets closed): treated as
# federal holidays for both the COT holiday-week rule and the next-business-day stamps. Add new ones here; a missing
# one can stamp a release too early, an extra one only makes it later.
CLOSURE_DAYS = pd.DatetimeIndex([
    "2018-12-05",   # national day of mourning, President George H. W. Bush
    "2025-01-09",   # national day of mourning, President Jimmy Carter
])
_HOLIDAYS = USFederalHolidayCalendar().holidays(pd.Timestamp("1990-01-01"), pd.Timestamp("2100-12-31")).union(CLOSURE_DAYS)
US_CLOSED_BDAY = CustomBusinessDay(holidays=list(_HOLIDAYS))   # US business day: federal holidays and CLOSURE_DAYS skipped


def us_holidays(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Federal holidays (observed) plus CLOSURE_DAYS between start and end, inclusive."""
    return _HOLIDAYS[(_HOLIDAYS >= start) & (_HOLIDAYS <= end)]


# Government shutdowns (CFTC stopped publishing; the backlog came out on a catch-up schedule afterwards): report dates
# in [first, last] are not treated as public before `not_before`. The dates are deliberately late (end of the catch-up
# as best known, rounded up); being late only makes the features staler for those weeks. The 2013 and 2018-19 rows are
# UNVERIFIED against the CFTC press releases (HANDOFF.md, positioning bullet); confirm them before reading COT results
# for those weeks. 2025: CFTC release 9147-25 (9 Dec 2025) accelerated the catch-up so that the last delayed report
# (as of 23 Dec 2025) was published 29 Dec 2025 and publication returned to the normal schedule (the 23 Dec 2025 CFTC
# update published the 16 Dec data on schedule); the floor is that date plus a margin, 3 Jan 2026. The report of
# 30 Dec 2025 onwards follows the normal rule (a New Year holiday week: Monday 5 Jan 2026).
SHUTDOWN_FLOORS: tuple[tuple[str, str, str], ...] = (
    ("2013-09-30", "2013-11-19", "2013-11-30 00:00"),
    ("2018-12-24", "2019-03-05", "2019-03-11 00:00"),
    ("2025-09-30", "2025-12-23", "2026-01-03 00:00"),
)


def _naive_days(d: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(d))
    return (idx.tz_localize(None) if idx.tz is not None else idx).normalize()


def cot_available_utc(report_date: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Conservative UTC time each COT report (positions as of `report_date`, normally a Tuesday) was public: the
    Friday of the report week at 15:30 ET, or 15:30 ET on the next US business day after that Friday when a federal
    holiday falls on Monday..Friday of the week; never before a shutdown floor (module docstring)."""
    d = _naive_days(report_date)
    if len(d) == 0:
        return pd.DatetimeIndex([], tz="UTC")
    friday = d + pd.to_timedelta((4 - d.dayofweek) % 7, unit="D")
    monday = friday - pd.Timedelta(days=4)
    hol = us_holidays(monday.min(), friday.max())
    holiday_week = np.array([bool(((hol >= m) & (hol <= f)).any()) for m, f in zip(monday, friday)])
    release = pd.DatetimeIndex([f + US_CLOSED_BDAY if h else f for f, h in zip(friday, holiday_week)])
    local = release + pd.Timedelta(hours=COT_RELEASE_ET.hour, minutes=COT_RELEASE_ET.minute)
    out = local.tz_localize(ET).tz_convert("UTC")
    for first, last, not_before in SHUTDOWN_FLOORS:
        floor = pd.Timestamp(not_before, tz="UTC")
        hit = (d >= pd.Timestamp(first)) & (d <= pd.Timestamp(last)) & (out < floor)
        out = out.where(~hit, floor)
    return pd.DatetimeIndex(out).as_unit("ns")


def _norm(c: str) -> str:
    """CFTC header -> lower snake case; their files spell some columns with a double underscore (Swap__Positions_...)."""
    c = c.strip().lower()
    while "__" in c:
        c = c.replace("__", "_")
    return c


def parse_cot(text_or_bytes: str | bytes) -> pd.DataFrame:
    """One disaggregated futures-only file (CSV text) -> gold rows: report_date, mm_long, mm_short, open_interest,
    comm_net. Raises ValueError when the expected columns are missing (a format change must not pass silently)."""
    buf = io.BytesIO(text_or_bytes) if isinstance(text_or_bytes, bytes) else io.StringIO(text_or_bytes)
    raw = pd.read_csv(buf, low_memory=False, dtype={"CFTC_Contract_Market_Code": str})
    raw.columns = [_norm(c) for c in raw.columns]
    date_col = next((c for c in ("report_date_as_yyyy-mm-dd", "report_date_as_mm_dd_yyyy") if c in raw.columns), None)
    need = ["cftc_contract_market_code", "open_interest_all", "m_money_positions_long_all", "m_money_positions_short_all",
            "prod_merc_positions_long_all", "prod_merc_positions_short_all", "swap_positions_long_all",
            "swap_positions_short_all"]
    missing = [c for c in need if c not in raw.columns] + ([] if date_col else ["report_date"])
    if missing:
        raise ValueError(f"COT file lacks {missing}")
    code = raw["cftc_contract_market_code"].astype(str).str.strip().str.zfill(6)
    g = raw[code == COT_GOLD_CODE]

    def num(c: str) -> pd.Series:
        return pd.to_numeric(g[c].astype(str).str.replace(",", "").str.strip(), errors="coerce")

    out = pd.DataFrame({
        "report_date": pd.to_datetime(g[date_col]).dt.normalize(),
        "mm_long": num("m_money_positions_long_all"),
        "mm_short": num("m_money_positions_short_all"),
        "open_interest": num("open_interest_all"),
        "comm_net": (num("prod_merc_positions_long_all") - num("prod_merc_positions_short_all")
                     + num("swap_positions_long_all") - num("swap_positions_short_all")),
    })
    return out.dropna().drop_duplicates("report_date", keep="last").sort_values("report_date").reset_index(drop=True)


def cot_frame(parsed: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    """parse_cot rows -> long release rows (COLUMNS), one per series and report date.

    Known look-ahead in the backfill: the CFTC history files hold the current (corrected) value for each report date,
    not the value first published. The first run (2006 onwards) therefore stamps those corrected values with the
    first-release available_utc. CFTC revisions of past reports are rare and small, so this is a small, accepted
    look-ahead for the backfilled history only; from the first published file on, `merge_releases` records a later
    revision as a new row available no earlier than the download that saw it, and first-release values are kept."""
    if parsed.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    days = _naive_days(parsed["report_date"])
    avail = cot_available_utc(days)
    values = {"cot_mm_long": parsed["mm_long"], "cot_mm_short": parsed["mm_short"],
              "cot_mm_net": parsed["mm_long"] - parsed["mm_short"], "cot_open_interest": parsed["open_interest"],
              "cot_comm_net": parsed["comm_net"]}
    parts = [pd.DataFrame({"series": s, "series_id": COT_GOLD_CODE, "value_date": days,
                           "value": v.astype(float).to_numpy(), "vintage": _vintage_day(vintage),
                           "available_utc": avail, "source": "cftc"}) for s, v in values.items()]
    out = pd.concat(parts, ignore_index=True)
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)]


def _get(url: str, *, retries: int, session: requests.Session | None, timeout: int = 120,
         sleep: Callable[[float], None] = _time.sleep) -> requests.Response:
    get = session.get if session is not None else requests.get
    last: Exception | None = None
    for i in range(retries):
        try:
            r = get(url, timeout=timeout, headers={"User-Agent": "goldbot-data (research; GitHub Actions)"})
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            if i + 1 < retries:
                sleep(10 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def fetch_cot(years: list[int], *, retries: int = 3, session: requests.Session | None = None) -> pd.DataFrame:
    """Gold rows (parse_cot) for the given years: 2006-2016 from the historical bundle, later years one zip each."""
    frames = []
    urls = ([COT_HIST_URL] if any(y <= 2016 for y in years) else []) + [COT_YEAR_URL.format(year=y) for y in years if y > 2016]
    for url in urls:
        r = _get(url, retries=retries, session=session)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        for name in z.namelist():
            if name.lower().endswith((".txt", ".csv")):
                frames.append(parse_cot(z.read(name)))
    if not frames:
        raise RuntimeError("COT: no data file in the downloaded archives")
    df = pd.concat(frames, ignore_index=True)
    df = df[df["report_date"].dt.year.isin(years)]
    return df.drop_duplicates("report_date", keep="last").sort_values("report_date").reset_index(drop=True)


# --------------------------------------------------------------------------------------------------- GLD holdings
GLD_URL = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"
GLD_AVAILABLE_AT_UTC = pd.Timedelta(hours=14)


class GldSourceUnavailable(RuntimeError):
    """The issuer's archive could not be read as CSV (blocked, moved or reformatted). Not scraped around."""


def gld_available_utc(value_date: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The next US business day (federal holidays and CLOSURE_DAYS skipped) after value_date, 14:00 UTC."""
    d = _naive_days(value_date)
    return (pd.DatetimeIndex([x + US_CLOSED_BDAY for x in d]) + GLD_AVAILABLE_AT_UTC).tz_localize("UTC").as_unit("ns")


def parse_gld_csv(text: str) -> pd.DataFrame:
    """The SPDR archive CSV -> value_date, tonnes. The header row is the first line naming both a date and a tonnes
    column (the file has had preamble lines); rows without a number (holidays, 'NYSE closed') are dropped. Raises
    GldSourceUnavailable for anything that is not that table (an HTML challenge page, a changed layout)."""
    if text.lstrip()[:1] == "<":
        raise GldSourceUnavailable("GLD archive returned HTML, not CSV (bot protection or a moved page)")
    lines = text.splitlines()
    head = next((i for i, ln in enumerate(lines[:50]) if "date" in ln.lower() and "tonnes" in ln.lower()), None)
    if head is None:
        raise GldSourceUnavailable("GLD archive has no Date/Tonnes header")
    raw = pd.read_csv(io.StringIO("\n".join(lines[head:])), skipinitialspace=True)
    raw.columns = [str(c).strip() for c in raw.columns]
    date_col = next(c for c in raw.columns if c.lower().startswith("date"))
    ton_col = next(c for c in raw.columns if "tonnes" in c.lower())
    dates = pd.to_datetime(raw[date_col].astype(str).str.strip(), format="%d-%b-%Y", errors="coerce")
    dates = dates.fillna(pd.to_datetime(raw[date_col].astype(str).str.strip(), format="mixed", errors="coerce"))
    tonnes = pd.to_numeric(raw[ton_col].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    out = pd.DataFrame({"value_date": dates, "tonnes": tonnes}).dropna()
    out = out[out["tonnes"] > 0]
    if out.empty:
        raise GldSourceUnavailable("GLD archive parsed to no rows")
    return out.drop_duplicates("value_date", keep="last").sort_values("value_date").reset_index(drop=True)


def fetch_gld(*, retries: int = 3, session: requests.Session | None = None) -> pd.DataFrame:
    try:
        r = _get(GLD_URL, retries=retries, session=session)
    except RuntimeError as exc:
        raise GldSourceUnavailable(str(exc)) from exc
    return parse_gld_csv(r.text)


def gld_frame(parsed: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    days = _naive_days(parsed["value_date"])
    out = pd.DataFrame({"series": "gld_tonnes", "series_id": "GLD", "value_date": days,
                        "value": parsed["tonnes"].astype(float).to_numpy(), "vintage": _vintage_day(vintage),
                        "available_utc": gld_available_utc(days), "source": "spdr"})
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)]


# --------------------------------------------------------------------------------------------------- vintages
def _vintage_day(v: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(v)
    return (t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t).normalize()


def merge_releases(prev: pd.DataFrame | None, fresh: pd.DataFrame, retrieved_utc: pd.Timestamp,
                   last_ok: dict[str, pd.Timestamp] | None = None) -> pd.DataFrame:
    """Add one download to the published history without rewriting it.

    * A (series, value_date) first seen now keeps its publication-rule available_utc, unless that time is before the
      previous successful download of its source (`last_ok[source]`): the row should have been there then and was not,
      so the release was late (a shutdown, an outage) and it is stamped no earlier than `retrieved_utc`.
    * A value that differs from the last one published is a revision: a new row, available no earlier than
      `retrieved_utc`. The first release stays, so research can always use first-release values.
    * Unchanged values add nothing."""
    now = pd.Timestamp(retrieved_utc)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    fresh = fresh.copy()
    fresh["available_utc"] = pd.DatetimeIndex(pd.to_datetime(fresh["available_utc"], utc=True)).as_unit("ns")
    if prev is None or prev.empty:
        out = fresh
    else:
        prev = prev.copy()
        prev["available_utc"] = pd.DatetimeIndex(pd.to_datetime(prev["available_utc"], utc=True)).as_unit("ns")
        last = (prev.sort_values("available_utc").drop_duplicates(["series", "value_date"], keep="last")
                [["series", "value_date", "value"]].rename(columns={"value": "prev_value"}))
        m = fresh.merge(last, on=["series", "value_date"], how="left")
        new = m["prev_value"].isna().to_numpy()
        revised = ~new & ~np.isclose(m["value"].to_numpy(float), m["prev_value"].to_numpy(float), rtol=0, atol=1e-9)
        rule = pd.DatetimeIndex(m["available_utc"])
        ok = {k: str(pd.Timestamp(v)) for k, v in (last_ok or {}).items()}
        seen_before = pd.DatetimeIndex(pd.to_datetime(m["source"].map(ok), utc=True)).as_unit("ns")
        late = new & np.asarray((rule < seen_before), dtype=bool)
        bump = (revised | late) & np.asarray(rule < now, dtype=bool)
        m["available_utc"] = rule.where(~bump, now)
        out = pd.concat([prev, m[new | revised].drop(columns=["prev_value"])], ignore_index=True)
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)].sort_values(["series", "value_date", "available_utc"]).reset_index(drop=True)


class HistoryLoss(ValueError):
    """A frame about to be published would drop or change rows of the previously published file."""


def check_keeps_history(prev: pd.DataFrame | None, merged: pd.DataFrame) -> None:
    """Raise HistoryLoss unless `merged` keeps every previously published row unchanged and has, for each source, at
    least as many rows as `prev`. The publish step overwrites the release asset, so this is the last line that keeps
    revision rows, late-release stamps and source history (GLD while its site is blocked) from being lost."""
    if prev is None or prev.empty:
        return
    for src, n_prev in prev.groupby("source").size().items():
        n_new = int((merged["source"] == src).sum())
        if n_new < n_prev:
            raise HistoryLoss(f"{src}: {n_new:,} rows would replace {n_prev:,} published rows")
    key = ["series", "value_date", "available_utc", "value"]

    def norm(df: pd.DataFrame) -> pd.DataFrame:
        out = df[key].copy()
        out["value_date"] = _naive_days(out["value_date"])
        out["available_utc"] = pd.DatetimeIndex(pd.to_datetime(out["available_utc"], utc=True)).as_unit("ns")
        out["value"] = out["value"].astype(float).round(9)
        return out

    m = norm(prev).merge(norm(merged).drop_duplicates(), on=key, how="left", indicator=True)
    lost = m[m["_merge"] == "left_only"]
    if len(lost):
        sample = lost.head(3)[key].to_dict("records")
        raise HistoryLoss(f"{len(lost):,} published rows missing or changed, e.g. {sample}")


# --------------------------------------------------------------------------------------------------- the file
META_KEY = b"goldbot.positioning.last_ok"


def write_release(df: pd.DataFrame, path: str, last_ok: dict[str, pd.Timestamp]) -> None:
    """Parquet with the last successful download time per source in the schema metadata (merge_releases reads it)."""
    table = pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[META_KEY] = json.dumps({k: pd.Timestamp(v).isoformat() for k, v in last_ok.items()}).encode()
    pq.write_table(table.replace_schema_metadata(meta), path, compression="zstd")


def read_release(path: str) -> tuple[pd.DataFrame, dict[str, pd.Timestamp]]:
    table = pq.read_table(path)
    raw = (table.schema.metadata or {}).get(META_KEY)
    last_ok = {k: pd.Timestamp(v) for k, v in json.loads(raw).items()} if raw else {}
    return table.to_pandas(), last_ok

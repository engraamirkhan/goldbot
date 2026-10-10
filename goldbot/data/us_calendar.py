"""Point-in-time US economic calendar, tiers 1 and 2, 2010 onwards (BACKLOG 18 data part, playbook gap G-3; research
finding E-1: H-16/H-17/H-18 need a historical tier-1 schedule). The data-us-calendar workflow builds the file and
publishes it on release `calendar-v1` (Claude sandboxes cannot reach FRED or federalreserve.gov; GitHub's runners can).

Tiers keep the labels of goldbot/data/econ_calendar.py and settings `risk.blackout.events`:
* tier 1 (the RiskGate blackout list): CPI, NFP (Employment Situation), PCE (Personal Income and Outlays), FOMC
  statement and FOMC press conference;
* tier 2 (G-3): PPI, retail sales, GDP (every estimate, the advance one included: GDP is not on the tier-1 list in
  code), JOLTS, weekly jobless claims, ISM manufacturing and services, FOMC minutes. Fed speakers are not covered (no
  free, structured historical schedule).

Sources and licence (docs/decisions/0007-us-calendar-source.md):
* FRED API `fred/release/dates` (Federal Reserve Bank of St. Louis; ALFRED keeps past release dates) for the agency
  releases (RELEASES). A free API key is needed (repo secret FRED_API_KEY; never in code or logs). The dates are
  scheduling facts of US federal agencies (BLS, Census, BEA, DOL: US government works, public domain); FRED's API
  terms ask for the notice "This product uses the FRED(R) API but is not endorsed or certified by the Federal Reserve
  Bank of St. Louis" and no implied endorsement. No free, licence-compatible consensus source exists, so the file has
  no actual/consensus columns.
* FOMC: the Federal Reserve Board's meeting calendars (federalreserve.gov `fomccalendars.htm` for the recent years and
  the year ahead, `fomchistorical<year>.htm` before that; US government work, public domain). Scheduled meetings
  only: unscheduled meetings, conference calls, notation votes and cancelled meetings are not schedulable events. A
  press conference is listed where the page links one, and at every meeting from 2019 (the Chair's practice since
  then; the link appears only after the meeting). Minutes: the "Released" date the page gives, else the statement
  + 21 days (the practice since 2005), 14:00 ET.
* ISM manufacturing and services are not on FRED and ISM's content is licensed, so their dates are rule-derived
  (source `rule`): first and third US business day of the month, 10:00 ET. ISM occasionally deviates (unverified).

Times are New York wall times (per type; FOMC per era: statement 14:15 until April 2011, 12:30 on 2011-2012 press
conference days and 14:15 otherwise, 14:00 from 2013; press conference 14:15 in 2011-2012, 14:30 from 2013),
converted to UTC per date via zoneinfo, so DST is right on both sides of every switch. FRED dates are dates only; a
release moved within its day is not seen.

Point in time. A schedule is known before the event, but how long before is not recorded for the past. Rule:
`available_utc = event_utc - SCHEDULE_LEAD` (7 days; the agencies publish their calendars for the year ahead each
autumn and the FOMC its meetings a year ahead, so a week is conservative), raised for agency releases (not the Fed,
which is self-funded) to a disruption floor for events on or after a government shutdown began (SHUTDOWN_FLOORS:
rescheduled dates were announced only after reopening), and never later than the event itself. Features cap their
horizon at the same lead, so history and live data look the same. Going forward `merge_calendar` keeps the history as
published: an event first seen in a later run although its rule stamp has already passed was added or moved late, so
it is stamped at the download that first saw it; a future event that disappears adds a `cancelled` row at that
download; a cancelled event that comes back adds a `scheduled` row. Nothing is rewritten.

Every row: event_id (`<event_type>:<ET date>`), event_type, tier (1 | 2), release_id (FRED id, -1 otherwise),
event_utc, status (scheduled | cancelled), available_utc, ts_utc (= available_utc), vintage (retrieval date), source
(`fred` | `fed` | `rule`). Features join only via `store.asof_join` on available_utc (goldbot/features/us_calendar.py).
"""
from __future__ import annotations

import json
import re
import time as _time
from datetime import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from goldbot.base import FrozenRecord
from goldbot.data.macro import US_BDAY
from goldbot.data.timeutil import epoch_ns

ET = ZoneInfo("America/New_York")
COLUMNS = ("event_id", "event_type", "tier", "release_id", "event_utc", "status", "available_utc", "ts_utc",
           "vintage", "source")
COVERAGE_START = pd.Timestamp("2010-01-01")
SCHEDULE_LEAD = pd.Timedelta(days=7)
RULE_HORIZON = pd.Timedelta(days=400)      # rule-derived events are generated this far ahead of each run
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
FRED_RELEASE_URL = "https://api.stlouisfed.org/fred/release"
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_HISTORICAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
SCHEDULED, CANCELLED = "scheduled", "cancelled"
USER_AGENT = "goldbot-data (research; GitHub Actions)"


class Release(FrozenRecord):
    event_type: str
    tier: int
    release_id: int          # FRED release id
    name: str                # substring the FRED release name must contain (guards against a wrong id)
    at_et: time              # scheduled release time, New York


RELEASES: tuple[Release, ...] = (
    Release(event_type="cpi", tier=1, release_id=10, name="Consumer Price Index", at_et=time(8, 30)),
    Release(event_type="nfp", tier=1, release_id=50, name="Employment Situation", at_et=time(8, 30)),
    Release(event_type="pce", tier=1, release_id=54, name="Personal Income and Outlays", at_et=time(8, 30)),
    Release(event_type="ppi", tier=2, release_id=46, name="Producer Price Index", at_et=time(8, 30)),
    Release(event_type="retail_sales", tier=2, release_id=9, name="Retail", at_et=time(8, 30)),
    Release(event_type="gdp", tier=2, release_id=53, name="Gross Domestic Product", at_et=time(8, 30)),
    Release(event_type="jolts", tier=2, release_id=192, name="Job Openings and Labor Turnover", at_et=time(10, 0)),
    Release(event_type="jobless_claims", tier=2, release_id=180, name="Unemployment Insurance Weekly Claims",
            at_et=time(8, 30)),
)
FOMC_TYPES = {"fomc_statement": 1, "fomc_press_conference": 1, "fomc_minutes": 2}
FOMC_MINUTES_AFTER = pd.Timedelta(days=21)
FOMC_MINUTES_ET = time(14, 0)
PRESS_EVERY_MEETING_FROM = pd.Timestamp("2019-01-01")
ISM = {"ism_manufacturing": 1, "ism_services": 3}      # n-th US business day of the month
ISM_ET = time(10, 0)
TIERS: dict[str, int] = {**{r.event_type: r.tier for r in RELEASES}, **FOMC_TYPES, **{t: 2 for t in ISM}}
EVENT_TYPES = tuple(TIERS)

# (shutdown start, ET date): agency events on or after the start are not known before the floor (revised schedules
# were announced after reopening; floors are a few weeks after it, conservative, unverified to the day).
SHUTDOWN_FLOORS: tuple[tuple[pd.Timestamp, pd.Timestamp], ...] = (
    (pd.Timestamp("2013-10-01"), pd.Timestamp("2013-10-31")),     # reopened 2013-10-17
    (pd.Timestamp("2018-12-22"), pd.Timestamp("2019-02-15")),     # reopened 2019-01-25 (Commerce: Census, BEA)
    (pd.Timestamp("2025-10-01"), pd.Timestamp("2025-12-05")),     # reopened 2025-11-12
)


# --------------------------------------------------------------------------------------------------- timing
def et_to_utc(dates: pd.Series | pd.DatetimeIndex | list[Any], at: time | list[time]) -> pd.DatetimeIndex:
    """ET calendar dates at a New York wall time (one, or one per date) -> UTC instants, DST per date (08:30 to 14:30
    are never in a DST gap or fold)."""
    d = pd.DatetimeIndex(pd.to_datetime(pd.Series(dates))).tz_localize(None).normalize()
    times = at if isinstance(at, list) else [at] * len(d)
    local = d + pd.to_timedelta([t.hour * 60 + t.minute for t in times], unit="min")
    return pd.DatetimeIndex(local.tz_localize(ET).tz_convert("UTC")).as_unit("ns")


def rule_available_utc(event_utc: pd.Series | pd.DatetimeIndex, *, shutdown_floors: bool = True) -> pd.DatetimeIndex:
    """event - SCHEDULE_LEAD, raised (agency releases: `shutdown_floors`) to the shutdown floor for events on or after
    a shutdown began, never after the event."""
    ev = pd.DatetimeIndex(pd.to_datetime(pd.Series(event_utc), utc=True)).as_unit("ns")
    avail = ev - SCHEDULE_LEAD
    if shutdown_floors:
        ev_et_day = ev.tz_convert(ET).tz_localize(None).normalize()
        for start, floor_day in SHUTDOWN_FLOORS:
            floor = pd.Timestamp(floor_day, tz=ET).tz_convert("UTC")
            hit = np.asarray(ev_et_day >= start) & np.asarray(avail < floor)
            avail = avail.where(~hit, floor)
    return avail.where(avail <= ev, ev)


def nth_us_business_days(start: pd.Timestamp, end: pd.Timestamp, n: int) -> pd.DatetimeIndex:
    """The n-th US business day (federal holidays skipped) of every month from start's month to end's."""
    months = pd.date_range(pd.Timestamp(start).normalize().replace(day=1), pd.Timestamp(end), freq="MS")
    out = []
    for m in months:
        d = m - pd.Timedelta(days=1)
        for _ in range(n):
            d = d + US_BDAY
        out.append(d)
    return pd.DatetimeIndex(out)


def fomc_statement_et(day: pd.Timestamp, press: bool) -> time:
    if day < pd.Timestamp("2011-04-27"):
        return time(14, 15)
    if day < pd.Timestamp("2013-01-01"):
        return time(12, 30) if press else time(14, 15)
    return time(14, 0)


def fomc_press_et(day: pd.Timestamp) -> time:
    return time(14, 15) if day < pd.Timestamp("2013-01-01") else time(14, 30)


# --------------------------------------------------------------------------------------------------- rows
def _naive_day(v: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(v)
    return (t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t).normalize()


def event_rows(event_type: str, release_id: int, et_dates: pd.DatetimeIndex, at: time | list[time],
               vintage: pd.Timestamp, source: str) -> pd.DataFrame:
    """Scheduled rows for one event type with the rule availability stamp (`at`: one time, or one per date)."""
    days = pd.DatetimeIndex(et_dates).tz_localize(None).normalize()
    times = at if isinstance(at, list) else [at] * len(days)
    keep = np.asarray(days >= COVERAGE_START) & ~pd.Series(days).duplicated().to_numpy()
    order = [i for i in np.argsort(days.to_numpy(), kind="stable") if keep[i]]
    days, times = days[order], [times[i] for i in order]
    ev = et_to_utc(days, times)
    out = pd.DataFrame({
        "event_id": [f"{event_type}:{d:%Y-%m-%d}" for d in days], "event_type": event_type,
        "tier": TIERS[event_type], "release_id": int(release_id), "event_utc": ev, "status": SCHEDULED,
        "available_utc": rule_available_utc(ev, shutdown_floors=source != "fed"), "vintage": _naive_day(vintage),
        "source": source})
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)]


def rule_rows(now: pd.Timestamp, vintage: pd.Timestamp) -> pd.DataFrame:
    """ISM manufacturing and services from COVERAGE_START to now + RULE_HORIZON."""
    end = _naive_day(now)
    return pd.concat([event_rows(t, -1, nth_us_business_days(COVERAGE_START, end + RULE_HORIZON, n), ISM_ET, vintage,
                                 "rule") for t, n in ISM.items()], ignore_index=True)


def fomc_rows(meetings: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    """Scheduled FOMC meetings (parse_fomc_*: day = the statement day, press, minutes_day) -> statement and press
    conference (tier 1) and minutes (tier 2) rows."""
    if meetings.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    m = meetings.drop_duplicates("day", keep="last").sort_values("day").reset_index(drop=True)
    days = pd.DatetimeIndex(m["day"])
    press = m["press"].astype(bool).to_numpy() | np.asarray(days >= PRESS_EVERY_MEETING_FROM)
    released = pd.DatetimeIndex(pd.to_datetime(m["minutes_day"]))
    minutes = released.where(~np.asarray(released.isna(), dtype=bool),days + FOMC_MINUTES_AFTER)
    return pd.concat([
        event_rows("fomc_statement", -1, days, [fomc_statement_et(d, bool(p)) for d, p in zip(days, press)], vintage,
                   "fed"),
        event_rows("fomc_press_conference", -1, days[press], [fomc_press_et(d) for d in days[press]], vintage, "fed"),
        event_rows("fomc_minutes", -1, minutes, FOMC_MINUTES_ET, vintage, "fed"),
    ], ignore_index=True)


def calendar_frame(fred_dates: dict[str, pd.DatetimeIndex], now: pd.Timestamp,
                   fomc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Fresh rows for one run: each fetched FRED release in `fred_dates` (event_type -> ET dates), the FOMC meetings
    (`fomc`, when fetched) and the rule-derived ISM rows."""
    by_type = {r.event_type: r for r in RELEASES}
    frames = [event_rows(t, by_type[t].release_id, d, by_type[t].at_et, now, "fred")
              for t, d in fred_dates.items() if t in by_type]
    if fomc is not None:
        frames.append(fomc_rows(fomc, now))
    frames.append(rule_rows(now, now))
    return pd.concat([f for f in frames if len(f)], ignore_index=True)


# --------------------------------------------------------------------------------------------------- Fed calendars
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_SKIP = re.compile(r"unscheduled|cancel|notation|conference call", re.I)
_RELEASED = re.compile(r"Released\s+([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")


class FedCalendarError(RuntimeError):
    """A Fed calendar page could not be fetched or no longer has the expected structure."""


def _month(s: str) -> int:
    m = _MONTHS.get(s.strip().lower()[:3])
    if m is None:
        raise FedCalendarError(f"unknown month {s!r}")
    return m


def _meeting(year: int, months: str, days: str, chunk: str) -> dict[str, Any] | None:
    """One meeting block -> day (the statement day: the meeting's last day), press, minutes_day; None when it is not a
    scheduled meeting. `months` is "June", "Apr/May" or "April/May"; `days` "16-17*", "30-1" or "October 31-November 1"
    style."""
    if _SKIP.search(days) or _SKIP.search(months):
        return None
    ms = [p for p in months.strip().split("/") if p]
    found = re.findall(r"(?:([A-Za-z]+)\s+)?(\d{1,2})", days)
    if not found or not ms:
        return None
    end_month = _month(found[-1][0] or ms[-1])
    end_year = year + 1 if end_month < _month(ms[0]) else year      # a meeting across New Year (none so far)
    day = pd.Timestamp(year=end_year, month=end_month, day=int(found[-1][1]))
    rel = _RELEASED.search(chunk)
    minutes = pd.Timestamp(year=int(rel.group(3)), month=_month(rel.group(1)), day=int(rel.group(2))) if rel else pd.NaT
    return {"day": day, "press": "fomcpresconf" in chunk, "minutes_day": minutes}


def parse_fomc_current(html: str) -> pd.DataFrame:
    """fomccalendars.htm (recent years and the year ahead): one panel per year, one `fomc-meeting` row per meeting."""
    rows = []
    panels = re.split(r"(\d{4}) FOMC Meetings", html)
    for i in range(1, len(panels) - 1, 2):
        year = int(panels[i])
        for block in re.split(r'class="[^"]*\brow fomc-meeting\b[^"]*"', panels[i + 1])[1:]:
            mo = re.search(r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>", block)
            da = re.search(r"fomc-meeting__date[^>]*>([^<]+)<", block)
            if mo and da:
                r = _meeting(year, mo.group(1), da.group(1), block)
                if r:
                    rows.append(r)
    return pd.DataFrame(rows, columns=["day", "press", "minutes_day"])


def parse_fomc_historical(html: str) -> pd.DataFrame:
    """fomchistorical<year>.htm: one `<h5>` heading per meeting, "<Month[/Month]> <d>[-<d>] Meeting - <year>"."""
    rows = []
    parts = re.split(r"<h5[^>]*>\s*([^<]*?)\s+-\s+(\d{4})\s*</h5>", html)
    for i in range(1, len(parts) - 2, 3):
        head, year, chunk = parts[i], int(parts[i + 1]), parts[i + 2]
        mm = re.match(r"([A-Za-z]+(?:/[A-Za-z]+)?)\s+(.*)$", head)
        if not mm or not re.search(r"\bMeeting\b", head):
            continue
        r = _meeting(year, mm.group(1), mm.group(2).replace("Meeting", ""), chunk)
        if r:
            rows.append(r)
    return pd.DataFrame(rows, columns=["day", "press", "minutes_day"])


def _get_text(url: str, *, retries: int, session: requests.Session | None, sleep: Callable[[float], None]) -> str:
    get = session.get if session is not None else requests.get
    last = ""
    for i in range(retries):
        try:
            r = get(url, timeout=60, headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                return str(r.text)
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = type(exc).__name__
        if i + 1 < retries:
            sleep(10 * (i + 1))
    raise FedCalendarError(f"{url}: {last}")


def fetch_fomc(*, retries: int = 3, session: requests.Session | None = None,
               sleep: Callable[[float], None] = _time.sleep) -> pd.DataFrame:
    """Scheduled FOMC meetings from COVERAGE_START: the current calendar page plus the historical page of every
    earlier year. A page with too few meetings (format change) fails the source."""
    cur = parse_fomc_current(_get_text(FED_CALENDAR_URL, retries=retries, session=session, sleep=sleep))
    if cur.empty:
        raise FedCalendarError(f"{FED_CALENDAR_URL}: no meetings found (page format changed?)")
    frames = [cur]
    for year in range(COVERAGE_START.year, int(pd.DatetimeIndex(cur["day"]).year.min())):
        url = FED_HISTORICAL_URL.format(year=year)
        h = parse_fomc_historical(_get_text(url, retries=retries, session=session, sleep=sleep))
        if len(h) < 6:                     # 8 scheduled meetings a year
            raise FedCalendarError(f"{url}: {len(h)} scheduled meetings found (page format changed?)")
        frames.append(h)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates("day", keep="first").sort_values("day").reset_index(drop=True)


# --------------------------------------------------------------------------------------------------- FRED
class FredError(RuntimeError):
    """A FRED request failed; the message never contains the API key."""


def parse_release_dates(payload: dict[str, Any]) -> pd.DatetimeIndex:
    """`fred/release/dates` JSON -> sorted unique dates. A payload without `release_dates` is refused."""
    if "release_dates" not in payload:
        raise FredError(f"unexpected release/dates payload keys {sorted(payload)[:5]}")
    days = pd.to_datetime(pd.Series([r.get("date") for r in payload["release_dates"]]), errors="coerce").dropna()
    return pd.DatetimeIndex(days).normalize().unique().sort_values()


def _fred_get(url: str, params: dict[str, Any], api_key: str, *, retries: int, session: requests.Session | None,
              sleep: Callable[[float], None]) -> dict[str, Any]:
    get = session.get if session is not None else requests.get
    last = ""
    for i in range(retries):
        try:
            r = get(url, params={**params, "api_key": api_key, "file_type": "json"}, timeout=60,
                    headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                return dict(r.json())
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as exc:
            last = type(exc).__name__          # the exception text can carry the URL with the key; not printed
        if i + 1 < retries:
            sleep(10 * (i + 1))
    raise FredError(f"{url} {params}: {last}")


def fetch_release(rel: Release, api_key: str, *, retries: int = 3, session: requests.Session | None = None,
                  sleep: Callable[[float], None] = _time.sleep) -> pd.DatetimeIndex:
    """All past and scheduled dates of one FRED release since COVERAGE_START, after checking its name."""
    meta = _fred_get(FRED_RELEASE_URL, {"release_id": rel.release_id}, api_key, retries=retries, session=session,
                     sleep=sleep)
    names = [str(r.get("name", "")) for r in meta.get("releases", [])]
    if not any(rel.name.lower() in n.lower() for n in names):
        raise FredError(f"FRED release {rel.release_id} is {names}, expected a name containing {rel.name!r}")
    payload = _fred_get(FRED_RELEASE_DATES_URL, {
        "release_id": rel.release_id, "realtime_start": f"{COVERAGE_START:%Y-%m-%d}", "realtime_end": "9999-12-31",
        "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 10000},
        api_key, retries=retries, session=session, sleep=sleep)
    days = parse_release_dates(payload)
    return days[days >= COVERAGE_START]


# --------------------------------------------------------------------------------------------------- merging
def _utc_ns(x: pd.Series) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(x, utc=True)).as_unit("ns")


def merge_calendar(prev: pd.DataFrame | None, fresh: pd.DataFrame, retrieved_utc: pd.Timestamp,
                   last_ok: dict[str, pd.Timestamp] | None = None,
                   fetched_types: tuple[str, ...] | list[str] | None = None) -> pd.DataFrame:
    """Add one download to the published calendar without rewriting it.

    * An event_id first seen now keeps its rule stamp when that is still in the future (a schedule published ahead,
      the normal case). If its source was downloaded before (`last_ok[source]`) and the rule stamp has already passed,
      it was added or moved late (a reschedule): until now the system believed the old schedule, so it is available at
      `retrieved_utc`, even when that is after the event (known_timeline then counts it as a past event only from
      `retrieved_utc`).
    * A future event (event_utc > retrieved_utc) of a type in `fetched_types` (default: the types in `fresh`) whose
      latest status is scheduled and that `fresh` no longer lists gets a `cancelled` row at `retrieved_utc`; a
      cancelled one that `fresh` lists again gets a `scheduled` row at `retrieved_utc`. Past events are never touched.
    * Anything else adds nothing."""
    now = pd.Timestamp(retrieved_utc)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    fresh = fresh.copy()
    fresh["available_utc"] = _utc_ns(fresh["available_utc"])
    fresh["event_utc"] = _utc_ns(fresh["event_utc"])
    if prev is None or prev.empty:
        out = fresh
    else:
        prev = prev.copy()
        prev["available_utc"] = _utc_ns(prev["available_utc"])
        prev["event_utc"] = _utc_ns(prev["event_utc"])
        latest = prev.sort_values("available_utc", kind="stable").drop_duplicates("event_id", keep="last")
        state = latest.set_index("event_id")["status"]
        is_new = ~fresh["event_id"].isin(state.index).to_numpy()
        ok = {k: str(pd.Timestamp(v)) for k, v in (last_ok or {}).items()}
        seen_before = _utc_ns(fresh["source"].map(ok))
        rule = pd.DatetimeIndex(fresh["available_utc"])
        late = is_new & ~np.asarray(seen_before.isna(), dtype=bool) & np.asarray(rule < now, dtype=bool)
        fresh["available_utc"] = rule.where(~late, now)
        future = np.asarray(pd.DatetimeIndex(fresh["event_utc"]) > now, dtype=bool)
        back = ~is_new & future & (fresh["event_id"].map(state) == CANCELLED).to_numpy()
        revived = fresh[back].assign(available_utc=now)
        types = set(fetched_types) if fetched_types is not None else set(fresh["event_type"])
        gone = latest[(latest["status"] == SCHEDULED) & latest["event_type"].isin(types)
                      & (pd.DatetimeIndex(latest["event_utc"]) > now) & ~latest["event_id"].isin(fresh["event_id"])]
        cancelled = gone.assign(status=CANCELLED, available_utc=now, vintage=_naive_day(now))
        out = pd.concat([prev, fresh[is_new], revived, cancelled], ignore_index=True)
    out["available_utc"] = _utc_ns(out["available_utc"])
    out["ts_utc"] = out["available_utc"]
    out["tier"] = out["tier"].astype(int)
    out["release_id"] = out["release_id"].astype(int)
    return out[list(COLUMNS)].sort_values(["event_utc", "event_id", "available_utc"], kind="stable").reset_index(drop=True)


class HistoryLoss(ValueError):
    """A frame about to be published would drop or change rows of the previously published file."""


def check_keeps_history(prev: pd.DataFrame | None, merged: pd.DataFrame) -> None:
    """Raise HistoryLoss unless `merged` keeps every previously published row unchanged and has, for each source, at
    least as many rows as `prev` (the approach of goldbot/data/positioning.py: the publish step overwrites the
    release asset, so this is the last line that keeps late stamps and cancellations from being lost)."""
    if prev is None or prev.empty:
        return
    for src, n_prev in prev.groupby("source").size().items():
        n_new = int((merged["source"] == src).sum())
        if n_new < n_prev:
            raise HistoryLoss(f"{src}: {n_new:,} rows would replace {n_prev:,} published rows")
    key = ["event_id", "status", "event_utc", "available_utc"]

    def norm(df: pd.DataFrame) -> pd.DataFrame:
        out = df[key].copy()
        out["event_utc"] = _utc_ns(out["event_utc"])
        out["available_utc"] = _utc_ns(out["available_utc"])
        return out

    m = norm(prev).merge(norm(merged).drop_duplicates(), on=key, how="left", indicator=True)
    lost = m[m["_merge"] == "left_only"]
    if len(lost):
        sample = lost.head(3)[key].astype(str).to_dict("records")
        raise HistoryLoss(f"{len(lost):,} published rows missing or changed, e.g. {sample}")


# --------------------------------------------------------------------------------------------------- the file
META_KEY = b"goldbot.us_calendar.last_ok"
ASSET = "us_calendar.parquet"


def write_release(df: pd.DataFrame, path: str, last_ok: dict[str, pd.Timestamp]) -> None:
    """Parquet with the last successful download time per source in the schema metadata (merge_calendar reads it)."""
    table = pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[META_KEY] = json.dumps({k: pd.Timestamp(v).isoformat() for k, v in last_ok.items()}).encode()
    pq.write_table(table.replace_schema_metadata(meta), path, compression="zstd")


def read_release(path: str) -> tuple[pd.DataFrame, dict[str, pd.Timestamp]]:
    table = pq.read_table(path)
    raw = (table.schema.metadata or {}).get(META_KEY)
    last_ok = {k: pd.Timestamp(v) for k, v in json.loads(raw).items()} if raw else {}
    return table.to_pandas(), last_ok


# --------------------------------------------------------------------------------------------------- knowledge
def known_timeline(cal: pd.DataFrame, tier: int | None = None) -> pd.DataFrame:
    """What the calendar said at every instant it changed, for one tier (None: all): DataFrame(available_utc,
    next_event_utc, last_event_utc, count_today).

    At change point c (a row's available_utc, an event passing, or a New York midnight) the next event is the earliest
    event whose row valid at c (its latest row with available_utc <= c) is scheduled and whose time is after c; the
    last event is the latest scheduled event at or before c that was known by c; count_today is the number of events
    valid at c on c's New York calendar day. Between change points none of them changes, so an as-of join on
    available_utc gives each bar exactly what was known at its time. NaT where none."""
    cols = ["available_utc", "next_event_utc", "last_event_utc", "count_today"]
    if cal is None or len(cal) == 0:
        return pd.DataFrame(columns=cols)
    c = cal[["event_id", "event_utc", "status", "available_utc", "tier"]].copy()
    if tier is not None:
        c = c[c["tier"].astype(int) == int(tier)]
        if c.empty:
            return pd.DataFrame(columns=cols)
    c["available_utc"], c["event_utc"] = _utc_ns(c["available_utc"]), _utc_ns(c["event_utc"])
    c = c.sort_values(["event_id", "available_utc"], kind="stable").reset_index(drop=True)
    avail, ev = epoch_ns(c["available_utc"]), epoch_ns(c["event_utc"])
    big = np.iinfo(np.int64).max
    same_next = (c["event_id"].shift(-1) == c["event_id"]).to_numpy()
    until = np.where(same_next, np.roll(avail, -1), big)    # row valid on [avail, until)
    sched = (c["status"] == SCHEDULED).to_numpy()
    known_at = np.maximum(avail, ev)
    lo, hi = pd.Timestamp(int(avail.min()), tz="UTC"), pd.Timestamp(int(known_at.max()), tz="UTC")
    days = pd.date_range(lo.tz_convert(ET).normalize().tz_localize(None),
                         hi.tz_convert(ET).normalize().tz_localize(None) + pd.Timedelta(days=1), freq="D")
    midnights = epoch_ns(days.tz_localize(ET))
    points = np.unique(np.concatenate([avail, known_at[sched], until[until < big], midnights]))
    points = points[points >= avail.min()]
    day_start = midnights[np.searchsorted(midnights, points, side="right") - 1]
    day_end = midnights[np.minimum(np.searchsorted(midnights, points, side="right"), len(midnights) - 1)]
    nat = np.iinfo(np.int64).min
    nxt = np.full(len(points), nat, dtype=np.int64)
    lst = np.full(len(points), nat, dtype=np.int64)
    cnt = np.zeros(len(points), dtype=np.int64)
    for i, p in enumerate(points):
        valid = sched & (avail <= p) & (p < until)
        up = valid & (ev > p)
        if up.any():
            nxt[i] = ev[up].min()
        done = valid & (known_at <= p)
        if done.any():
            lst[i] = ev[done].max()
        cnt[i] = int((valid & (ev >= day_start[i]) & (ev < day_end[i])).sum())

    def to_ts(a: np.ndarray) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(pd.to_datetime(np.where(a == nat, np.datetime64("NaT"), a.astype("datetime64[ns]")),
                                               utc=True)).as_unit("ns")
    return pd.DataFrame({"available_utc": to_ts(points), "next_event_utc": to_ts(nxt), "last_event_utc": to_ts(lst),
                         "count_today": cnt.astype(float)})

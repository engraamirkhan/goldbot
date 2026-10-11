"""Point-in-time US economic calendar, tiers 1 and 2, 2010 onwards (BACKLOG 18 data part, playbook gap G-3; research
finding E-1: H-16/H-17/H-18 need a historical tier-1 schedule). The data-us-calendar workflow builds the file and
publishes it on release `calendar-v1` (Claude sandboxes cannot reach FRED or federalreserve.gov; GitHub's runners can).

Tiers keep the labels of goldbot/data/econ_calendar.py and settings `risk.blackout.events`:
* tier 1 (the RiskGate blackout list): CPI, NFP (Employment Situation), PCE (Personal Income and Outlays), FOMC
  statement and FOMC press conference;
* tier 2 (G-3): PPI, retail sales, GDP advance (`gdp_advance`, the first GDP release after each quarter) and the later
  GDP estimates (`gdp`), JOLTS, weekly jobless claims, ISM manufacturing and services, FOMC minutes. GDP is not on the
  tier-1 list in code; promoting `gdp_advance` is an owner / trading-safety decision. Fed speakers are not covered (no
  free, structured historical schedule).

Sources and licence (docs/decisions/0008-us-calendar-source.md):
* FRED API `fred/release/dates` (Federal Reserve Bank of St. Louis; ALFRED keeps past release dates) for the agency
  releases (RELEASES). A free API key is needed (repo secret FRED_API_KEY; never in code or logs). The dates are
  scheduling facts of US federal agencies (BLS, Census, BEA, DOL: US government works, public domain); FRED's API
  terms ask for the notice "This product uses the FRED(R) API but is not endorsed or certified by the Federal Reserve
  Bank of St. Louis" and no implied endorsement. No free, licence-compatible consensus source exists, so the file has
  no actual/consensus columns.
* FOMC: the Federal Reserve Board's meeting calendars (federalreserve.gov `fomccalendars.htm` for the recent years and
  the year ahead, `fomchistorical<year>.htm` before that; US government work, public domain). Scheduled meetings
  only. A press conference is listed where the page links one (the link spelling varies), and at every meeting from
  2019. Minutes: the "Released" date of the page's minutes block when it is 14-28 days after the statement, else
  statement + 21 days, 14:00 ET.
* ISM manufacturing and services are not on FRED and ISM's content is licensed, so their dates are rule-derived
  (source `rule`): first and third US business day of the month, 10:00 ET. ISM occasionally deviates (unverified);
  RULE_OVERRIDES moves or drops a hand-checked date, and research can leave rule rows out.
* Scheduled-then-cancelled events (MANUAL_EVENTS, source `manual`): the downloads list what happened, so an event
  that was on the schedule and then cancelled or moved (shutdowns, the March 2020 FOMC) is hand-checked here.

Times are New York wall times (per type; FOMC per era: statement 14:15 until April 2011, 12:30 on 2011-2012 press
conference days and 14:15 otherwise, 14:00 from 2013; press conference 14:15 in 2011-2012, 14:30 from 2013),
converted to UTC per date via zoneinfo, so DST is right on both sides of every switch. FRED dates are dates only; a
release moved within its day is not seen. PCE at 08:30 ET in 2010-2012 is unverified.

Point in time. A schedule is known before the event, but how long before is not recorded for the past. Rule:
`available_utc = event_utc - SCHEDULE_LEAD` (7 days), raised to a disruption floor for events of an agency closed by
a government shutdown (SHUTDOWN_FLOORS, per agency; the Fed and ISM are never closed), never later than the event.
Features cap their horizon at the same lead, so history and live data look the same. `merge_calendar` keeps the
history as published. last_ok is kept per event type: a type never downloaded before is a backfill and keeps its rule
stamps; for a type downloaded before, an event first seen now whose rule stamp has passed was added or moved late and
is stamped at this download. A future event that disappears adds a `cancelled` row; one that comes back a `scheduled`
row; a run that would cancel more than MAX_CANCELLATIONS future events of one type is refused for that type (a
truncated download, not a schedule change) unless the operator allows it once (`allow_cancellations`, audited).
Both rows of any pair of monthly releases less than MIN_MONTHLY_SPACING apart are quarantined (kept out and reported)
unless hand-checked (SPACING_EXEMPT). A GDP advance more than 45 days after its quarter end is flagged unless
GDP_ADVANCE_OVERRIDES lists the quarter. `count_table` checks per-type, per-year counts. last_ok metadata keyed by
anything but event types (the first, source-keyed format) is refused.

Every row: event_id (`<event_type>:<ET date>`), event_type, tier (1 | 2), release_id (FRED id, -1 otherwise),
event_utc, status (scheduled | cancelled), available_utc, ts_utc (= available_utc), vintage (retrieval date), source
(`fred` | `fed` | `rule` | `manual`). Features join only via `store.asof_join` on available_utc
(goldbot/features/us_calendar.py).
"""
from __future__ import annotations

import json
import re
import time as _time
from collections.abc import Sequence
from datetime import time
from typing import Any, Callable, NamedTuple
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
MAX_CANCELLATIONS = 2                      # future events of one type a single run may cancel
MIN_MONTHLY_SPACING = pd.Timedelta(days=20)
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
    Release(event_type="retail_sales", tier=2, release_id=9, name="Advance Monthly Sales for Retail",
            at_et=time(8, 30)),
    Release(event_type="gdp", tier=2, release_id=53, name="Gross Domestic Product", at_et=time(8, 30)),
    Release(event_type="jolts", tier=2, release_id=192, name="Job Openings and Labor Turnover", at_et=time(10, 0)),
    Release(event_type="jobless_claims", tier=2, release_id=180, name="Unemployment Insurance Weekly Claims",
            at_et=time(8, 30)),
)
FOMC_TYPES = {"fomc_statement": 1, "fomc_press_conference": 1, "fomc_minutes": 2}
FOMC_MINUTES_AFTER = pd.Timedelta(days=21)
FOMC_MINUTES_GAP = (pd.Timedelta(days=14), pd.Timedelta(days=28))
FOMC_MINUTES_ET = time(14, 0)
PRESS_EVERY_MEETING_FROM = pd.Timestamp("2019-01-01")
ISM = {"ism_manufacturing": 1, "ism_services": 3}      # n-th US business day of the month
ISM_ET = time(10, 0)
TIERS: dict[str, int] = {**{r.event_type: r.tier for r in RELEASES}, "gdp_advance": 2, **FOMC_TYPES,
                         **{t: 2 for t in ISM}}
EVENT_TYPES = tuple(TIERS)
AGENCY: dict[str, str] = {"cpi": "bls", "nfp": "bls", "ppi": "bls", "jolts": "bls", "jobless_claims": "dol",
                          "retail_sales": "census", "gdp": "bea", "gdp_advance": "bea", "pce": "bea",
                          **{t: "fed" for t in FOMC_TYPES}, **{t: "ism" for t in ISM}}
MONTHLY_TYPES = ("cpi", "nfp", "pce", "ppi", "retail_sales", "jolts")
_RELEASE_OF = {r.event_type: r for r in RELEASES}


class Shutdown(NamedTuple):
    start: pd.Timestamp                 # ET date the lapse began
    floor: pd.Timestamp                 # ET date before which no rescheduled date is taken as known (conservative)
    agencies: frozenset[str]
    note: str


_ALL = frozenset({"bls", "dol", "census", "bea"})
SHUTDOWN_FLOORS: tuple[Shutdown, ...] = (
    Shutdown(pd.Timestamp("2013-10-01"), pd.Timestamp("2013-10-31"), _ALL, "reopened 2013-10-17"),
    Shutdown(pd.Timestamp("2018-12-22"), pd.Timestamp("2019-02-15"), frozenset({"census", "bea"}),
             "reopened 2019-01-25; Commerce only (BLS and DOL were funded)"),
    Shutdown(pd.Timestamp("2025-10-01"), pd.Timestamp("2025-12-05"), _ALL, "reopened 2025-11-12"),
    Shutdown(pd.Timestamp("2026-01-31"), pd.Timestamp("2026-02-13"), frozenset({"bls"}),
             "short lapse; BLS only until other agencies are verified (BLS revised dates verified, start unverified)"),
)


class ManualEvent(NamedTuple):
    event_type: str
    day: str                 # originally scheduled ET date
    cancel_at_et: str        # when it was known not to happen (ET wall time)
    source_note: str


# Hand-checked scheduled-then-cancelled/moved events. Agency cancel times: the shutdown's start for events up to
# reopening (2013, 2025, where the start is verified); otherwise the shutdown floor or one minute before the event,
# whichever is earlier (the announcement time is not recorded; later is conservative, never after the event).
MANUAL_EVENTS: tuple[ManualEvent, ...] = (
    ManualEvent("nfp", "2013-10-04", "2013-10-01 00:00", "BLS revised schedule (Calculated Risk copy, 2013-10-17)"),
    ManualEvent("jolts", "2013-10-08", "2013-10-01 00:00", "BLS revised schedule 2013-10-17"),
    ManualEvent("ppi", "2013-10-11", "2013-10-01 00:00", "DOL notice 2013-10-03; BLS 2013-10-17"),
    ManualEvent("cpi", "2013-10-16", "2013-10-01 00:00", "DOL notice 2013-10-03; BLS 2013-10-17"),
    ManualEvent("nfp", "2013-11-01", "2013-10-17 23:59", "BLS 2013-10-17: moved to 2013-11-08"),
    ManualEvent("gdp_advance", "2019-01-30", "2018-12-22 00:00", "NY Fed nowcast 2019-02-15: moved to 2019-02-28"),
    ManualEvent("fomc_statement", "2020-03-18", "2020-03-15 17:00", "Fed 2020 page: March 17-18 meeting cancelled"),
    ManualEvent("fomc_press_conference", "2020-03-18", "2020-03-15 17:00", "Fed 2020 page: meeting cancelled"),
    ManualEvent("nfp", "2025-10-03", "2025-10-01 00:00", "bls.gov/bls/2025-lapse-revised-release-dates.htm"),
    ManualEvent("cpi", "2025-10-15", "2025-10-01 00:00", "inferred from the Real Earnings row (same release)"),
    ManualEvent("ppi", "2025-10-16", "2025-10-01 00:00", "bls.gov 2025 lapse table"),
    ManualEvent("jolts", "2025-11-04", "2025-10-01 00:00", "bls.gov 2025 lapse table (cancelled)"),
    ManualEvent("nfp", "2025-11-07", "2025-10-01 00:00", "bls.gov 2025 lapse table (cancelled)"),
    ManualEvent("cpi", "2025-11-13", "2025-11-13 08:29", "bls.gov 2025 lapse table (cancelled)"),
    ManualEvent("ppi", "2025-11-14", "2025-11-14 08:29", "bls.gov 2025 lapse table (cancelled)"),
    ManualEvent("pce", "2025-11-26", "2025-11-26 08:29", "bea.gov blog 2025-11-24/12-10 (combined into 2026-01-22)"),
    ManualEvent("jolts", "2025-12-02", "2025-12-02 09:59", "bls.gov 2025 lapse table (moved to 2025-12-09)"),
    ManualEvent("nfp", "2025-12-05", "2025-12-05 00:00", "bls.gov 2025 lapse table (moved to 2025-12-16)"),
    ManualEvent("cpi", "2025-12-10", "2025-12-05 00:00", "bls.gov 2025 lapse table (moved to 2025-12-18)"),
    ManualEvent("ppi", "2025-12-11", "2025-12-05 00:00", "bls.gov 2025 lapse table (moved to 2026-01-14)"),
    ManualEvent("pce", "2025-12-19", "2025-12-05 00:00", "bea.gov blog (combined into 2026-01-22)"),
    ManualEvent("jolts", "2026-02-03", "2026-02-03 09:59", "bls.gov 2026 lapse table (moved to 2026-02-05)"),
    ManualEvent("nfp", "2026-02-06", "2026-02-06 08:29", "bls.gov 2026 lapse table (moved to 2026-02-11)"),
    ManualEvent("cpi", "2026-02-11", "2026-02-11 08:29", "bls.gov 2026 lapse table (moved to 2026-02-13)"),
)
# Monthly releases closer than MIN_MONTHLY_SPACING that are real (hand-checked).
SPACING_EXEMPT: frozenset[str] = frozenset({
    "nfp:2013-10-22", "nfp:2013-11-08",     # the delayed September report and the October one, 17 days apart (2013)
})
# The real GDP advance for a quarter end when the first GDP release after it is something else (hand-checked):
# quarter end -> advance date. A first release more than GDP_ADVANCE_MAX_LAG after a quarter end not listed here is
# flagged (late_gdp_advances) for review.
GDP_ADVANCE_MAX_LAG = pd.Timedelta(days=45)
GDP_ADVANCE_OVERRIDES: dict[str, str] = {
    "2018-12-31": "2019-02-28",   # NY Fed nowcast 2019-02-15: postponed by the shutdown
    "2025-09-30": "2025-12-23",   # BEA blog 2025-11-24/12-10: the October advance cancelled, initial estimate Dec 23
    "2025-12-31": "2026-02-20",   # BEA blog 2026-01-07; 2026-01-22 was the Q3 update and PCE, not the Q4 advance
}
# Rule-derived (ISM) dates checked by hand: rule event_id -> actual ET date, or None to drop it.
RULE_OVERRIDES: dict[str, str | None] = {}


# --------------------------------------------------------------------------------------------------- timing
def et_to_utc(dates: pd.Series | pd.DatetimeIndex | list[Any], at: time | list[time]) -> pd.DatetimeIndex:
    """ET calendar dates at a New York wall time (one, or one per date) -> UTC instants, DST per date (08:30 to 14:30
    are never in a DST gap or fold)."""
    d = pd.DatetimeIndex(pd.to_datetime(pd.Series(dates))).tz_localize(None).normalize()
    times = at if isinstance(at, list) else [at] * len(d)
    local = d + pd.to_timedelta([t.hour * 60 + t.minute for t in times], unit="min")
    return pd.DatetimeIndex(local.tz_localize(ET).tz_convert("UTC")).as_unit("ns")


def rule_available_utc(event_utc: pd.Series | pd.DatetimeIndex, agency: str | Sequence[str] | None = None
                       ) -> pd.DatetimeIndex:
    """event - SCHEDULE_LEAD, raised to the floor of each shutdown that closed the event's `agency` (one, or one per
    event; None: no floors) for events on or after the shutdown began, never after the event."""
    ev = pd.DatetimeIndex(pd.to_datetime(pd.Series(event_utc), utc=True)).as_unit("ns")
    avail = ev - SCHEDULE_LEAD
    if agency is None:
        return avail.where(avail <= ev, ev)
    agencies = np.array([agency] * len(ev) if isinstance(agency, str) else list(agency), dtype=object)
    ev_et_day = ev.tz_convert(ET).tz_localize(None).normalize()
    for sd in SHUTDOWN_FLOORS:
        floor = pd.Timestamp(sd.floor, tz=ET).tz_convert("UTC")
        hit = (np.asarray(ev_et_day >= sd.start) & np.asarray(avail < floor)
               & np.isin(agencies, list(sd.agencies)))
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


def event_time_et(event_type: str, day: pd.Timestamp) -> time:
    """The scheduled New York time of one event (FOMC statements: as on a press-conference day from 2011)."""
    if event_type == "fomc_statement":
        return fomc_statement_et(day, day >= pd.Timestamp("2019-01-01"))
    if event_type == "fomc_press_conference":
        return fomc_press_et(day)
    if event_type == "fomc_minutes":
        return FOMC_MINUTES_ET
    if event_type in ISM:
        return ISM_ET
    return _RELEASE_OF["gdp" if event_type == "gdp_advance" else event_type].at_et


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
        "available_utc": rule_available_utc(ev, AGENCY[event_type]), "vintage": _naive_day(vintage),
        "source": source})
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)]


def rule_rows(now: pd.Timestamp, vintage: pd.Timestamp) -> pd.DataFrame:
    """ISM manufacturing and services from COVERAGE_START to now + RULE_HORIZON, with RULE_OVERRIDES applied."""
    end = _naive_day(now)
    frames = []
    for t, n in ISM.items():
        days = []
        for d in nth_us_business_days(COVERAGE_START, end + RULE_HORIZON, n):
            key = f"{t}:{d:%Y-%m-%d}"
            if key in RULE_OVERRIDES:
                new = RULE_OVERRIDES[key]
                if new is not None:
                    days.append(pd.Timestamp(new))
            else:
                days.append(d)
        frames.append(event_rows(t, -1, pd.DatetimeIndex(days), ISM_ET, vintage, "rule"))
    return pd.concat(frames, ignore_index=True)


def _gdp_advance_by_quarter(dates: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp]:
    """quarter end -> its advance date: GDP_ADVANCE_OVERRIDES when listed (and in the data), else the first GDP release
    after the quarter end (from the quarter end just before the first date: earlier quarters are not guessed)."""
    d = pd.DatetimeIndex(dates).tz_localize(None).normalize().unique().sort_values()
    if len(d) == 0:
        return {}
    out = {}
    for qe in pd.date_range(d.min() - pd.Timedelta(days=35), d.max(), freq="QE"):
        q = pd.Timestamp(qe).normalize()
        override = GDP_ADVANCE_OVERRIDES.get(f"{q:%Y-%m-%d}")
        if override is not None and pd.Timestamp(override) in d:
            out[q] = pd.Timestamp(override)
            continue
        i = int(np.searchsorted(d.to_numpy(), q.to_datetime64(), side="right"))
        if i < len(d):
            out[q] = d[i]
    return out


def split_gdp(dates: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """(advance, later estimates), with the advance per quarter from _gdp_advance_by_quarter."""
    d = pd.DatetimeIndex(dates).tz_localize(None).normalize().unique().sort_values()
    advance = pd.DatetimeIndex(sorted(set(_gdp_advance_by_quarter(d).values())))
    return advance, d[~d.isin(advance)]


def late_gdp_advances(dates: pd.DatetimeIndex) -> list[str]:
    """Quarters whose advance (not hand-checked) comes more than GDP_ADVANCE_MAX_LAG after the quarter end."""
    return [f"quarter {q:%Y-%m-%d}: first GDP release {a:%Y-%m-%d} is {(a - q).days} days after the quarter end"
            for q, a in _gdp_advance_by_quarter(dates).items()
            if a - q > GDP_ADVANCE_MAX_LAG and f"{q:%Y-%m-%d}" not in GDP_ADVANCE_OVERRIDES]


def fomc_rows(meetings: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    """Scheduled FOMC meetings (parse_fomc_*: day = the statement day, press, minutes_day) -> statement and press
    conference (tier 1) and minutes (tier 2) rows. A minutes date outside 14-28 days after the statement is not
    trusted (statement + 21 days instead)."""
    if meetings.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    m = meetings.drop_duplicates("day", keep="last").sort_values("day").reset_index(drop=True)
    days = pd.DatetimeIndex(m["day"])
    press = m["press"].astype(bool).to_numpy() | np.asarray(days >= PRESS_EVERY_MEETING_FROM)
    released = pd.DatetimeIndex(pd.to_datetime(m["minutes_day"]))
    gap = released - days
    ok = ~np.asarray(released.isna(), dtype=bool) & np.asarray(gap >= FOMC_MINUTES_GAP[0]) & \
        np.asarray(gap <= FOMC_MINUTES_GAP[1])
    minutes = released.where(ok, days + FOMC_MINUTES_AFTER)
    return pd.concat([
        event_rows("fomc_statement", -1, days, [fomc_statement_et(d, bool(p)) for d, p in zip(days, press)], vintage,
                   "fed"),
        event_rows("fomc_press_conference", -1, days[press], [fomc_press_et(d) for d in days[press]], vintage, "fed"),
        event_rows("fomc_minutes", -1, minutes, FOMC_MINUTES_ET, vintage, "fed"),
    ], ignore_index=True)


def manual_rows(vintage: pd.Timestamp) -> pd.DataFrame:
    """MANUAL_EVENTS -> a scheduled row (event - 7 days, before the cancellation) and a cancelled row each."""
    rows = []
    for e in MANUAL_EVENTS:
        day = pd.Timestamp(e.day)
        ev = et_to_utc([day], event_time_et(e.event_type, day))[0]
        cancel = pd.Timestamp(e.cancel_at_et, tz=ET).tz_convert("UTC").as_unit("ns")
        sched = min(ev - SCHEDULE_LEAD, cancel - pd.Timedelta(minutes=1))
        rel = _RELEASE_OF.get("gdp" if e.event_type == "gdp_advance" else e.event_type)
        base = {"event_id": f"{e.event_type}:{day:%Y-%m-%d}", "event_type": e.event_type,
                "tier": TIERS[e.event_type], "release_id": rel.release_id if rel else -1, "event_utc": ev,
                "vintage": _naive_day(vintage), "source": "manual"}
        rows += [{**base, "status": SCHEDULED, "available_utc": sched},
                 {**base, "status": CANCELLED, "available_utc": cancel}]
    out = pd.DataFrame(rows)
    out["event_utc"], out["available_utc"] = _utc_ns(out["event_utc"]), _utc_ns(out["available_utc"])
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)]


def calendar_frame(fred_dates: dict[str, pd.DatetimeIndex], now: pd.Timestamp,
                   fomc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Fresh rows for one run: each fetched FRED release in `fred_dates` (event_type -> ET dates; `gdp` is split into
    `gdp_advance` and `gdp`), the FOMC meetings (`fomc`, when fetched), the rule-derived ISM rows and the hand-checked
    cancellations."""
    frames = []
    for t, d in fred_dates.items():
        if t not in _RELEASE_OF:
            continue
        rel = _RELEASE_OF[t]
        if t == "gdp":
            advance, later = split_gdp(d)
            frames += [event_rows("gdp_advance", rel.release_id, advance, rel.at_et, now, "fred"),
                       event_rows("gdp", rel.release_id, later, rel.at_et, now, "fred")]
        else:
            frames.append(event_rows(t, rel.release_id, d, rel.at_et, now, "fred"))
    if fomc is not None:
        frames.append(fomc_rows(fomc, now))
    frames += [rule_rows(now, now), manual_rows(now)]
    return pd.concat([f for f in frames if len(f)], ignore_index=True)


def types_of(fetched_sources: Sequence[str]) -> list[str]:
    """Event types produced by the fetched FRED releases / `fomc` / ISM (`gdp` yields `gdp_advance` too)."""
    out: list[str] = []
    for s in fetched_sources:
        if s == "fomc":
            out += list(FOMC_TYPES)
        elif s == "gdp":
            out += ["gdp_advance", "gdp"]
        else:
            out.append(s)
    return out


# --------------------------------------------------------------------------------------------------- quality
def quarantine_spacing(fresh: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(kept, quarantined): BOTH rows of any pair of downloaded monthly releases (MONTHLY_TYPES) less than
    MIN_MONTHLY_SPACING apart are quarantined (one is a duplicate, a revision day or a parse error, and which one is
    not knowable here), except rows SPACING_EXEMPT lists (hand-checked real releases)."""
    f = fresh[(fresh["source"] == "fred") & fresh["event_type"].isin(MONTHLY_TYPES)
              & (fresh["status"] == SCHEDULED)].sort_values(["event_type", "event_utc"])
    t = pd.DatetimeIndex(f["event_utc"]).to_series(index=f.index)
    by = f["event_type"].to_numpy()
    close_prev = (t.groupby(by).diff() < MIN_MONTHLY_SPACING).to_numpy()
    close_next = (-t.groupby(by).diff(-1) < MIN_MONTHLY_SPACING).to_numpy()
    bad = f.index[(close_prev | close_next) & ~f["event_id"].isin(SPACING_EXEMPT).to_numpy()]
    return fresh.drop(index=bad), fresh.loc[bad]


def expected_count(event_type: str, year: int) -> tuple[int, int]:
    """Expected number of events of one type in one full year (inclusive range; shutdown years allowed for)."""
    if event_type == "jobless_claims":
        return 51, 53
    if event_type in (*MONTHLY_TYPES, *ISM):
        return 10, 13
    if event_type == "gdp_advance":
        return 4, 4
    if event_type == "gdp":
        return 6, 10
    if event_type == "fomc_statement":
        return 7, 8
    if event_type == "fomc_minutes":
        return 7, 9                  # by release year: a December meeting's minutes can come out in the same year
    if event_type == "fomc_press_conference":
        known = {2011: (3, 3), 2012: (5, 5)}                  # counted on the Fed's 2011 and 2012 pages
        if year < 2011:
            return 0, 0
        if year in known:
            return known[year]
        return (4, 4) if year < 2019 else (7, 8)
    return 0, 10_000


def count_table(cal: pd.DataFrame, through_year: int | None = None) -> pd.DataFrame:
    """Events per type and year (latest status scheduled) against expected_count; `ok` is None for years after
    `through_year` (partial years)."""
    latest = cal.sort_values("available_utc", kind="stable").drop_duplicates("event_id", keep="last") \
        if "available_utc" in cal.columns else cal.drop_duplicates("event_id", keep="last")
    live = latest[latest["status"] == SCHEDULED]
    years = pd.DatetimeIndex(pd.to_datetime(live["event_utc"], utc=True)).tz_convert(ET).year
    n = live.groupby([live["event_type"].to_numpy(), years]).size()
    rows = []
    for key, k in n.items():
        t, y = str(key[0]), int(key[1])               # type: ignore[index]
        lo, hi = expected_count(t, y)
        rows.append({"event_type": t, "year": y, "n": int(k), "expected": f"{lo}-{hi}" if lo != hi else str(lo),
                     "ok": None if through_year is not None and y > through_year else bool(lo <= k <= hi)})
    return pd.DataFrame(rows, columns=["event_type", "year", "n", "expected", "ok"])


# --------------------------------------------------------------------------------------------------- Fed calendars
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_SKIP = re.compile(r"unscheduled|cancel|notation|conference call", re.I)
_MINUTES_RELEASED = re.compile(r"Minutes\b(?:(?!Minutes\b).){0,600}?\(Released\s+([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})\)",
                               re.S)
_PRESS = re.compile(r"fomcpres+conf|>\s*press conference\s*<")


class FedCalendarError(RuntimeError):
    """A Fed calendar page could not be fetched or no longer has the expected structure."""


def _month(s: str) -> int:
    m = _MONTHS.get(s.strip().lower()[:3])
    if m is None:
        raise FedCalendarError(f"unknown month {s!r}")
    return m


def _meeting(year: int, months: str, days: str, chunk: str) -> dict[str, Any] | None:
    """One meeting block -> day (the statement day: the meeting's last day), press, minutes_day; None when it is not a
    scheduled meeting. `months` is "June", "Apr/May" or "April/May"; `days` "16-17*" or "30-1"."""
    if _SKIP.search(days) or _SKIP.search(months):
        return None
    ms = [p for p in months.strip().split("/") if p]
    found = re.findall(r"(?:([A-Za-z]+)\s+)?(\d{1,2})", days)
    if not found or not ms:
        return None
    end_month = _month(found[-1][0] or ms[-1])
    end_year = year + 1 if end_month < _month(ms[0]) else year      # a meeting across New Year (none so far)
    day = pd.Timestamp(year=end_year, month=end_month, day=int(found[-1][1]))
    rel = _MINUTES_RELEASED.search(chunk)
    minutes = pd.Timestamp(year=int(rel.group(3)), month=_month(rel.group(1)), day=int(rel.group(2))) if rel else pd.NaT
    return {"day": day, "press": bool(_PRESS.search(chunk.lower())), "minutes_day": minutes}


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
                    rows.append({**r, "panel_year": year})
    return pd.DataFrame(rows, columns=["day", "press", "minutes_day", "panel_year"])


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


MIN_MEETINGS_PER_YEAR = 6          # 8 scheduled a year; 7 in 2020 (March cancelled)


def fetch_fomc(*, retries: int = 3, session: requests.Session | None = None,
               sleep: Callable[[float], None] = _time.sleep) -> pd.DataFrame:
    """Scheduled FOMC meetings from COVERAGE_START: the current calendar page plus the historical page of every
    earlier year. Any year panel (the year ahead included) or historical page with fewer than MIN_MEETINGS_PER_YEAR
    scheduled meetings (a partial parse or a format change) fails the source."""
    cur = parse_fomc_current(_get_text(FED_CALENDAR_URL, retries=retries, session=session, sleep=sleep))
    if cur.empty:
        raise FedCalendarError(f"{FED_CALENDAR_URL}: no meetings found (page format changed?)")
    per_year = cur["panel_year"].astype(int).value_counts()
    short = {int(y): int(n) for y, n in zip(per_year.index, per_year.to_numpy()) if n < MIN_MEETINGS_PER_YEAR}
    if short:
        raise FedCalendarError(f"{FED_CALENDAR_URL}: too few scheduled meetings per year panel: "
                               + ", ".join(f"{y}: {n}" for y, n in short.items()))
    frames = [cur.drop(columns=["panel_year"])]
    for year in range(COVERAGE_START.year, int(cur["panel_year"].min())):
        url = FED_HISTORICAL_URL.format(year=year)
        h = parse_fomc_historical(_get_text(url, retries=retries, session=session, sleep=sleep))
        if len(h) < MIN_MEETINGS_PER_YEAR:
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


class MassCancellation(ValueError):
    """A run would cancel more than MAX_CANCELLATIONS future events of one type (a truncated download)."""


def _latest(prev: pd.DataFrame) -> pd.DataFrame:
    p = prev.copy()
    p["available_utc"], p["event_utc"] = _utc_ns(p["available_utc"]), _utc_ns(p["event_utc"])
    return p.sort_values("available_utc", kind="stable").drop_duplicates("event_id", keep="last")


def _gone(latest: pd.DataFrame, fresh: pd.DataFrame, now: pd.Timestamp, types: set[str]) -> pd.DataFrame:
    return latest[(latest["status"] == SCHEDULED) & latest["event_type"].isin(types) & (latest["source"] != "manual")
                  & (pd.DatetimeIndex(latest["event_utc"]) > now) & ~latest["event_id"].isin(fresh["event_id"])]


def mass_cancellations(prev: pd.DataFrame | None, fresh: pd.DataFrame, retrieved_utc: pd.Timestamp,
                       fetched_types: Sequence[str] | None = None, limit: int = MAX_CANCELLATIONS,
                       allow: Sequence[str] = ()) -> dict[str, int]:
    """event_type -> number of future events this run would cancel, for the types above `limit` (not in `allow`)."""
    if prev is None or prev.empty:
        return {}
    now = pd.Timestamp(retrieved_utc).tz_convert("UTC")
    types = set(fetched_types) if fetched_types is not None else set(fresh["event_type"])
    n = _gone(_latest(prev), fresh, now, types).groupby("event_type").size()
    return {str(t): int(k) for t, k in n.items() if k > limit and str(t) not in set(allow)}


def check_last_ok(last_ok: dict[str, Any]) -> None:
    """last_ok must be keyed by event type. The first format (keyed by source: fred, fed, rule) would be misread as
    'never downloaded' or worse, so it is refused."""
    bad = sorted(set(last_ok) - set(EVENT_TYPES))
    if bad:
        raise ValueError(f"last_ok keys {bad} are not event types (old source-keyed metadata?); refusing to read it")


def merge_calendar(prev: pd.DataFrame | None, fresh: pd.DataFrame, retrieved_utc: pd.Timestamp,
                   last_ok: dict[str, pd.Timestamp] | None = None,
                   fetched_types: Sequence[str] | None = None,
                   allow_cancellations: Sequence[str] = ()) -> pd.DataFrame:
    """Add one download to the published calendar without rewriting it. `last_ok`: event_type -> the previous
    successful download of that type (before this run).

    * An event_id first seen now keeps its rule stamp when its type was never downloaded before (a backfill) or when
      the stamp is still in the future. Otherwise (a type downloaded before, rule stamp passed) it was added or moved
      late: available at `retrieved_utc`, even after the event (known_timeline counts it as past only from then).
      Hand-checked rows (source manual, SPACING_EXEMPT) keep their stamps.
    * A future event of a type in `fetched_types` (default: the types in `fresh`) whose latest status is scheduled and
      that `fresh` no longer lists gets a `cancelled` row at `retrieved_utc`; a cancelled one listed again gets a
      `scheduled` row. More than MAX_CANCELLATIONS for one type raises MassCancellation unless the type is in
      `allow_cancellations` (an operator's one-shot, verified override). Past events are never touched."""
    check_last_ok(last_ok or {})
    now = pd.Timestamp(retrieved_utc)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    fresh = fresh.copy()
    fresh["available_utc"] = _utc_ns(fresh["available_utc"])
    fresh["event_utc"] = _utc_ns(fresh["event_utc"])
    if prev is None or prev.empty:
        out = fresh
    else:
        mass = mass_cancellations(prev, fresh, now, fetched_types, allow=allow_cancellations)
        if mass:
            raise MassCancellation(f"refused: this run would cancel {mass} future events (limit {MAX_CANCELLATIONS})")
        prev = prev.copy()
        prev["available_utc"], prev["event_utc"] = _utc_ns(prev["available_utc"]), _utc_ns(prev["event_utc"])
        latest = _latest(prev)
        state = latest.set_index("event_id")["status"]
        fresh_key = fresh["event_id"] + "|" + fresh["status"]
        manual = (fresh["source"] == "manual").to_numpy()
        is_new = np.where(manual, ~fresh_key.isin(set(prev["event_id"] + "|" + prev["status"])).to_numpy(),
                          ~fresh["event_id"].isin(state.index).to_numpy())
        downloaded_before = fresh["event_type"].isin(set(last_ok or {})).to_numpy()
        rule = pd.DatetimeIndex(fresh["available_utc"])
        late = (is_new & downloaded_before & np.asarray(rule < now, dtype=bool) & ~manual
                & ~fresh["event_id"].isin(SPACING_EXEMPT).to_numpy())
        fresh["available_utc"] = rule.where(~late, now)
        future = np.asarray(pd.DatetimeIndex(fresh["event_utc"]) > now, dtype=bool)
        back = ~is_new & ~manual & future & (fresh["event_id"].map(state) == CANCELLED).to_numpy()
        revived = fresh[back].assign(available_utc=now)
        types = set(fetched_types) if fetched_types is not None else set(fresh["event_type"])
        cancelled = _gone(latest, fresh, now, types).assign(status=CANCELLED, available_utc=now,
                                                            vintage=_naive_day(now))
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
    """Parquet with the last successful download time per event type in the schema metadata (merge_calendar)."""
    table = pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[META_KEY] = json.dumps({k: pd.Timestamp(v).isoformat() for k, v in last_ok.items()}).encode()
    pq.write_table(table.replace_schema_metadata(meta), path, compression="zstd")


def read_release(path: str) -> tuple[pd.DataFrame, dict[str, pd.Timestamp]]:
    table = pq.read_table(path)
    raw = (table.schema.metadata or {}).get(META_KEY)
    last_ok = {k: pd.Timestamp(v) for k, v in json.loads(raw).items()} if raw else {}
    check_last_ok(last_ok)
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

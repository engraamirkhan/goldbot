"""Keyless release dates for the US calendar (calendar-v1, goldbot/data/us_calendar.py; ADR 0044 amends ADR 0008).

The FRED API (`fred/release/dates`) needs a key, and the FRED_API_KEY repo secret cannot be obtained. The same release
dates are published without a key by the Federal Reserve Bank of St. Louis on two public pages, and GitHub's runners
(and the Mac sandbox, checked 2026-10-25) can reach both:

* ALFRED's "Download Release Dates" text file, `alfred.stlouisfed.org/release/downloaddates?rid=<id>&ff=txt`: every
  past release date of one FRED release (the ALFRED vintage dates the API serves, 1950s onwards). One request per
  release. It lists only dates that have happened.
* FRED's release calendar, `fred.stlouisfed.org/releases/calendar?rid=<id>&y=<year>`: the past and scheduled dates of
  one release in one year (the page offers last year, this year and next year only; another year falls back to last
  year, so the page's own year is checked) with the scheduled time in US Central time. It supplies the scheduled
  future dates the API returns with `include_release_dates_with_no_data=true`.

Release dates = ALFRED (history) union the calendar for this year and next year. Over 2025-2026 the two agree on every
past date (checked 2026-10-25 for all eight releases). The rows keep source `fred` (the data and its lineage are the
same FRED/ALFRED release dates; only the transport differs), so a later API run merges into the published file without
spurious new events or cancellations.

Guards (each failure fails that release only; its published rows are kept):
* the release name on both pages must contain `Release.name` (a wrong id or a redirect fails);
* the ALFRED file must have its "Release Dates:" block and a date within ALFRED_MAX_AGE of the run (a stale or
  truncated file fails);
* the calendar page must be for the year asked and must list this year's dates (next year may be empty until the
  agencies publish it).
Reported, not failed: past dates of this year that the two pages disagree on, and calendar times that differ from the
time calendar-v1 uses (`Release.at_et`, or the hand-checked TIME_OVERRIDES entry); a flagged day is for review.
Responses are streamed and refused beyond MAX_RESPONSE_BYTES.

Requests are spaced CRAWL_DELAY apart (robots.txt asks for 1 s on fred and 2 s on alfred; both allow these paths):
8 releases x 3 requests, about a minute. FRED terms: the dates are facts published by US federal agencies (public
domain); the release keeps the FRED notice ("This product uses the FRED(R) API but is not endorsed or certified by the
Federal Reserve Bank of St. Louis").
"""
from __future__ import annotations

import html as _html
import re
import time as _time
from datetime import time
from typing import Any, Callable, NamedTuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from goldbot.data.us_calendar import (
    COVERAGE_START,
    ET,
    REVISION_ONLY,
    TIME_OVERRIDES,
    USER_AGENT,
    FredError,
    Release,
)

CT = ZoneInfo("America/Chicago")
ALFRED_DATES_URL = "https://alfred.stlouisfed.org/release/downloaddates?rid={rid}&ff=txt"
FRED_CALENDAR_URL = "https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={year}"
CRAWL_DELAY = 2.0                                  # seconds before every request (robots.txt: alfred 2, fred 1)
MAX_RESPONSE_BYTES = 2_000_000                     # pages are 4-100 kB; a larger body is refused, not parsed
ALFRED_MAX_AGE = pd.Timedelta(days=120)            # newest ALFRED date must be this recent (2025 lapse gap: ~7 weeks)

_DATE_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*$", re.M)
_ALFRED_NAME = re.compile(r"^Release:\s*(.+?)\s*$", re.M)
_CAL_TITLE = re.compile(r"<title>\s*(\d{4}) Economic Release Calendar - ([^|<]+?)\s*\|", re.S)
_CAL_DAY = re.compile(r'font-weight:\s*bold;?">\s*[A-Za-z]+day\s+([A-Za-z]+ \d{1,2}, \d{4})\s*</span>(.*?)'
                      r'(?=font-weight:\s*bold;?">\s*[A-Za-z]+day\s|Releases \d+ - \d+ of \d+|$)', re.S)
_CAL_ROW = re.compile(r'<td[^>]*>\s*(\d{1,2}:\d{2}\s*[ap]m)\s*</td>\s*<td[^>]*>\s*<a href="/release\?rid=(\d+)">'
                      r"([^<]+)</a>", re.S | re.I)


class KeylessDates(NamedTuple):
    dates: pd.DatetimeIndex          # ET calendar dates, sorted, unique, from COVERAGE_START
    times: pd.DataFrame              # calendar rows: day, time_et (this year and next year only)
    notes: list[str]                 # disagreements and time flags, for the step summary


def _check_name(found: str, rel: Release, where: str) -> None:
    if rel.name.lower() not in found.lower():
        raise FredError(f"{where}: release {rel.release_id} is {found!r}, expected a name containing {rel.name!r}")


def parse_alfred_dates(text: str, rel: Release) -> pd.DatetimeIndex:
    """ALFRED "Download Release Dates" text file -> sorted unique dates. The release name must match and the
    "Release Dates:" block must be present (an HTML error page or a format change fails)."""
    name = _ALFRED_NAME.search(text)
    if name is None or "Release Dates:" not in text:
        raise FredError(f"ALFRED release {rel.release_id}: not a release-dates file (format changed?)")
    _check_name(name.group(1), rel, "ALFRED")
    block = text[text.rindex("Release Dates:"):]
    days = pd.to_datetime(pd.Series(_DATE_LINE.findall(block), dtype=object), format="%Y-%m-%d", errors="coerce")
    out = pd.DatetimeIndex(days.dropna()).normalize().unique().sort_values()
    if len(out) == 0:
        raise FredError(f"ALFRED release {rel.release_id}: no dates in the file")
    return out


def ct_to_et(day: pd.Timestamp, clock: str) -> time:
    """A US Central wall time on one date ("7:30 am") -> the New York wall time (zone-converted, not a fixed +1 h)."""
    t = pd.Timestamp(f"{day:%Y-%m-%d} {clock.replace(' ', '').upper()}")
    return pd.Timestamp(t.to_pydatetime().replace(tzinfo=CT)).tz_convert(ET).time()


def parse_fred_calendar(page: str, rel: Release, year: int) -> pd.DataFrame:
    """FRED release calendar page for one release and year -> DataFrame(day, time_et), one row per listed date. The
    page must be for `year` (the site falls back to another year for years it does not offer) and for `rel`."""
    title = _CAL_TITLE.search(page)
    if title is None:
        raise FredError(f"FRED calendar rid={rel.release_id} y={year}: no calendar title (format changed?)")
    if int(title.group(1)) != year:
        raise FredError(f"FRED calendar rid={rel.release_id} y={year}: the page is for {title.group(1)} "
                        "(year not offered)")
    _check_name(_html.unescape(title.group(2)), rel, "FRED calendar")
    rows = []
    for m in _CAL_DAY.finditer(page):
        day = pd.Timestamp(pd.to_datetime(m.group(1), format="%B %d, %Y"))
        for clock, rid, name in _CAL_ROW.findall(m.group(2)):
            if int(rid) != rel.release_id:
                continue
            _check_name(_html.unescape(name), rel, "FRED calendar row")
            rows.append({"day": day, "time_et": ct_to_et(day, clock)})
    out = pd.DataFrame(rows, columns=["day", "time_et"])
    return out.drop_duplicates("day").sort_values("day").reset_index(drop=True)


def _read_capped(r: Any, url: str) -> str:
    """The streamed body, refused (FredError, no retry) beyond MAX_RESPONSE_BYTES."""
    chunks, n = [], 0
    for c in r.iter_content(chunk_size=65536):
        n += len(c)
        if n > MAX_RESPONSE_BYTES:
            raise FredError(f"{url}: response larger than {MAX_RESPONSE_BYTES:,} bytes; refused")
        chunks.append(c)
    return b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")


def _get(url: str, *, retries: int, session: requests.Session | None, sleep: Callable[[float], None]) -> str:
    get = session.get if session is not None else requests.get
    last = ""
    for i in range(retries):
        sleep(CRAWL_DELAY * (1 + 5 * i))
        try:
            r = get(url, timeout=60, headers={"User-Agent": USER_AGENT}, stream=True)
            try:
                if r.status_code == 200:
                    return _read_capped(r, url)
                last = f"HTTP {r.status_code}"
            finally:
                r.close()
        except requests.RequestException as exc:
            last = type(exc).__name__
    raise FredError(f"{url}: {last}")


def fetch_release_keyless(rel: Release, now: pd.Timestamp, *, retries: int = 3,
                          session: requests.Session | None = None,
                          sleep: Callable[[float], None] = _time.sleep) -> KeylessDates:
    """All past (ALFRED) and scheduled (FRED calendar, this year and next) dates of one release since
    COVERAGE_START. Raises FredError when a guard fails (module docstring)."""
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    today = now.tz_convert(ET).tz_localize(None).normalize()
    past = parse_alfred_dates(_get(ALFRED_DATES_URL.format(rid=rel.release_id), retries=retries, session=session,
                                   sleep=sleep), rel)
    if today - past.max() > ALFRED_MAX_AGE:
        raise FredError(f"ALFRED release {rel.release_id}: newest date {past.max():%Y-%m-%d} is more than "
                        f"{ALFRED_MAX_AGE.days} days old (stale file?)")
    notes: list[str] = []
    frames = []
    for year in (today.year, today.year + 1):
        page = _get(FRED_CALENDAR_URL.format(rid=rel.release_id, year=year), retries=retries, session=session,
                    sleep=sleep)
        try:
            cal = parse_fred_calendar(page, rel, year)
        except FredError as exc:
            if year == today.year or "year not offered" not in str(exc):
                raise
            notes.append(f"{rel.event_type}: {year} calendar not offered yet")
            continue
        if year == today.year and cal.empty:
            raise FredError(f"FRED calendar rid={rel.release_id} y={year}: no dates listed for the current year")
        frames.append(cal)
    times = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["day", "time_et"])
    listed = pd.DatetimeIndex(times["day"])
    this_year_past = listed[(listed.year == today.year) & (listed < today)]
    alfred_this_year = past[(past.year == today.year) & (past < today)]
    only_cal = this_year_past.difference(alfred_this_year)
    only_alfred = alfred_this_year.difference(this_year_past)
    if len(only_cal) or len(only_alfred):
        notes.append(f"{rel.event_type}: ALFRED and the FRED calendar disagree on past {today.year} dates "
                     f"(calendar only {[f'{d:%Y-%m-%d}' for d in only_cal]}, "
                     f"ALFRED only {[f'{d:%Y-%m-%d}' for d in only_alfred]}); the union is used")
    used = [TIME_OVERRIDES.get(f"{rel.event_type}:{d:%Y-%m-%d}", rel.at_et) for d in times["day"]]
    off = times.assign(used=pd.Series(used, index=times.index, dtype=object))
    revision = [f"{rel.event_type}:{d:%Y-%m-%d}" in REVISION_ONLY for d in times["day"]]
    off = off[(off["time_et"] != off["used"]).to_numpy() & ~pd.Series(revision, dtype=bool).to_numpy()]
    notes += [f"TIME FLAG {rel.event_type}:{r.day:%Y-%m-%d} listed at {r.time_et:%H:%M} ET, calendar-v1 uses "
              f"{r.used:%H:%M} ET (check, then add to TIME_OVERRIDES)" for r in off.itertuples()]
    dates = past.union(listed).normalize().unique().sort_values()
    return KeylessDates(pd.DatetimeIndex(dates[dates >= COVERAGE_START]), times, notes)

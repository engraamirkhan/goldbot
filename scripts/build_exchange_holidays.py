"""Build config/research/exchange_holidays.yaml: NYSE, LSE and Xetra full-day closures from a published calendar.

BACKLOG 152. The research catalogue (S05 pre-holiday and every detector reading `ctx.events`) used to infer a holiday
as "a weekday with no bar in the long daily series": a data gap read as a holiday, and the set was known only after
the fact. This snapshot comes from `exchange_calendars` (the open-source exchange calendars maintained from the
exchanges' published holiday notices) at a PINNED version, so the file is versioned, reviewable and rebuilt the same
way every time. No market data: dates and names only (the repo is public; ADR on data-local-only is untouched).

    uv run --no-project --with exchange_calendars==4.13.2 --with pyyaml python scripts/build_exchange_holidays.py
    uv run --no-project --with exchange_calendars==4.13.2 --with pyyaml python scripts/build_exchange_holidays.py --check

Availability (`available_utc`, when the closure became knowable), the as-of contract of the catalogue:
* regular holidays (the exchange's recurring rules): 1 December of the prior year, 00:00 UTC. An ASSUMED publication
  floor, not a measured date: NYSE publishes its holidays years ahead, UK bank holidays and Deutsche Boerse's trading
  calendar are out by autumn of the prior year. It only matters for a decision taken in December about a holiday in
  early January (New Year's Day); every other use is months after the floor.
* ad hoc closures (`AD_HOC` below, one per exchange_calendars `adhoc_holidays` entry): the day after the exchange's
  notice, 00:00 UTC (a day of margin over time zones), or the floor rule for one-offs proclaimed more than a year
  ahead (jubilees, the millennium). An unannounced closure (11 Sept 2001) or one whose notice date was not verified is
  available only the day after it happened: it can never be traded ahead.
A new ad hoc date in a newer exchange_calendars release fails the build until it is added to AD_HOC with its notice.

Early closes (BACKLOG 237): each exchange's `early_closes` (NYSE 13:00 ET half days, LSE 12:30 on 24/31 Dec, Xetra's
last session of the year), with the close instant in UTC and `available_utc` by the same rules: regular ones from the
floor, the ad hoc ones (`ADHOC_EARLY`, from exchange_calendars' special_closes_adhoc) the day after unless a notice is
recorded.

Yearly refresh (BACKLOG 237): before 31 December, bump VALID_THROUGH by a year (and REVISION; the exchange_calendars
pin when a newer release exists, adding any new ad hoc date with its notice), rebuild, and run `--check` in review.
tests/test_exchange_holidays_refresh.py fails once valid_through is less than a year away.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "research" / "exchange_holidays.yaml"
XCALS_VERSION = "4.13.2"
REVISION = 2                # r2 (2026-11-14): valid through 2028, early closes listed (4.13.2 is still the latest)
VALID_FROM = date(1990, 1, 1)
VALID_THROUGH = date(2028, 12, 31)
EXCHANGES = {"US": "XNYS", "UK": "XLON", "DE": "XETR"}
FLOOR = "floor"            # one-off proclaimed > 1 year ahead: the regular floor rule
AFTER = "after"            # unannounced or notice not verified: knowable the day after

# (exchange, date) -> (notice date or FLOOR/AFTER, what). Notice dates are the exchange's or government's public notice.
AD_HOC: dict[tuple[str, str], tuple[str, str]] = {
    ("US", "1994-04-27"): (AFTER, "National day of mourning, President Nixon (notice date not verified)"),
    ("US", "2001-09-11"): (AFTER, "11 September attacks: unannounced closure"),
    ("US", "2001-09-12"): (AFTER, "11 September attacks: closure extended day by day"),
    ("US", "2001-09-13"): (AFTER, "11 September attacks: closure extended day by day"),
    ("US", "2001-09-14"): (AFTER, "11 September attacks: closure extended day by day"),
    ("US", "2004-06-11"): ("2004-06-07", "National day of mourning, President Reagan"),
    ("US", "2007-01-02"): ("2006-12-28", "National day of mourning, President Ford"),
    ("US", "2012-10-29"): ("2012-10-28", "Hurricane Sandy"),
    ("US", "2012-10-30"): ("2012-10-29", "Hurricane Sandy (second day)"),
    ("US", "2018-12-05"): ("2018-12-01", "National day of mourning, President G.H.W. Bush"),
    ("US", "2025-01-09"): ("2024-12-31", "National day of mourning, President Carter"),
    ("UK", "1995-05-08"): (FLOOR, "VE Day 50th anniversary (May bank holiday moved by proclamation)"),
    ("UK", "1999-12-31"): (FLOOR, "Millennium bank holiday"),
    ("UK", "2002-06-03"): (FLOOR, "Golden Jubilee (spring bank holiday moved)"),
    ("UK", "2002-06-04"): (FLOOR, "Golden Jubilee"),
    ("UK", "2011-04-29"): ("2010-11-23", "Royal wedding"),
    ("UK", "2012-06-04"): (FLOOR, "Diamond Jubilee (spring bank holiday moved)"),
    ("UK", "2012-06-05"): (FLOOR, "Diamond Jubilee"),
    ("UK", "2020-05-08"): (FLOOR, "VE Day 75th anniversary (May bank holiday moved, notice June 2019)"),
    ("UK", "2022-06-02"): (FLOOR, "Platinum Jubilee (spring bank holiday moved, notice Nov 2020)"),
    ("UK", "2022-06-03"): (FLOOR, "Platinum Jubilee"),
    ("UK", "2022-09-19"): ("2022-09-10", "State funeral of Queen Elizabeth II"),
    ("UK", "2023-05-08"): ("2022-11-06", "Coronation of King Charles III"),
    ("DE", "2007-05-28"): (FLOOR, "Whit Monday 2007 (trading calendar)"),
    ("DE", "2017-10-31"): (FLOOR, "Reformation Day 500th anniversary (one-off national holiday)"),
}

# (exchange, date) -> (notice date or FLOOR/AFTER, what): exchange_calendars' ad hoc early closes. Notices not verified.
ADHOC_EARLY: dict[tuple[str, str], tuple[str, str]] = {
    ("US", "1997-12-26"): (AFTER, "13:00 close, day after Christmas (notice date not verified)"),
    ("US", "1999-12-31"): (AFTER, "13:00 close, millennium New Year's Eve (notice date not verified)"),
    ("US", "2003-12-26"): (AFTER, "13:00 close, day after Christmas (notice date not verified)"),
}


def _utc(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_for(day: date) -> date:
    return date(day.year - 1, 12, 1)


def available(ex: str, day: date) -> tuple[str, str, str]:
    """(kind, available_utc, note) for one closed weekday."""
    hit = AD_HOC.get((ex, day.isoformat()))
    if hit is None:
        return "regular", _utc(floor_for(day)), ""
    notice, what = hit
    if notice == FLOOR:
        return "adhoc", _utc(floor_for(day)), what
    if notice == AFTER:
        return "adhoc", _utc(day + timedelta(days=1)), what
    return "adhoc", _utc(date.fromisoformat(notice) + timedelta(days=1)), what


def build() -> str:
    import exchange_calendars as xc  # type: ignore[import-not-found]  # build-time only, never a project dependency
    import pandas as pd

    got = getattr(xc, "__version__", "?")
    if got != XCALS_VERSION:
        raise SystemExit(f"exchange_calendars {got} != pinned {XCALS_VERSION}")
    lines = [
        "# GENERATED by scripts/build_exchange_holidays.py (BACKLOG 152); do not edit by hand (review: --check).",
        "# Full-day closures (weekdays only) of the cash exchanges whose indices the catalogue trades: US (NYSE, XNYS) for",
        "# US500/USTEC/US30, UK (LSE, XLON) for UK100, DE (Xetra, XETR) for DE40; and each exchange's early closes",
        "# (close_utc = the early close instant).",
        "# available_utc = when the closure became knowable (the as-of key): regular rules 1 Dec of the prior year (an",
        "# assumed publication floor), ad hoc closures the day after the notice, or the day after the closure when it was",
        "# unannounced or the notice was not verified. See the generator's docstring.",
        f"version: xcals-{XCALS_VERSION}-r{REVISION}",
        f"source: 'exchange_calendars {XCALS_VERSION}, the published holiday calendars of XNYS, XLON and XETR'",
        "generator: scripts/build_exchange_holidays.py",
        f"valid_from: '{VALID_FROM.isoformat()}'",
        f"valid_through: '{VALID_THROUGH.isoformat()}'",
        "exchanges:",
    ]
    seen: set[tuple[str, str]] = set()
    for ex, code in EXCHANGES.items():
        cal = xc.get_calendar(code, start=VALID_FROM.isoformat(), end=VALID_THROUGH.isoformat())
        names = cal.regular_holidays.holidays(pd.Timestamp(VALID_FROM), pd.Timestamp(VALID_THROUGH), return_name=True)
        adhoc = {pd.Timestamp(d).date() for d in cal.adhoc_holidays}
        weekdays = pd.bdate_range(VALID_FROM, VALID_THROUGH)
        closed = weekdays.difference(pd.DatetimeIndex(cal.sessions).tz_localize(None))
        lines += [f"  {ex}:", f"    calendar: {code}", "    days:"]
        for ts in closed:
            day = ts.date()
            kind, av, note = available(ex, day)
            if (day in adhoc) != (kind == "adhoc"):
                raise SystemExit(f"{code} {day}: ad hoc in exchange_calendars but not in AD_HOC (or vice versa)")
            seen.add((ex, day.isoformat()))
            name = note or str(names.get(ts, "closed"))
            lines.append(f"      - {{date: '{day.isoformat()}', kind: {kind}, available_utc: '{av}', "
                         f"name: \"{name}\"}}")
        adhoc_early = {pd.Timestamp(d).date() for _, dates in cal.special_closes_adhoc for d in dates}
        lines.append("    early_closes:")
        for s in cal.early_closes:
            day = pd.Timestamp(s).date()
            if not VALID_FROM <= day <= VALID_THROUGH:
                continue
            hit = ADHOC_EARLY.get((ex, day.isoformat()))
            if (day in adhoc_early) != (hit is not None):
                raise SystemExit(f"{code} {day}: ad hoc early close in exchange_calendars but not in ADHOC_EARLY "
                                 "(or vice versa)")
            if hit is None:
                kind, av, note = "regular", _utc(floor_for(day)), "early close"
            else:
                after = day + timedelta(days=1) if hit[0] == AFTER else date.fromisoformat(hit[0]) + timedelta(days=1)
                kind, av, note = "adhoc", _utc(after), hit[1]
            close = pd.Timestamp(cal.closes[s]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append(f"      - {{date: '{day.isoformat()}', kind: {kind}, close_utc: '{close}', "
                         f"available_utc: '{av}', name: \"{note}\"}}")
    stale = sorted(set(AD_HOC) - seen)
    if stale:
        raise SystemExit(f"AD_HOC dates that are not closures in the calendar: {stale}")
    text = "\n".join(lines) + "\n"
    import yaml
    doc = yaml.safe_load(text)                    # the file must parse; the loader validates it on every read
    assert set(doc["exchanges"]) == set(EXCHANGES)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed file differs from a rebuild")
    a = ap.parse_args(argv)
    text = build()
    if a.check:
        same = OUT.exists() and OUT.read_text() == text
        print("exchange_holidays.yaml up to date" if same else "exchange_holidays.yaml differs from a rebuild")
        return 0 if same else 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

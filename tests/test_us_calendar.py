"""US calendar, tiers 1 and 2 (data-us-calendar workflow, release calendar-v1): DST-correct release times, FOMC dates
from the Fed's calendar pages (synthetic HTML), rule-derived ISM dates, conservative schedule availability (lead,
shutdown floors), history-keeping merges and the opt-in proximity features (as-of on available_utc, truncation
look-ahead)."""
import importlib.util
import re
from datetime import time
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest

from goldbot.data import us_calendar as uc
from goldbot.data.release import read_us_calendar_files
from goldbot.features import build_features, registry
from goldbot.features import us_calendar as fc

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2024-06-01 06:00", tz="UTC")


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.fixture
def enabled():
    """Register the opt-in features for one test and restore the registry afterwards (other tests see the default)."""
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    yield fc.enable()
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def _fred_dates() -> dict[str, pd.DatetimeIndex]:
    """Synthetic FRED release dates, 2023-11 .. 2024-12 (the real ones come from the workflow)."""
    thursdays = pd.date_range("2023-11-02", "2024-12-26", freq="W-THU")
    return {"ppi": pd.DatetimeIndex(["2024-01-12", "2024-02-16", "2024-03-14", "2024-04-11", "2024-05-14",
                                     "2024-06-13", "2024-07-12"]),
            "jobless_claims": thursdays,
            "cpi": pd.DatetimeIndex(["2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15", "2024-06-12",
                                     "2024-07-11"]),
            "nfp": pd.DatetimeIndex(["2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05", "2024-05-03", "2024-06-07",
                                     "2024-07-05"]),
            "gdp": pd.DatetimeIndex(["2023-12-21", "2024-01-25", "2024-02-28", "2024-03-28", "2024-04-25", "2024-05-30",
                                     "2024-06-27", "2024-07-25"])}


def _fomc() -> pd.DataFrame:
    """Scheduled meetings as fetch_fomc returns them: statement day, press-conference link, minutes release date."""
    return pd.DataFrame({"day": pd.DatetimeIndex(["2023-12-13", "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
                                                  "2024-07-31"]),
                         "press": [True, True, True, True, True, False],
                         "minutes_day": pd.DatetimeIndex(["2024-01-03", "2024-02-21", "2024-04-10", "2024-05-22", None,
                                                          None])})


def _move(d: pd.DatetimeIndex, old: str, new: str) -> pd.DatetimeIndex:
    """A release date moved (or dropped and another added): the next download lists `new` instead of `old`."""
    return pd.DatetimeIndex([x for x in d if x != pd.Timestamp(old)] + [pd.Timestamp(new)]).sort_values()


def _cal(now: pd.Timestamp = NOW) -> pd.DataFrame:
    return uc.merge_calendar(None, uc.calendar_frame(_fred_dates(), now, _fomc()), now, {})


def _ok(t: pd.Timestamp) -> dict[str, pd.Timestamp]:
    """Every event type downloaded successfully at `t` (last_ok is kept per event type)."""
    return {e: t for e in uc.EVENT_TYPES}


# --------------------------------------------------------------------------- timing
def test_release_times_follow_new_york_dst_on_both_sides_of_each_switch():
    ev = uc.et_to_utc(pd.DatetimeIndex(["2024-03-08", "2024-03-11", "2024-11-01", "2024-11-04", "2024-07-11"]),
                      uc.RELEASES[0].at_et)
    assert list(ev) == [_utc("2024-03-08 13:30"), _utc("2024-03-11 12:30"), _utc("2024-11-01 12:30"),
                        _utc("2024-11-04 13:30"), _utc("2024-07-11 12:30")]
    cal = _cal()
    jolts_like = cal[cal["event_type"] == "ism_manufacturing"]
    assert set(pd.DatetimeIndex(jolts_like["event_utc"]).tz_convert(uc.ET).strftime("%H:%M")) == {"10:00"}
    minutes = cal[cal["event_type"] == "fomc_minutes"]
    assert set(pd.DatetimeIndex(minutes["event_utc"]).tz_convert(uc.ET).strftime("%H:%M")) == {"14:00"}


def test_tiers_keep_the_existing_labels():
    cal = _cal()
    tiers = cal.groupby("event_type")["tier"].first().to_dict()
    assert {t for t, v in tiers.items() if v == 1} == {"cpi", "nfp", "pce", "fomc_statement", "fomc_press_conference"}
    assert uc.TIERS["pce"] == 1 and uc.TIERS["gdp"] == 2 and uc.TIERS["fomc_minutes"] == 2
    assert uc.TIERS["gdp_advance"] == 2           # promotion to tier 1 is an owner / trading-safety decision
    # settings risk.blackout.events and econ_calendar's tier-1 patterns name the same four families
    from goldbot.data.econ_calendar import TIER1_PATTERNS
    assert set(TIER1_PATTERNS) == {"CPI", "NFP", "FOMC", "PCE"}


def test_fomc_minutes_use_the_released_date_else_three_weeks():
    m = _cal()
    m = m[m["event_type"] == "fomc_minutes"]
    assert {"fomc_minutes:2024-01-03", "fomc_minutes:2024-04-10"} <= set(m["event_id"])       # page's Released date
    assert {"fomc_minutes:2024-07-03", "fomc_minutes:2024-08-21"} <= set(m["event_id"])       # not released: + 21 d


def test_fomc_times_follow_the_era():
    meetings = pd.DataFrame({"day": pd.DatetimeIndex(["2010-03-16", "2011-04-27", "2011-08-09", "2012-12-12",
                                                      "2013-01-30", "2019-01-30", "2026-10-28"]),
                             "press": [False, True, False, True, False, False, False],
                             "minutes_day": pd.NaT})
    rows = uc.fomc_rows(meetings, NOW)
    et = pd.DatetimeIndex(rows["event_utc"]).tz_convert(uc.ET).strftime("%Y-%m-%d %H:%M")
    got = dict(zip(rows["event_id"], et))
    assert got["fomc_statement:2010-03-16"] == "2010-03-16 14:15"
    assert got["fomc_statement:2011-04-27"] == "2011-04-27 12:30"        # 2011-12 press-conference days
    assert got["fomc_press_conference:2011-04-27"] == "2011-04-27 14:15"
    assert got["fomc_statement:2011-08-09"] == "2011-08-09 14:15"
    assert got["fomc_statement:2013-01-30"] == "2013-01-30 14:00"
    assert "fomc_press_conference:2013-01-30" not in got                  # quarterly until 2018
    assert got["fomc_press_conference:2019-01-30"] == "2019-01-30 14:30"  # every meeting from 2019, link or not
    assert got["fomc_press_conference:2026-10-28"] == "2026-10-28 14:30"
    assert set(rows.loc[rows["event_type"] != "fomc_minutes", "tier"]) == {1}
    # the Fed is not stopped by shutdowns: a week's lead, no floor
    oct25 = rows[rows["event_id"] == "fomc_statement:2026-10-28"]
    assert (oct25["event_utc"] - oct25["available_utc"]).iloc[0] == uc.SCHEDULE_LEAD


CURRENT_HTML = """
<div class="panel panel-default"><div class="panel-heading"><h4><a id="1">2025 FOMC Meetings</a></h4></div>
<div class="row fomc-meeting" ">
 <div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
 <div class="fomc-meeting__date col-xs-4">28-29</div>
 <a href="/monetarypolicy/fomcpresconf20250129.htm">Press Conference</a>
 <strong>Minutes:</strong><br> (Released February 19, 2025)
</div>
<div class="fomc-meeting--shaded row fomc-meeting" ">
 <div class="fomc-meeting--shaded fomc-meeting__month col-xs-5"><strong>Apr/May</strong></div>
 <div class="fomc-meeting__date col-xs-4">30-1*</div>
</div>
<div class="fomc-meeting--shaded row fomc-meeting">
 <div class="fomc-meeting__month col-xs-5"><strong>August</strong></div>
 <div class="fomc-meeting__date col-xs-4">22 (notation vote)</div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="2">2026 FOMC Meetings</a></h4></div>
<div class="row fomc-meeting" ">
 <div class="fomc-meeting__month col-xs-5"><strong>October</strong></div>
 <div class="fomc-meeting__date col-xs-4">27-28</div>
</div>
<div class="row fomc-meeting" ">
 <div class="fomc-meeting__month col-xs-5"><strong>March</strong></div>
 <div class="fomc-meeting__date col-xs-4">(unscheduled)</div>
</div>
"""

HISTORICAL_HTML = """
<h5 class="panel-heading panel-heading--shaded">January 26-27 Meeting - 2010</h5>
<p>Minutes (Released Feb 17, 2010):</p>
<h5>May 9 Conference Call - 2010</h5>
<p>Minutes (Released May 19, 2010):</p>
<h5>April/May 30-1 Meeting - 2019</h5>
<p><a href="/monetarypolicy/fomcpresconf20190501.htm">Press Conference</a></p>
<p>Minutes (Released May 22, 2019):</p>
<h5>March 15 (unscheduled) Meeting - 2020</h5>
<h5>March 17-18 (cancelled) Meeting - 2020</h5>
<h5>October 4 (unscheduled) - 2019</h5>
<h5>Oct/Nov 31-1 Meeting - 2017</h5>
<h5>December 13 Meeting - 2011</h5>
"""


def test_parse_the_fed_current_calendar_page():
    m = uc.parse_fomc_current(CURRENT_HTML)
    assert list(m["day"]) == [pd.Timestamp("2025-01-29"), pd.Timestamp("2025-05-01"), pd.Timestamp("2026-10-28")]
    assert list(m["press"]) == [True, False, False]
    assert m["minutes_day"].iloc[0] == pd.Timestamp("2025-02-19") and pd.isna(m["minutes_day"].iloc[2])


def test_parse_the_fed_historical_pages_keeps_only_scheduled_meetings():
    m = uc.parse_fomc_historical(HISTORICAL_HTML)
    assert list(m["day"]) == [pd.Timestamp("2010-01-27"), pd.Timestamp("2019-05-01"), pd.Timestamp("2017-11-01"),
                              pd.Timestamp("2011-12-13")]
    assert list(m["press"]) == [False, True, False, False]
    assert m["minutes_day"].iloc[0] == pd.Timestamp("2010-02-17") and m["minutes_day"].iloc[1] == pd.Timestamp("2019-05-22")


def _current_page(counts: dict[int, int]) -> str:
    """A synthetic fomccalendars.htm with `n` scheduled meetings in each year's panel."""
    months = ["January", "March", "April", "June", "July", "September", "October", "December"]
    out = []
    for y, n in counts.items():
        out.append(f'<div class="panel"><div class="panel-heading"><h4><a id="{y}">{y} FOMC Meetings</a></h4></div>')
        out += [f'<div class="row fomc-meeting" "><div class="fomc-meeting__month"><strong>{months[i]}</strong></div>'
                f'<div class="fomc-meeting__date">{10 + i}-{11 + i}</div></div>' for i in range(n)]
    return "\n".join(out)


class _Pages:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(url)
        return type("R", (), {"status_code": 200 if url in self.pages else 404, "text": self.pages.get(url, "")})()


def test_fetch_fomc_reads_every_year_before_the_current_page_and_fails_on_a_changed_format():
    year = "".join(f'<h5>{mo} {d} Meeting - {{y}}</h5>' for mo, d in
                   (("January", 27), ("March", 16), ("April", 28), ("June", 23), ("August", 10), ("September", 21),
                    ("November", 3), ("December", 14)))
    pages = {uc.FED_CALENDAR_URL: _current_page({2012: 8, 2013: 8})}
    pages |= {uc.FED_HISTORICAL_URL.format(year=y): year.format(y=y) for y in (2010, 2011)}
    m = uc.fetch_fomc(session=cast(Any, _Pages(pages)), sleep=lambda _: None)
    assert pd.DatetimeIndex(m["day"]).year.min() == 2010 and len(m) == 4 * 8
    short = dict(pages)
    short[uc.FED_CALENDAR_URL] = _current_page({2012: 8, 2013: 3})            # a partial parse of the next year
    with pytest.raises(uc.FedCalendarError, match="2013: 3"):
        uc.fetch_fomc(session=cast(Any, _Pages(short)), sleep=lambda _: None)
    pages[uc.FED_HISTORICAL_URL.format(year=2011)] = "<html>redesigned</html>"
    with pytest.raises(uc.FedCalendarError, match="2011"):
        uc.fetch_fomc(session=cast(Any, _Pages(pages)), sleep=lambda _: None)
    with pytest.raises(uc.FedCalendarError, match="HTTP 404"):
        uc.fetch_fomc(session=cast(Any, _Pages({})), sleep=lambda _: None)


def test_ism_is_the_first_and_third_us_business_day():
    mfg = uc.nth_us_business_days(pd.Timestamp("2024-01-01"), pd.Timestamp("2025-09-30"), 1)
    srv = uc.nth_us_business_days(pd.Timestamp("2024-01-01"), pd.Timestamp("2025-09-30"), 3)
    assert pd.Timestamp("2024-01-02") in mfg and pd.Timestamp("2024-01-04") in srv   # New Year's Day skipped
    assert pd.Timestamp("2025-09-02") in mfg and pd.Timestamp("2025-09-04") in srv   # Labor Day skipped
    assert pd.Timestamp("2024-06-03") in mfg and pd.Timestamp("2024-06-05") in srv   # June 1 is a Saturday
    assert len(mfg) == len(srv) == 21


def test_coverage_starts_in_2010_and_ism_rows_run_ahead_of_the_run():
    cal = _cal()
    assert pd.DatetimeIndex(cal["event_utc"]).min() >= _utc("2010-01-01")
    ism = cal[cal["event_type"] == "ism_manufacturing"]
    assert pd.DatetimeIndex(ism["event_utc"]).min() < _utc("2010-01-06")
    assert pd.DatetimeIndex(ism["event_utc"]).max() > NOW + pd.Timedelta(days=360)


# --------------------------------------------------------------------------- availability
def test_schedule_is_known_a_week_ahead_and_never_after_the_event():
    ev = pd.DatetimeIndex([_utc("2024-01-12 13:30")])
    assert uc.rule_available_utc(ev, "bls")[0] == _utc("2024-01-05 13:30")
    cal = _cal()
    assert (pd.DatetimeIndex(cal["available_utc"]) <= pd.DatetimeIndex(cal["event_utc"])).all()
    assert (cal["ts_utc"] == cal["available_utc"]).all()


def test_shutdown_reschedules_are_not_known_before_the_floor():
    ev = uc.et_to_utc(pd.DatetimeIndex(["2025-10-16", "2025-12-10", "2026-01-15", "2025-09-25"]), uc.RELEASES[0].at_et)
    av = uc.rule_available_utc(ev, "bls")
    assert av[0] == ev[0]                                                   # during the shutdown: known when it happens
    assert av[1] == pd.Timestamp("2025-12-05", tz=uc.ET).tz_convert("UTC")   # floor beats event - 7 days
    assert av[2] == ev[2] - uc.SCHEDULE_LEAD                                # past floor + lead: the plain rule
    assert av[3] == ev[3] - uc.SCHEDULE_LEAD                                # before the shutdown began: unaffected
    ev13 = uc.et_to_utc(pd.DatetimeIndex(["2013-10-30"]), uc.RELEASES[0].at_et)
    assert uc.rule_available_utc(ev13, "bls")[0] == ev13[0]


# --------------------------------------------------------------------------- merging
def test_late_new_events_are_stamped_at_first_sight_and_vanished_ones_cancelled():
    pub = _cal()
    t2 = NOW + pd.Timedelta(days=7)
    d = _fred_dates()
    d["ppi"] = _move(d["ppi"], "2024-06-13", "2024-06-14")   # rescheduled
    out = uc.merge_calendar(pub, uc.calendar_frame(d, t2, _fomc()), t2, _ok(NOW))
    uc.check_keeps_history(pub, out)
    moved = out[out["event_id"] == "ppi:2024-06-14"]
    assert len(moved) == 1 and moved["available_utc"].iloc[0] == t2        # rule stamp (Jun 7) < previous download
    old = out[out["event_id"] == "ppi:2024-06-13"].sort_values("available_utc")
    assert list(old["status"]) == ["scheduled", "cancelled"] and old["available_utc"].iloc[1] == t2
    assert len(out) == len(pub) + 2                                          # nothing else changed
    # it comes back: a scheduled row at that download; past events that vanish are never touched
    t3 = t2 + pd.Timedelta(days=1)
    d2 = _fred_dates()
    d2["ppi"] = _move(d2["ppi"], "2024-01-12", "2024-06-14")
    out2 = uc.merge_calendar(out, uc.calendar_frame(d2, t3, _fomc()), t3, _ok(t2))
    back = out2[out2["event_id"] == "ppi:2024-06-13"].sort_values("available_utc")
    assert list(back["status"]) == ["scheduled", "cancelled", "scheduled"]
    assert (out2["event_id"] == "ppi:2024-01-12").sum() == 1 and len(out2) == len(out) + 1


def test_a_type_that_failed_to_download_is_not_cancelled():
    pub = _cal()
    t2 = NOW + pd.Timedelta(days=7)
    d = _fred_dates()
    del d["ppi"]
    out = uc.merge_calendar(pub, uc.calendar_frame(d, t2, _fomc()), t2, _ok(NOW),
                            fetched_types=["jobless_claims", "cpi", "nfp", *uc.FOMC_TYPES, *uc.ISM])
    assert ((out["status"] == "cancelled") & (out["source"] != "manual")).sum() == 0 and len(out) == len(pub)


def test_check_keeps_history_refuses_fewer_rows_or_changed_rows():
    pub = _cal()
    uc.check_keeps_history(None, pub)
    uc.check_keeps_history(pub, pub)
    with pytest.raises(uc.HistoryLoss, match="rule"):
        uc.check_keeps_history(pub, pub[pub["source"] != "rule"])
    changed = pub.copy()
    av = pd.DatetimeIndex(changed["available_utc"])
    changed["available_utc"] = av.where(np.arange(len(av)) != 3, av[3] - pd.Timedelta(days=1))
    with pytest.raises(uc.HistoryLoss, match="missing or changed"):
        uc.check_keeps_history(pub, changed)


def test_release_file_round_trips_rows_and_last_download_times(tmp_path):
    pub = _cal()
    p = tmp_path / uc.ASSET
    uc.write_release(pub, str(p), {"cpi": NOW})
    back, ok = uc.read_release(str(p))
    assert len(back) == len(pub) and ok == {"cpi": NOW}
    loaded = read_us_calendar_files(tmp_path)
    assert len(loaded) == len(pub) and str(loaded["available_utc"].dt.tz) == "UTC"
    assert read_us_calendar_files(tmp_path / "missing").empty
    pub.drop(columns=["available_utc"]).to_parquet(tmp_path / "bad.parquet")
    with pytest.raises(ValueError, match="available_utc"):
        read_us_calendar_files(tmp_path / "bad.parquet")


# --------------------------------------------------------------------------- FRED client
class _Resp:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body = status, body

    def json(self) -> dict:
        return self._body


class _Session:
    def __init__(self, name: str, dates: list[str], status: int = 200):
        self.name, self.dates, self.status = name, dates, status
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, dict(params or {})))
        if self.status != 200:
            return _Resp(self.status, {})
        if url.endswith("/release"):
            return _Resp(200, {"releases": [{"id": params["release_id"], "name": self.name}]})
        return _Resp(200, {"release_dates": [{"release_id": params["release_id"], "date": d} for d in self.dates]})


def test_fetch_release_checks_the_release_name_and_keeps_the_key_out_of_errors():
    rel = next(r for r in uc.RELEASES if r.event_type == "ppi")
    s = _Session("Producer Price Index", ["2009-12-15", "2010-01-14", "2010-01-14", "2027-01-14"])
    d = uc.fetch_release(rel, "SECRETKEY123", session=cast(Any, s), sleep=lambda _: None)
    assert list(d) == [pd.Timestamp("2010-01-14"), pd.Timestamp("2027-01-14")]          # before 2010 dropped
    url, params = s.calls[1]
    assert params["include_release_dates_with_no_data"] == "true" and params["realtime_start"] == "2010-01-01"
    with pytest.raises(uc.FredError, match="expected a name") as e1:
        uc.fetch_release(rel, "SECRETKEY123", session=cast(Any, _Session("Consumer Price Index", [])), sleep=lambda _: None)
    with pytest.raises(uc.FredError, match="HTTP 500") as e2:
        uc.fetch_release(rel, "SECRETKEY123", session=cast(Any, _Session("x", [], status=500)), sleep=lambda _: None)
    assert "SECRETKEY123" not in str(e1.value) + str(e2.value)
    with pytest.raises(uc.FredError):
        uc.parse_release_dates({"error_code": 400})


# --------------------------------------------------------------------------- feature
def _bars(start: str, end: str, freq: str = "15min") -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": pd.date_range(start, end, freq=freq, tz="UTC")})


def test_proximity_minutes_match_their_definition(enabled):
    cal = _cal()
    bars = _bars("2024-02-10", "2024-02-25")
    X = build_features(bars, enabled, {"us_calendar": cal})
    t = _utc("2024-02-16 13:00")                    # PPI 08:30 ET = 13:30 UTC (winter)
    row = (bars["ts_utc"] == t).to_numpy()
    assert X.loc[row, "min_to_next_tier2_pit"].item() == 30.0
    assert X.loc[row, "min_since_last_tier2_pit"].item() == pytest.approx((t - _utc("2024-02-15 13:30")) / pd.Timedelta(minutes=1))
    at = (bars["ts_utc"] == _utc("2024-02-16 13:30")).to_numpy()
    assert X.loc[at, "min_since_last_tier2_pit"].item() == 0.0
    assert (X[["min_to_next_tier2_pit", "min_since_last_tier2_pit"]] <= fc.HORIZON_MIN).all().all()
    assert (X[["min_to_next_tier2_pit", "min_since_last_tier2_pit"]] >= 0).all().all()


def test_before_the_calendar_or_without_it_the_columns_are_nan_and_fixed(enabled):
    bars = _bars("2009-12-20", "2009-12-21")
    X = build_features(bars, enabled, {"us_calendar": _cal()})
    assert X[list(fc.COLUMNS)].isna().all().all()
    X = build_features(bars, enabled, {})
    assert list(X.columns.drop("ts_utc")) == list(fc.COLUMNS) and X.drop(columns=["ts_utc"]).isna().all().all()


def test_far_from_any_event_the_horizon_cap_applies(enabled):
    cal = _cal()
    cal = cal[cal["event_type"].isin(["ppi"])]
    bars = _bars("2024-01-20", "2024-01-21", "1h")
    X = build_features(bars, enabled, {"us_calendar": cal})
    assert (X["min_to_next_tier2_pit"] == fc.HORIZON_MIN).all()                 # Feb 16 PPI not yet known (7-day lead)
    assert (X["min_since_last_tier2_pit"] == fc.HORIZON_MIN).all()               # Jan 12 PPI more than 7 days ago


def test_a_seeded_late_event_is_never_seen_before_its_available_utc(enabled):
    """A rescheduled event first seen by a later download must not show before that download, and its cancelled
    predecessor stops showing only from then."""
    pub = _cal()
    t2 = _utc("2024-06-10 06:00")
    d = _fred_dates()
    d["ppi"] = _move(d["ppi"], "2024-06-13", "2024-06-12")
    out = uc.merge_calendar(pub, uc.calendar_frame(d, t2, _fomc()), t2, _ok(NOW))
    only_ppi = out[out["event_type"] == "ppi"]
    bars = _bars("2024-06-08", "2024-06-14")
    X = build_features(bars, enabled, {"us_calendar": only_ppi})
    ts = bars["ts_utc"]
    before = (ts < t2).to_numpy()
    expect_old = (_utc("2024-06-13 12:30") - ts).dt.total_seconds().to_numpy() / 60
    assert np.allclose(X.loc[before, "min_to_next_tier2_pit"], expect_old[before])          # still the old date
    after = ((ts >= t2) & (ts < _utc("2024-06-12 12:30"))).to_numpy()
    expect_new = (_utc("2024-06-12 12:30") - ts).dt.total_seconds().to_numpy() / 60
    assert np.allclose(X.loc[after, "min_to_next_tier2_pit"], expect_new[after])
    later = (ts > _utc("2024-06-12 12:30")).to_numpy()
    assert (X.loc[later, "min_to_next_tier2_pit"] == fc.HORIZON_MIN).all()                # the cancelled date is gone


def test_features_on_a_truncated_calendar_match_the_full_one_before_the_cut(enabled):
    pub = _cal()
    t2 = _utc("2024-06-10 06:00")
    d = _fred_dates()
    d["ppi"] = _move(d["ppi"], "2024-06-13", "2024-06-12")
    cal = uc.merge_calendar(pub, uc.calendar_frame(d, t2, _fomc()), t2, _ok(NOW))
    bars = _bars("2024-01-01", "2024-08-01", "1h")
    full = build_features(bars, enabled, {"us_calendar": cal})
    for cut in (_utc("2024-03-14 12:00"), _utc("2024-06-10 07:00"), _utc("2024-06-11 00:00")):
        part = build_features(bars, enabled, {"us_calendar": cal[cal["available_utc"] <= cut]})
        before = (bars["ts_utc"] < cut).to_numpy()
        for c in fc.COLUMNS:
            assert full.loc[before, c].notna().all(), c
            assert np.allclose(full.loc[before, c], part.loc[before, c], equal_nan=True), (c, cut)


def test_opt_in_leaves_the_default_feature_version_unchanged():
    from goldbot.features.registry import FEATURES, feature_version
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
    assert not set(fc.NAMES) & set(FEATURES)                  # importing the module registers nothing
    base = feature_version(DEFAULT_FEATURE_NAMES)
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        names = fc.enable()
        assert fc.enable() == names == ["tier1_events", "tier2_events"]
        assert not set(names) & set(DEFAULT_FEATURE_NAMES) and feature_version(DEFAULT_FEATURE_NAMES) == base
        assert feature_version([*DEFAULT_FEATURE_NAMES, *names]) != base
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
        registry.SIGNED.clear()
        registry.SIGNED.update(signed)


# --------------------------------------------------------------------------- builder script and workflow
def _script():
    spec = importlib.util.spec_from_file_location("us_calendar_data", ROOT / "scripts" / "us_calendar_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_fetch(rel, api_key):
    return _fred_dates().get(rel.event_type, pd.DatetimeIndex(["2024-01-10", "2024-02-10"]))


def test_script_needs_the_key_and_never_mistakes_a_missing_file_for_a_first_run(tmp_path, monkeypatch, capsys):
    mod = _script()
    monkeypatch.setattr(mod, "fetch_release", _fake_fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out, missing = tmp_path / "out.parquet", tmp_path / "prev" / uc.ASSET
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert mod.main(["--out", str(out), "--existing", str(missing), "--first-run"]) == 1 and not out.exists()
    monkeypatch.setenv("FRED_API_KEY", "k")
    assert mod.main(["--out", str(out), "--existing", str(missing)]) == 2
    assert not out.exists() and "REFUSED" in capsys.readouterr().out
    assert mod.main(["--out", str(out), "--existing", str(missing), "--first-run"]) == 0
    first, ok = uc.read_release(str(out))
    assert set(ok) == set(uc.EVENT_TYPES) and set(uc.EVENT_TYPES) == set(first["event_type"])
    assert mod.main(["--out", str(tmp_path / "again.parquet"), "--existing", str(out)]) == 0      # idempotent rerun
    again, _ = uc.read_release(str(tmp_path / "again.parquet"))
    uc.check_keeps_history(first, again)


def test_script_never_publishes_a_frame_that_loses_history(tmp_path, monkeypatch, capsys):
    mod = _script()
    prev = tmp_path / "prev.parquet"
    uc.write_release(_cal(), str(prev), {"cpi": NOW})
    monkeypatch.setattr(mod, "fetch_release", _fake_fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.setenv("FRED_API_KEY", "k")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(mod, "merge_calendar", lambda prev_df, fresh, now, last_ok, **kw: fresh.head(5))
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(prev)]) == 2
    assert not out.exists() and "REFUSED" in capsys.readouterr().out


def test_script_reports_a_failed_release_and_keeps_its_rows(tmp_path, monkeypatch):
    mod = _script()
    prev = tmp_path / "prev.parquet"
    pub = _cal()
    uc.write_release(pub, str(prev), _ok(NOW))

    def fetch(rel, api_key):
        if rel.event_type == "ppi":
            raise uc.FredError("HTTP 503")
        return _fake_fetch(rel, api_key)
    monkeypatch.setattr(mod, "fetch_release", fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.setenv("FRED_API_KEY", "k")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(prev)]) == 1
    merged, _ = uc.read_release(str(out))
    assert ((merged["event_type"] == "ppi") & (merged["status"] == "cancelled") & (merged["source"] != "manual")).sum() == 0
    uc.check_keeps_history(pub, merged)


def test_workflow_follows_the_data_workflow_pattern():
    wf = (ROOT / ".github" / "workflows" / "data-us-calendar.yml").read_text()
    assert re.search(r"^permissions: \{\}", wf, re.M)
    assert "persist-credentials: false" in wf
    for use in re.findall(r"uses: (\S+)", wf):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", use), use               # pinned to a commit
    assert "release not found" in wf and "--first-run" in wf and "steps.prev.outcome == 'success'" in wf
    assert "${{ secrets.FRED_API_KEY }}" in wf and "calendar-v1" in wf and uc.ASSET in wf
    assert "not endorsed or certified by the Federal Reserve Bank of St. Louis" in wf


def test_workflow_skips_cleanly_without_the_fred_key():
    # without the repo secret the Sunday run used to fail and open a confusing `ci` issue; now a key-check job (no
    # token scopes) reports the missing secret as a warning and the fetch job is skipped, never failed
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "data-us-calendar.yml").read_text())
    jobs = wf["jobs"]
    check = jobs["key-check"]
    assert check["permissions"] == {} and check["outputs"]["present"]
    step = check["steps"][0]
    assert step["env"]["FRED_API_KEY"] == "${{ secrets.FRED_API_KEY }}"
    assert "::warning::" in step["run"] and "GITHUB_STEP_SUMMARY" in step["run"] and "exit 1" not in step["run"]
    assert "echo \"$FRED_API_KEY\"" not in step["run"]                      # the key is tested, never printed
    fetch = jobs["fetch"]
    assert fetch["needs"] == "key-check" and fetch["if"] == "needs.key-check.outputs.present == 'true'"


# --------------------------------------------------------------------------- tier 1
def test_tier1_proximity_and_count_today(enabled):
    cal = _cal()
    bars = _bars("2024-03-18", "2024-03-22")
    X = build_features(bars, enabled, {"us_calendar": cal})
    ts = bars["ts_utc"]
    # 2024-03-20: FOMC statement 14:00 ET and press conference 14:30 ET (18:00 / 18:30 UTC, US DST already on)
    at = (ts == _utc("2024-03-20 17:00")).to_numpy()
    assert X.loc[at, "min_to_next_tier1_pit"].item() == 60.0
    assert X.loc[at, "tier1_count_today"].item() == 2.0
    between = (ts == _utc("2024-03-20 18:15")).to_numpy()
    assert X.loc[between, "min_to_next_tier1_pit"].item() == 15.0
    assert X.loc[between, "min_since_last_tier1_pit"].item() == 15.0
    # New York day boundaries: 03:45 UTC on the 20th is still the 19th in New York (no tier-1 event that day)
    assert X.loc[(ts == _utc("2024-03-20 03:45")).to_numpy(), "tier1_count_today"].item() == 0.0
    assert X.loc[(ts == _utc("2024-03-20 04:00")).to_numpy(), "tier1_count_today"].item() == 2.0
    assert X.loc[at, "tier2_count_today"].item() == 0.0                    # tiers are counted apart


def test_a_seeded_late_tier1_event_is_never_seen_before_its_available_utc(enabled):
    """A CPI date added by a later download (stamped at that download) is invisible before it, also in the count."""
    pub = _cal()
    t2 = _utc("2024-06-26 06:00")
    d = _fred_dates()
    d["cpi"] = _move(d["cpi"], "2024-07-11", "2024-06-27")
    out = uc.merge_calendar(pub, uc.calendar_frame(d, t2, _fomc()), t2, _ok(NOW))
    assert out.loc[out["event_id"] == "cpi:2024-06-27", "available_utc"].iloc[0] == t2
    bars = _bars("2024-06-26", "2024-06-28")
    X = build_features(bars, enabled, {"us_calendar": out})
    ts = bars["ts_utc"]
    before = (ts < t2).to_numpy()
    assert (X.loc[before, "tier1_count_today"] == 0.0).all()               # the 26th in New York: nothing known
    to_cpi = (_utc("2024-06-27 12:30") - ts).dt.total_seconds().to_numpy() / 60
    assert (X.loc[before, "min_to_next_tier1_pit"].to_numpy() > to_cpi[before]).all()
    after = ((ts >= t2) & (ts < _utc("2024-06-27 12:30"))).to_numpy()
    assert np.allclose(X.loc[after, "min_to_next_tier1_pit"], to_cpi[after])
    on_day = ((ts >= _utc("2024-06-27 04:00")) & (ts < _utc("2024-06-28 00:00"))).to_numpy()
    assert (X.loc[on_day, "tier1_count_today"] == 1.0).all()


def test_research_pass_adds_the_calendar_features_only_with_the_flag(enabled, tmp_path):
    from goldbot.research.pipeline import research_feature_names
    spec = importlib.util.spec_from_file_location("research_pass", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.feature_ctx(None, None, None) == (None, None)
    assert mod.load_us_calendar("")[1]["used"] is False
    assert mod.load_us_calendar(str(tmp_path / "none"))[1]["used"] is False
    uc.write_release(_cal(), str(tmp_path / uc.ASSET), {"cpi": NOW})
    cal, info = mod.load_us_calendar(str(tmp_path))
    assert info["used"] is True and cal is not None and "cpi" in info["event_types"]
    ctx, names = mod.feature_ctx(None, None, cal)
    assert ctx is not None and ctx["us_calendar"] is cal and names is None      # ctx only, for day_type (review M7)
    ctx, names = mod.feature_ctx(None, None, cal, calendar_features=True)
    assert ctx is not None and names == [*research_feature_names(ctx), "tier1_events", "tier2_events"]


@pytest.mark.integration
def test_research_pass_calendar_frame_passes_the_lookahead_check(enabled):
    """The pipeline's own leakage check (history and the calendar cut at 70%) on the research frame."""
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import lookahead_check
    bars = resample_bars(ticks_to_1m(synthetic_ticks("2024-03-04", "2024-04-26", ticks_per_minute=2, seed=7)),
                         "1h").reset_index(drop=True)
    out = lookahead_check(bars, None, feature_names=enabled, ctx={"us_calendar": _cal()})
    assert out["lookahead_columns"] == [] and out["columns_checked"] == len(fc.COLUMNS)


# --------------------------------------------------------------------------- review fixes (H1, M1-M5, GDP, L1-L5)
def test_a_type_that_failed_on_the_first_run_is_backfilled_with_rule_stamps(tmp_path, monkeypatch):
    """H1: CPI fails on the first run and comes back on the second: its history keeps event - 7 days, it is not
    stamped at the second download (it was never downloaded before, so nothing was missing from an earlier run)."""
    mod = _script()
    calls = {"n": 0}

    def fetch(rel, api_key):
        if rel.event_type == "cpi" and calls["n"] == 0:
            raise uc.FredError("HTTP 503")
        return _fake_fetch(rel, api_key)
    monkeypatch.setattr(mod, "fetch_release", fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.setenv("FRED_API_KEY", "k")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    first, second = tmp_path / "1.parquet", tmp_path / "2.parquet"
    assert mod.main(["--out", str(first), "--existing", str(tmp_path / "none"), "--first-run"]) == 1
    one, ok1 = uc.read_release(str(first))
    assert "cpi" not in ok1 and "ppi" in ok1
    assert set(one.loc[one["event_type"] == "cpi", "source"]) <= {"manual"}            # only the hand-checked rows
    calls["n"] = 1
    assert mod.main(["--out", str(second), "--existing", str(first)]) == 0
    two, ok2 = uc.read_release(str(second))
    cpi = two[(two["event_type"] == "cpi") & (two["source"] == "fred")]
    assert len(cpi) == 7 and "cpi" in ok2
    lead = pd.DatetimeIndex(cpi["event_utc"]) - pd.DatetimeIndex(cpi["available_utc"])
    assert (lead == uc.SCHEDULE_LEAD).all()
    uc.check_keeps_history(one, two)


def test_last_ok_is_per_event_type_in_the_merge():
    pub = _cal()
    t2 = NOW + pd.Timedelta(days=7)
    d = _fred_dates()
    d["ppi"] = _move(d["ppi"], "2024-06-13", "2024-06-14")
    d["cpi"] = _move(d["cpi"], "2024-06-12", "2024-06-11")
    ok = {e: NOW for e in uc.EVENT_TYPES if e != "cpi"}                    # cpi never downloaded: a backfill
    out = uc.merge_calendar(pub, uc.calendar_frame(d, t2, _fomc()), t2, ok)
    assert out.loc[out["event_id"] == "ppi:2024-06-14", "available_utc"].iloc[0] == t2
    cpi = out.loc[out["event_id"] == "cpi:2024-06-11"]
    assert cpi["available_utc"].iloc[0] == cpi["event_utc"].iloc[0] - uc.SCHEDULE_LEAD


def test_a_run_that_would_cancel_many_future_events_of_a_type_is_refused():
    """M1: a truncated download (a partial parse) must not cancel the future schedule."""
    pub = _cal(_utc("2024-01-01 06:00"))
    t2 = _utc("2024-01-08 06:00")
    d = _fred_dates()
    d["jobless_claims"] = d["jobless_claims"][d["jobless_claims"] < pd.Timestamp("2024-02-01")]
    fresh = uc.calendar_frame(d, t2, _fomc())
    assert uc.mass_cancellations(pub, fresh, t2) == {"jobless_claims": 48}
    with pytest.raises(uc.MassCancellation, match="jobless_claims"):
        uc.merge_calendar(pub, fresh, t2, _ok(t2))
    d2 = _fred_dates()
    d2["ppi"] = _move(_move(d2["ppi"], "2024-05-14", "2024-05-15"), "2024-06-13", "2024-06-14")
    assert uc.mass_cancellations(pub, uc.calendar_frame(d2, t2, _fomc()), t2) == {}      # two moves are fine


def test_script_refuses_a_type_that_would_mass_cancel_and_keeps_its_rows(tmp_path, monkeypatch):
    mod = _script()
    prev = tmp_path / "prev.parquet"
    pub = _cal(_utc("2024-01-01 06:00"))
    uc.write_release(pub, str(prev), _ok(_utc("2024-01-01 06:00")))

    def fetch(rel, api_key):
        d = _fake_fetch(rel, api_key)
        return d[d < pd.Timestamp("2024-02-01")] if rel.event_type == "jobless_claims" else d
    monkeypatch.setattr(mod, "fetch_release", fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.setenv("FRED_API_KEY", "k")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(mod.pd.Timestamp, "now", classmethod(lambda cls, tz=None: _utc("2024-01-08 06:00")))
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(prev)]) == 1
    merged, ok = uc.read_release(str(out))
    assert ((merged["event_type"] == "jobless_claims") & (merged["status"] == "cancelled")).sum() == 0
    assert ok["jobless_claims"] == _utc("2024-01-01 06:00")                # not counted as a successful download
    uc.check_keeps_history(pub, merged)


def test_hand_checked_cancellations_are_in_the_calendar():
    """M2: scheduled-then-cancelled events that the downloads cannot show (they list what happened)."""
    cal = _cal()
    fomc = cal[cal["event_id"] == "fomc_statement:2020-03-18"].sort_values("available_utc")
    assert list(fomc["status"]) == ["scheduled", "cancelled"]
    ev = pd.Timestamp("2020-03-18 14:00", tz=uc.ET).tz_convert("UTC")
    assert fomc["available_utc"].iloc[0] == ev - uc.SCHEDULE_LEAD
    assert fomc["available_utc"].iloc[1] == pd.Timestamp("2020-03-15 17:00", tz=uc.ET).tz_convert("UTC")
    assert (cal["event_id"] == "fomc_press_conference:2020-03-18").sum() == 2
    nfp13 = cal[cal["event_id"] == "nfp:2013-10-04"].sort_values("available_utc")
    assert list(nfp13["status"]) == ["scheduled", "cancelled"]
    assert nfp13["available_utc"].iloc[1] == pd.Timestamp("2013-10-01", tz=uc.ET).tz_convert("UTC")
    for eid in ("cpi:2013-10-16", "ppi:2013-10-11", "jolts:2013-10-08", "gdp_advance:2019-01-30",
                "nfp:2025-10-03", "ppi:2025-10-16", "cpi:2025-11-13", "nfp:2025-11-07", "nfp:2026-02-06"):
        rows = cal[cal["event_id"] == eid].sort_values("available_utc")
        assert list(rows["status"]) == ["scheduled", "cancelled"], eid
        assert rows["available_utc"].iloc[0] < rows["available_utc"].iloc[1] <= rows["event_utc"].iloc[0], eid
    assert set(cal.loc[cal["source"] == "manual", "event_type"]) <= set(uc.EVENT_TYPES)


def test_a_cancelled_meeting_shows_until_its_cancellation_and_never_as_a_past_event(enabled):
    cal = _cal()
    bars = _bars("2020-03-12", "2020-03-20")
    X = build_features(bars, enabled, {"us_calendar": cal})
    ts = bars["ts_utc"]
    cancel = pd.Timestamp("2020-03-15 17:00", tz=uc.ET).tz_convert("UTC")
    ev = pd.Timestamp("2020-03-18 14:00", tz=uc.ET).tz_convert("UTC")
    before = ((ts >= ev - uc.SCHEDULE_LEAD) & (ts < cancel)).to_numpy()
    to_ev = (ev - ts).dt.total_seconds().to_numpy() / 60
    assert np.allclose(X.loc[before, "min_to_next_tier1_pit"], to_ev[before])
    after = (ts >= cancel).to_numpy()
    assert (X.loc[after, "min_to_next_tier1_pit"] == fc.HORIZON_MIN).all()
    on_day = ((ts >= _utc("2020-03-18 04:00")) & (ts < _utc("2020-03-19 04:00"))).to_numpy()
    assert (X.loc[on_day, "tier1_count_today"] == 0).all()
    assert (X.loc[on_day & (ts > ev).to_numpy(), "min_since_last_tier1_pit"] == fc.HORIZON_MIN).all()


def test_shutdown_floors_are_per_agency():
    """M5: 2018-19 closed Commerce (Census, BEA) only; BLS and the private ISM kept their schedule."""
    ev = uc.et_to_utc(pd.DatetimeIndex(["2019-01-16"]), time(8, 30))
    floor = pd.Timestamp("2019-02-15", tz=uc.ET).tz_convert("UTC")
    assert uc.rule_available_utc(ev, "census")[0] == ev[0]                  # floor is after the event
    assert uc.rule_available_utc(ev, "bea")[0] == ev[0]
    assert uc.rule_available_utc(ev, "bls")[0] == ev[0] - uc.SCHEDULE_LEAD
    ev2 = uc.et_to_utc(pd.DatetimeIndex(["2019-02-20"]), time(8, 30))
    assert uc.rule_available_utc(ev2, "census")[0] == floor
    for agency in ("bls", "dol", "census", "bea"):                          # 2013 and 2025 closed everything
        assert uc.rule_available_utc(uc.et_to_utc(pd.DatetimeIndex(["2025-11-20"]), time(8, 30)), agency)[0] > \
            uc.et_to_utc(pd.DatetimeIndex(["2025-11-20"]), time(8, 30))[0] - uc.SCHEDULE_LEAD
    ism = uc.rule_rows(_utc("2025-12-31"), NOW)
    ism = ism[ism["event_id"] == "ism_manufacturing:2025-11-03"]
    assert (ism["event_utc"] - ism["available_utc"]).iloc[0] == uc.SCHEDULE_LEAD
    assert uc.AGENCY["fomc_statement"] == "fed" and uc.AGENCY["retail_sales"] == "census"


def test_gdp_advance_is_the_first_gdp_release_after_each_quarter():
    cal = _cal()
    adv = set(cal.loc[(cal["event_type"] == "gdp_advance") & (cal["source"] == "fred"), "event_id"])
    assert adv == {"gdp_advance:2024-01-25", "gdp_advance:2024-04-25", "gdp_advance:2024-07-25"}
    later = set(cal.loc[cal["event_type"] == "gdp", "event_id"])
    assert "gdp:2024-02-28" in later and not {e.replace("gdp_advance", "gdp") for e in adv} & later
    assert "gdp:2023-12-21" in later


def test_count_table_and_monthly_spacing_quarantine():
    """M3: per-type, per-year counts against the expected ranges; monthly releases < 20 days apart are quarantined
    (kept out and reported) unless they are on the hand-checked exemption list."""
    d = _fred_dates()
    d["cpi"] = pd.DatetimeIndex([*d["cpi"], pd.Timestamp("2024-02-20")]).sort_values()  # 7 days after Feb 13
    keep, quarantined = uc.quarantine_spacing(uc.calendar_frame(d, NOW, _fomc()))
    assert list(quarantined["event_id"]) == ["cpi:2024-02-13", "cpi:2024-02-20"]   # both of the close pair
    assert "cpi:2024-02-20" not in set(keep["event_id"])
    assert "nfp:2013-11-08" in uc.SPACING_EXEMPT                            # 17 days after Oct 22, 2013 (shutdown)
    full = pd.DataFrame({"event_id": [f"cpi:2015-{m:02d}-14" for m in range(1, 13)], "event_type": "cpi",
                         "status": "scheduled", "event_utc": uc.et_to_utc([f"2015-{m:02d}-14" for m in range(1, 13)],
                                                                          time(8, 30))})
    t = uc.count_table(full)
    assert t.loc[(t["event_type"] == "cpi") & (t["year"] == 2015), "ok"].item()
    t = uc.count_table(full.iloc[:5])
    assert not t.loc[(t["event_type"] == "cpi") & (t["year"] == 2015), "ok"].item()
    assert uc.expected_count("jobless_claims", 2015) == (51, 53)
    assert uc.expected_count("fomc_press_conference", 2011) == (3, 3)       # verified on the Fed's 2011 page
    assert uc.expected_count("fomc_press_conference", 2012) == (5, 5)       # verified on the Fed's 2012 page
    assert uc.expected_count("fomc_press_conference", 2015) == (4, 4)
    assert uc.expected_count("fomc_press_conference", 2020) == (7, 8)


def test_script_writes_the_count_table_to_the_step_summary(tmp_path, monkeypatch):
    mod = _script()
    monkeypatch.setattr(mod, "fetch_release", _fake_fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.setenv("FRED_API_KEY", "k")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert mod.main(["--out", str(tmp_path / "o.parquet"), "--existing", str(tmp_path / "n"), "--first-run"]) == 0
    text = summary.read_text()
    assert "| event_type | year | n | expected | ok |" in text and "| cpi | 2024 |" in text


def test_press_conference_links_are_found_whatever_the_case_or_spelling():
    """M4: the Fed's links vary (fomcpresconf, FOMCpresconf, fomcpressconf in January 2026)."""
    for link in ("fomcpresconf20250129", "FOMCpresconf20250129", "fomcpressconf20260128"):
        html = CURRENT_HTML.replace("fomcpresconf20250129", link)
        assert uc.parse_fomc_current(html)["press"].iloc[0], link


def test_minutes_date_comes_from_the_minutes_block_and_a_plausible_gap():
    """L1: a "Released" date elsewhere in the block (a statement, a transcript) is not the minutes date, and a gap
    outside 14-28 days falls back to statement + 21 days."""
    block = '<strong>Minutes:</strong><br> (Released February 19, 2025)'
    html = CURRENT_HTML.replace(block, '<a>Transcript</a> (Released March 01, 2025) ' + block)
    assert uc.parse_fomc_current(html)["minutes_day"].iloc[0] == pd.Timestamp("2025-02-19")
    no_minutes = CURRENT_HTML.replace(block, '<a>Transcript</a> (Released March 01, 2025)')
    assert pd.isna(uc.parse_fomc_current(no_minutes)["minutes_day"].iloc[0])
    meetings = pd.DataFrame({"day": pd.DatetimeIndex(["2024-01-31", "2024-03-20"]), "press": True,
                             "minutes_day": pd.DatetimeIndex(["2024-06-01", "2024-04-10"])})
    ids = set(uc.fomc_rows(meetings, NOW)["event_id"])
    assert "fomc_minutes:2024-02-21" in ids and "fomc_minutes:2024-06-01" not in ids and "fomc_minutes:2024-04-10" in ids


def test_rule_rows_can_be_overridden_or_excluded(tmp_path, monkeypatch):
    """L2: ISM is rule-derived; a hand-checked override moves or drops a date, and research can leave rule rows out
    (the report line says how many rows are rule-derived)."""
    monkeypatch.setitem(uc.RULE_OVERRIDES, "ism_services:2024-01-04", "2024-01-05")
    monkeypatch.setitem(uc.RULE_OVERRIDES, "ism_manufacturing:2024-01-02", None)
    rows = uc.rule_rows(NOW, NOW)
    ids = set(rows["event_id"])
    assert "ism_services:2024-01-05" in ids and "ism_services:2024-01-04" not in ids
    assert "ism_manufacturing:2024-01-02" not in ids
    spec = importlib.util.spec_from_file_location("research_pass", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uc.write_release(_cal(), str(tmp_path / uc.ASSET), {})
    cal, info = mod.load_us_calendar(str(tmp_path))
    assert cal is not None and "rule-derived" in info["detail"] and (cal["source"] == "rule").any()
    cal2, info2 = mod.load_us_calendar(str(tmp_path), exclude_rule=True)
    assert cal2 is not None and not (cal2["source"] == "rule").any() and "rule rows excluded" in info2["detail"]


def test_tier2_columns_carry_the_pit_suffix():
    """L4: no tier-2 column can clash with a live-feed calendar column."""
    assert fc.TIER2_COLUMNS == ("min_to_next_tier2_pit", "min_since_last_tier2_pit", "tier2_count_today")


def test_retail_sales_name_check_is_specific():
    """L5: "Retail" alone would match other retail releases."""
    rel = next(r for r in uc.RELEASES if r.event_type == "retail_sales")
    assert rel.name == "Advance Monthly Sales for Retail"
    with pytest.raises(uc.FredError, match="expected a name"):
        uc.fetch_release(rel, "k", session=cast(Any, _Session("Monthly Retail Trade Report", [])), sleep=lambda _: None)


# --------------------------------------------------------------------------- re-verification fixes (M-R1, 2026, L-R1-3)
def test_a_phantom_listed_before_the_real_release_is_quarantined_with_it():
    """M-R1: both rows of a close pair are held back until one is hand-checked, so a phantom that comes first (e.g. a
    seasonal-factor revision day) is never kept in place of the real release."""
    d = _fred_dates()
    d["cpi"] = pd.DatetimeIndex([*d["cpi"], pd.Timestamp("2024-02-06")]).sort_values()     # phantom 7 days before
    keep, quarantined = uc.quarantine_spacing(uc.calendar_frame(d, NOW, _fomc()))
    assert set(quarantined["event_id"]) == {"cpi:2024-02-06", "cpi:2024-02-13"}
    assert not {"cpi:2024-02-06", "cpi:2024-02-13"} & set(keep["event_id"])


def test_a_hand_checked_row_of_a_close_pair_is_kept_and_its_partner_still_held(monkeypatch):
    d = _fred_dates()
    d["cpi"] = pd.DatetimeIndex([*d["cpi"], pd.Timestamp("2024-02-06")]).sort_values()
    monkeypatch.setattr(uc, "SPACING_EXEMPT", uc.SPACING_EXEMPT | {"cpi:2024-02-13"})
    keep, quarantined = uc.quarantine_spacing(uc.calendar_frame(d, NOW, _fomc()))
    assert list(quarantined["event_id"]) == ["cpi:2024-02-06"] and "cpi:2024-02-13" in set(keep["event_id"])
    assert {"nfp:2013-10-22", "nfp:2013-11-08"} <= uc.SPACING_EXEMPT        # both real (2013 shutdown)


def test_the_2026_lapse_floor_is_bls_only_and_its_rows_cancel_just_before_the_event():
    ev = uc.et_to_utc(pd.DatetimeIndex(["2026-02-10"]), time(8, 30))
    floor = pd.Timestamp("2026-02-13", tz=uc.ET).tz_convert("UTC")
    assert uc.rule_available_utc(ev, "bls")[0] == ev[0]                     # floor after the event
    for agency in ("dol", "census", "bea"):
        assert uc.rule_available_utc(ev, agency)[0] == ev[0] - uc.SCHEDULE_LEAD, agency
    assert uc.rule_available_utc(uc.et_to_utc(["2026-02-18"], time(8, 30)), "bls")[0] == floor
    cal = _cal()
    for eid, at in (("jolts:2026-02-03", "2026-02-03 09:59"), ("nfp:2026-02-06", "2026-02-06 08:29"),
                    ("cpi:2026-02-11", "2026-02-11 08:29")):
        c = cal[(cal["event_id"] == eid) & (cal["status"] == "cancelled")]
        assert c["available_utc"].iloc[0] == pd.Timestamp(at, tz=uc.ET).tz_convert("UTC"), eid


def test_allow_cancellations_lets_a_verified_type_through_and_is_audited(tmp_path, monkeypatch):
    """L-R1: after verifying a real schedule change, a one-shot --allow-cancellations TYPE applies it."""
    mod = _script()
    prev = tmp_path / "prev.parquet"
    pub = _cal(_utc("2024-01-01 06:00"))
    uc.write_release(pub, str(prev), _ok(_utc("2024-01-01 06:00")))

    def fetch(rel, api_key):
        d = _fake_fetch(rel, api_key)
        return d[d < pd.Timestamp("2024-02-01")] if rel.event_type == "jobless_claims" else d
    monkeypatch.setattr(mod, "fetch_release", fetch)
    monkeypatch.setattr(mod, "fetch_fomc", _fomc)
    monkeypatch.setenv("FRED_API_KEY", "k")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(mod.pd.Timestamp, "now", classmethod(lambda cls, tz=None: _utc("2024-01-08 06:00")))
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(prev), "--allow-cancellations", "jobless_claims"]) == 0
    merged, _ = uc.read_release(str(out))
    assert ((merged["event_type"] == "jobless_claims") & (merged["status"] == "cancelled")).sum() == 48
    assert "AUDIT" in summary.read_text() and "jobless_claims" in summary.read_text()
    with pytest.raises(SystemExit):
        mod.main(["--out", str(out), "--existing", str(prev), "--allow-cancellations", "not_a_type"])
    assert "--allow-cancellations" in (ROOT / "docs" / "RUNBOOK.md").read_text()


def test_late_gdp_advances_are_flagged_and_overrides_win():
    """L-R2: an advance more than 45 days after the quarter end is flagged unless hand-checked; the override list
    names the real advance (Q4 2025: 2026-02-20 per BEA, not the 2026-01-22 Q3 update)."""
    dates = pd.DatetimeIndex(["2025-10-01", "2025-12-23", "2026-01-22", "2026-02-20", "2026-03-13", "2026-04-29"])
    adv, later = uc.split_gdp(dates)
    assert pd.Timestamp("2026-02-20") in adv and pd.Timestamp("2026-01-22") in later
    assert pd.Timestamp("2025-12-23") in adv                                   # Q3 2025 initial estimate (BEA)
    assert uc.GDP_ADVANCE_OVERRIDES["2025-12-31"] == "2026-02-20"
    assert uc.late_gdp_advances(dates) == []                                   # both late ones are hand-checked
    flagged = uc.late_gdp_advances(pd.DatetimeIndex(["2023-12-21", "2024-03-01", "2024-04-25"]))
    assert flagged == ["quarter 2023-12-31: first GDP release 2024-03-01 is 61 days after the quarter end"]


def test_old_source_keyed_last_ok_is_rejected(tmp_path):
    """L-R3: metadata keyed by source (fred/fed/rule) would be misread as per-type; refuse it."""
    p = tmp_path / uc.ASSET
    uc.write_release(_cal(), str(p), {"fred": NOW, "rule": NOW})
    with pytest.raises(ValueError, match="last_ok"):
        uc.read_release(str(p))
    with pytest.raises(ValueError, match="last_ok"):
        uc.merge_calendar(_cal(), uc.calendar_frame(_fred_dates(), NOW, _fomc()), NOW, {"fred": NOW})

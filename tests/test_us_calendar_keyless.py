"""Keyless release dates for calendar-v1 (ADR 0044): parsers on saved snippets of the real ALFRED and FRED calendar
pages (tests/fixtures/us_calendar, fetched 2026-10-25), Central -> New York time conversion across DST, the fetch
guards, and the frame the workflow builds (schema, DST-correct event_utc, available_utc, counts per year)."""
from datetime import time
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest

from goldbot.data import us_calendar as uc
from goldbot.data import us_calendar_keyless as kl

FIX = Path(__file__).resolve().parent / "fixtures" / "us_calendar"
REL = {r.event_type: r for r in uc.RELEASES}
NOW = pd.Timestamp("2026-10-25 06:00", tz="UTC")


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------- parsers
def test_alfred_parser_reads_the_release_dates_block_only():
    d = kl.parse_alfred_dates(_text("alfred_rid50.txt"), REL["nfp"])
    assert d.is_monotonic_increasing and d.is_unique
    assert pd.Timestamp("2015-02-06") in d and pd.Timestamp("2014-10-03") in d
    assert pd.Timestamp("2026-10-02") not in d          # header "Period End" dates are not release dates
    assert d.min() == pd.Timestamp("1955-05-06")


def test_alfred_parser_refuses_a_wrong_release_or_an_error_page():
    with pytest.raises(uc.FredError, match="expected a name containing"):
        kl.parse_alfred_dates(_text("alfred_rid50.txt"), REL["cpi"])
    with pytest.raises(uc.FredError, match="not a release-dates file"):
        kl.parse_alfred_dates("<html>Service unavailable</html>", REL["nfp"])
    with pytest.raises(uc.FredError, match="no dates"):
        kl.parse_alfred_dates("Release: Employment Situation\nRelease Dates:\n----------\n", REL["nfp"])


def test_fred_calendar_parser_reads_days_and_converts_central_to_new_york():
    cal = kl.parse_fred_calendar(_text("fred_calendar_rid54_2026.html"), REL["pce"], 2026)
    assert len(cal) == 13 and cal["day"].is_monotonic_increasing
    by_day = dict(zip(cal["day"], cal["time_et"]))
    assert by_day[pd.Timestamp("2026-02-20")] == time(8, 30)        # 7:30 am CT, winter
    assert by_day[pd.Timestamp("2026-06-25")] == time(8, 30)        # 7:30 am CT, summer (both zones on DST)
    assert by_day[pd.Timestamp("2026-01-22")] == time(10, 0)        # 9:00 am CT: the 10:00 ET release (ADR 0008)
    assert pd.Timestamp("2026-12-23") in by_day                     # scheduled future dates are listed


def test_fred_calendar_parser_refuses_a_fallback_year_and_a_wrong_release():
    with pytest.raises(uc.FredError, match="year not offered"):
        kl.parse_fred_calendar(_text("fred_calendar_rid50_fallback.html"), REL["nfp"], 2015)
    with pytest.raises(uc.FredError, match="expected a name containing"):
        kl.parse_fred_calendar(_text("fred_calendar_rid54_2026.html"), REL["cpi"], 2026)
    with pytest.raises(uc.FredError, match="no calendar title"):
        kl.parse_fred_calendar("<html>oops</html>", REL["nfp"], 2026)
    assert kl.parse_fred_calendar(_text("fred_calendar_rid50_2027_empty.html"), REL["nfp"], 2027).empty


@pytest.mark.parametrize(("day", "clock", "et"), [
    ("2026-03-06", "7:30 am", time(8, 30)),     # day before the US spring-forward Sunday (2026-03-08)
    ("2026-03-09", "7:30 am", time(8, 30)),     # day after: both zones moved together
    ("2026-11-02", "9:00 am", time(10, 0)),     # after fall-back
    ("2026-07-02", "12:30 pm", time(13, 30)),
])
def test_central_to_new_york_is_one_hour_on_every_side_of_dst(day: str, clock: str, et: time):
    assert kl.ct_to_et(pd.Timestamp(day), clock) == et


def test_event_utc_is_dst_correct_for_keyless_dates():
    ev = uc.et_to_utc(pd.DatetimeIndex(["2026-03-06", "2026-03-13", "2026-11-06"]), time(8, 30))
    assert list(ev) == [pd.Timestamp("2026-03-06 13:30", tz="UTC"), pd.Timestamp("2026-03-13 12:30", tz="UTC"),
                        pd.Timestamp("2026-11-06 13:30", tz="UTC")]


# ------------------------------------------------------------------------------------------------- fetch
class _Resp:
    def __init__(self, text: str, status: int = 200):
        self.text, self.status_code, self.encoding = text, status, "utf-8"

    def iter_content(self, chunk_size: int = 1):
        yield self.text.encode()

    def close(self) -> None:
        pass


class _Session:
    """Serves ALFRED and FRED calendar pages by URL; records every URL asked for."""

    def __init__(self, pages: dict[str, str]):
        self.pages: dict[str, str] = pages
        self.urls: list[str] = []

    def get(self, url: str, **_: Any) -> _Resp:
        self.urls.append(url)
        return _Resp(self.pages[url]) if url in self.pages else _Resp("not found", 404)


def _alfred_pce(dates: list[str]) -> str:
    return "Release: Personal Income and Outlays\n\nRelease Dates:\n----------\n" + "\n".join(dates) + "\n"


def _pages(alfred: str, cal_2026: str | None = None, cal_2027: str | None = None) -> dict[str, str]:
    rid = REL["pce"].release_id
    return {kl.ALFRED_DATES_URL.format(rid=rid): alfred,
            kl.FRED_CALENDAR_URL.format(rid=rid, year=2026): cal_2026 or _text("fred_calendar_rid54_2026.html"),
            kl.FRED_CALENDAR_URL.format(rid=rid, year=2027): cal_2027 or _text("fred_calendar_rid50_fallback.html")}


PCE_PAST = ["2009-12-23", "2015-01-30", "2025-12-23", "2026-01-22", "2026-02-20", "2026-03-13", "2026-04-09",
            "2026-04-30", "2026-05-28", "2026-06-25", "2026-07-30", "2026-08-26", "2026-09-30"]


def test_fetch_unions_history_and_schedule_and_spaces_requests():
    s, waits = _Session(_pages(_alfred_pce(PCE_PAST))), cast(list[float], [])
    out = kl.fetch_release_keyless(REL["pce"], NOW, session=s, sleep=waits.append)  # type: ignore[arg-type]
    assert out.dates.min() == pd.Timestamp("2015-01-30")             # before COVERAGE_START dropped
    assert pd.Timestamp("2026-12-23") in out.dates                   # scheduled, from the calendar only
    assert len(s.urls) == 3 and all(w >= kl.CRAWL_DELAY for w in waits)
    assert any("2027 calendar not offered yet" in n for n in out.notes)
    assert not any(n.startswith("TIME FLAG") for n in out.notes)        # the listed odd times are in TIME_OVERRIDES


def test_fetch_reports_past_dates_the_two_pages_disagree_on():
    past = [d for d in PCE_PAST if d != "2026-04-30"] + ["2026-05-01"]
    out = kl.fetch_release_keyless(REL["pce"], NOW, session=_Session(_pages(_alfred_pce(past))),  # type: ignore[arg-type]
                                   sleep=lambda _: None)
    note = next(n for n in out.notes if "disagree" in n)
    assert "calendar only ['2026-04-30']" in note and "ALFRED only ['2026-05-01']" in note
    assert {pd.Timestamp("2026-04-30"), pd.Timestamp("2026-05-01")} <= set(out.dates)


def test_fetch_refuses_a_stale_alfred_file_an_http_error_or_an_empty_current_year():
    stale = _alfred_pce(["2015-01-30", "2026-05-28"])
    with pytest.raises(uc.FredError, match="days old"):
        kl.fetch_release_keyless(REL["pce"], NOW, session=_Session(_pages(stale)), sleep=lambda _: None)  # type: ignore[arg-type]
    with pytest.raises(uc.FredError, match="HTTP 404"):
        kl.fetch_release_keyless(REL["pce"], NOW, session=_Session({}), retries=2, sleep=lambda _: None)  # type: ignore[arg-type]
    empty = _text("fred_calendar_rid50_2027_empty.html").replace("2027", "2026").replace(
        "Employment Situation", "Personal Income and Outlays")
    with pytest.raises(uc.FredError, match="no dates listed for the current year"):
        kl.fetch_release_keyless(REL["pce"], NOW, session=_Session(_pages(_alfred_pce(PCE_PAST), cal_2026=empty)),  # type: ignore[arg-type]
                                 sleep=lambda _: None)


# ------------------------------------------------------------------------------------------------- the frame
def _monthly(first: str, n: int, day: int) -> pd.DatetimeIndex:
    """n monthly dates on `day` from `first`'s month, moved to the next weekday."""
    out = [pd.Timestamp(m) + pd.Timedelta(days=day - 1) for m in pd.date_range(first, periods=n, freq="MS")]
    return pd.DatetimeIndex([d + pd.offsets.BDay(0) for d in out])


def test_keyless_dates_build_the_calendar_v1_schema_with_expected_counts_and_rule_stamps():
    dates = {"nfp": _monthly("2015-01-01", 12 * 11, 5), "cpi": _monthly("2015-01-01", 12 * 11, 22),
             "pce": _monthly("2015-01-01", 12 * 11, 27)}
    fomc = pd.DataFrame({"day": pd.DatetimeIndex([f"{y}-{m:02d}-15" for y in range(2015, 2026)
                                                  for m in (1, 3, 4, 6, 7, 9, 10, 12)]) + pd.offsets.BDay(0),
                         "press": False, "minutes_day": pd.NaT})
    cal, quarantined = uc.quarantine_spacing(uc.calendar_frame(dates, NOW, fomc))
    assert quarantined.empty and list(cal.columns) == list(uc.COLUMNS)
    assert cal["event_utc"].dt.tz is not None and str(cal["event_utc"].dt.unit) == "ns"
    assert set(cal.loc[cal["event_type"].isin(dates), "source"]) == {"fred", "manual"}   # + hand-checked lapses
    assert (cal["available_utc"] <= cal["event_utc"]).all()
    fred = cal[(cal["source"] == "fred") & (cal["event_type"] == "nfp")
               & (cal["event_utc"] < pd.Timestamp("2025-09-01", tz="UTC"))]
    assert ((fred["event_utc"] - fred["available_utc"]) == uc.SCHEDULE_LEAD).all()
    counts = uc.count_table(cal, through_year=2025)
    for t in ("nfp", "cpi", "pce", "fomc_statement"):
        c = counts[(counts["event_type"] == t) & counts["year"].between(2015, 2024)
                   & ~counts["year"].isin([y for (k, y) in uc.MONTHLY_COUNT_EXCEPTIONS if k == t]
                                          + ([2020] if t == "fomc_statement" else []))]   # synthetic: no cancellation
        assert len(c) >= 9 and c["ok"].all(), (t, c)
    # one NFP in summer and one in winter: 08:30 New York is 12:30 / 13:30 UTC
    nfp = cal[cal["event_type"] == "nfp"].set_index("event_id")["event_utc"]
    assert nfp["nfp:2015-07-06"].hour == 12 and nfp["nfp:2015-01-05"].hour == 13


# ------------------------------------------------------------------------------------------------- the script
def _script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("us_calendar_data", FIX.parents[2] / "scripts" / "us_calendar_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _no_fomc() -> pd.DataFrame:
    days = pd.DatetimeIndex([f"2025-{m:02d}-17" for m in (1, 3, 5, 6, 7, 9, 10, 12)])
    return pd.DataFrame({"day": days, "press": True, "minutes_day": pd.NaT})


@pytest.mark.parametrize(("key", "source", "used"), [("", "auto", "keyless"), ("k", "auto", "api"),
                                                     ("k", "keyless", "keyless")])
def test_script_uses_the_keyless_source_without_the_key(tmp_path, monkeypatch, capsys, key: str, source: str,
                                                        used: str):
    mod = _script()
    calls: list[str] = []

    def keyless(rel: uc.Release, now: pd.Timestamp) -> kl.KeylessDates:
        calls.append("keyless")
        return kl.KeylessDates(pd.DatetimeIndex(["2025-01-10", "2025-02-07"]), pd.DataFrame(), [f"{rel.event_type} n"])

    def api(rel: uc.Release, api_key: str) -> pd.DatetimeIndex:
        calls.append("api")
        return pd.DatetimeIndex(["2025-01-10", "2025-02-07"])
    monkeypatch.setattr(mod, "fetch_release_keyless", keyless)
    monkeypatch.setattr(mod, "fetch_release", api)
    monkeypatch.setattr(mod, "fetch_fomc", _no_fomc)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("FRED_API_KEY", key)
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(tmp_path / "none"), "--first-run", "--source", source]) == 0
    assert set(calls) == {used} and len(calls) == len(uc.RELEASES)
    cal, ok = uc.read_release(str(out))
    assert set(ok) == set(uc.EVENT_TYPES) and (cal[cal["event_type"] == "nfp"]["source"] != "rule").all()
    assert ("keyless" if used == "keyless" else "FRED API") in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------- phantoms and gaps
def test_revision_only_dates_are_dropped_so_the_real_release_next_to_them_survives():
    # ALFRED lists the January-CPI seasonal-factor revision (2015-02-20) six days before the real release (02-26);
    # the spacing quarantine used to drop both, so the calendar had no CPI that February
    cpi = pd.DatetimeIndex(["2015-01-16", "2015-02-20", "2015-02-26", "2015-03-24"])
    cal, quarantined = uc.quarantine_spacing(uc.calendar_frame({"cpi": cpi}, NOW))
    ids = set(cal["event_id"])
    assert quarantined.empty and "cpi:2015-02-26" in ids and "cpi:2015-02-20" not in ids
    assert list(uc.drop_revision_only("nfp", pd.DatetimeIndex(["2024-08-02", "2024-08-21", "2024-09-06"]))) == [
        pd.Timestamp("2024-08-02"), pd.Timestamp("2024-09-06")]          # the benchmark preview is not an NFP


def test_a_gdp_phantom_never_becomes_a_quarters_advance():
    gdp = pd.DatetimeIndex(["2024-09-26", "2024-10-02", "2024-10-30", "2024-11-27", "2026-02-20", "2026-03-13",
                            "2026-04-09", "2026-04-30", "2026-05-28"])
    cal = uc.calendar_frame({"gdp": gdp}, NOW)
    adv = set(cal.loc[cal["event_type"] == "gdp_advance", "event_id"])
    assert {"gdp_advance:2024-10-30", "gdp_advance:2026-04-30"} <= adv
    assert not adv & {"gdp_advance:2024-10-02", "gdp_advance:2026-04-09"}
    assert "gdp:2026-04-09" in set(cal["event_id"]) and "gdp:2024-10-02" not in set(cal["event_id"])


def test_revision_only_and_spacing_exempt_never_overlap_and_name_known_types():
    assert not uc.REVISION_ONLY & uc.SPACING_EXEMPT
    for key in uc.REVISION_ONLY | uc.SPACING_EXEMPT:
        t, day = key.split(":")
        assert t in uc.TIERS and pd.Timestamp(day).strftime("%Y-%m-%d") == day


def test_monthly_gaps_report_a_lost_release_but_not_a_normal_month():
    nfp = pd.DatetimeIndex(["2024-06-07", "2024-07-05", "2024-08-02", "2024-10-04", "2024-11-01"])
    cal = uc.calendar_frame({"nfp": nfp}, NOW)
    gaps = [g for g in uc.monthly_gaps(cal, through=pd.Timestamp("2024-12-31", tz="UTC")) if g.startswith("nfp")]
    assert gaps == ["nfp: 63 days between 2024-08-02 and 2024-10-04"]


# ------------------------------------------------------------------------------------------------- review edits
def test_revision_only_equals_its_committed_evidence():
    # each phantom with the agency check behind it (bls.gov schedules, BEA archive, Census/DOL rules; 2026-10-25)
    ev = pd.read_csv(FIX / "revision_only_evidence.csv")
    assert set(ev["event_id"]) == set(uc.REVISION_ONLY) and ev["event_id"].is_unique
    assert set(ev["check"]) <= {"bls_schedule", "bea_archive", "census_annual_revision", "dol_weekly"}
    assert ev["evidence"].str.len().min() > 20


@pytest.mark.parametrize(("t", "year", "expected"), [
    ("cpi", 2016, (12, 12)), ("nfp", 2024, (12, 12)), ("retail_sales", 2015, (12, 12)), ("jolts", 2010, (5, 5)),
    ("jolts", 2011, (12, 12)), ("pce", 2019, (11, 11)), ("ppi", 2025, (10, 10)), ("nfp", 2025, (11, 11)),
    ("pce", 2026, (13, 13)), ("nfp", 2013, (12, 12)), ("gdp_advance", 2025, (4, 4)), ("fomc_statement", 2020, (7, 7)),
    ("fomc_statement", 2021, (8, 8)), ("ism_services", 2022, (12, 12)),
])
def test_expected_counts_are_exact_with_listed_exceptions(t: str, year: int, expected: tuple[int, int]):
    assert uc.expected_count(t, year) == expected


def test_time_overrides_move_the_event_to_the_published_time():
    # PCE 2026-01-22 was at 10:00 New York, not 08:30: a [-15, +30] min blackout must sit around 15:00 UTC
    cal = uc.calendar_frame({"pce": pd.DatetimeIndex(["2026-01-22", "2026-02-20"])}, NOW)
    ev = cal.set_index("event_id")["event_utc"]
    assert ev["pce:2026-01-22"] == pd.Timestamp("2026-01-22 15:00", tz="UTC")
    assert ev["pce:2026-02-20"] == pd.Timestamp("2026-02-20 13:30", tz="UTC")
    a = cal.set_index("event_id")["available_utc"]
    assert a["pce:2026-01-22"] == ev["pce:2026-01-22"] - uc.SCHEDULE_LEAD
    assert not set(uc.TIME_OVERRIDES) & uc.REVISION_ONLY
    for key in uc.TIME_OVERRIDES:
        assert key.split(":")[0] in uc.TIERS


def test_an_overridden_time_is_not_flagged_again():
    s = _Session(_pages(_alfred_pce(PCE_PAST)))
    out = kl.fetch_release_keyless(REL["pce"], NOW, session=s, sleep=lambda _: None)  # type: ignore[arg-type]
    assert not any(n.startswith("TIME FLAG pce") for n in out.notes)    # both 2026 PCE flags are overridden


class _Big(_Resp):
    def __init__(self, n: int):
        super().__init__("")
        self.n, self.closed = n, False

    def iter_content(self, chunk_size: int = 1):
        sent = 0
        while sent < self.n:
            yield b"x" * min(chunk_size, self.n - sent)
            sent += chunk_size

    def close(self) -> None:
        self.closed = True


def test_get_refuses_a_response_over_the_size_cap_without_retrying():
    calls: list[int] = []

    class S:
        def get(self, url: str, **kw: Any) -> _Big:
            assert kw.get("stream") is True
            calls.append(1)
            return _Big(kl.MAX_RESPONSE_BYTES + 1)
    with pytest.raises(uc.FredError, match="larger than"):
        kl._get("https://x", retries=3, session=S(), sleep=lambda _: None)  # type: ignore[arg-type]
    assert calls == [1]
    ok = _Big(1000)
    assert kl._read_capped(ok, "u") == "x" * 1000


def test_an_odd_time_without_an_override_is_flagged():
    page = _text("fred_calendar_rid54_2026.html")
    i = page.index("February 20, 2026")
    page = page[:i] + page[i:].replace("7:30 am", "9:00 am", 1)
    out = kl.fetch_release_keyless(REL["pce"], NOW, session=_Session(_pages(_alfred_pce(PCE_PAST), cal_2026=page)),  # type: ignore[arg-type]
                                   sleep=lambda _: None)
    assert any(n.startswith("TIME FLAG pce:2026-02-20 listed at 10:00 ET, calendar-v1 uses 08:30 ET") for n in out.notes)

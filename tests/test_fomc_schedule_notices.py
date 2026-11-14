"""BACKLOG 235 (release side): the Fed's FOMC schedule announcements, hand-checked against the federalreserve.gov press
releases, give each scheduled statement and press conference the time its date was published. calendar-v1 gets them
as appended `scheduled` rows (source fed, vintage = the build day) without touching a published row, and the research
catalogue reads them instead of the assumed 1-December floor, so a schedule published late is never read early."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import us_calendar as uc
from goldbot.research.catalogue import data as DA

VINTAGE = pd.Timestamp("2026-11-14", tz="UTC")


def _ns(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value)


def _row(eid: str, ev: str, status: str, av: str, source: str = "fed") -> dict:
    t = eid.split(":")[0]
    return {"event_id": eid, "event_type": t, "tier": uc.TIERS[t], "release_id": -1,
            "event_utc": pd.Timestamp(ev, tz="UTC"), "status": status, "available_utc": pd.Timestamp(av, tz="UTC"),
            "ts_utc": pd.Timestamp(av, tz="UTC"), "vintage": pd.Timestamp("2026-11-08"), "source": source}


def _cal() -> pd.DataFrame:
    return pd.DataFrame([
        _row("fomc_statement:2012-03-13", "2012-03-13 18:15", "scheduled", "2012-03-06 18:15"),
        _row("fomc_statement:2012-08-01", "2012-08-01 16:15", "scheduled", "2012-07-25 16:15"),   # moved 2012-05-16
        _row("fomc_statement:2011-09-21", "2011-09-21 18:15", "scheduled", "2011-09-14 18:15"),   # moved in Aug 2011
        _row("fomc_press_conference:2014-03-19", "2014-03-19 18:30", "scheduled", "2014-03-12 18:30"),
        _row("fomc_press_conference:2014-04-30", "2014-04-30 18:30", "scheduled", "2014-04-23 18:30"),
        _row("fomc_minutes:2014-04-09", "2014-04-09 18:00", "scheduled", "2014-04-02 18:00"),
        _row("fomc_statement:2020-03-18", "2020-03-18 18:00", "scheduled", "2020-03-11 18:00", "manual"),
        _row("fomc_statement:2020-03-18", "2020-03-18 18:00", "cancelled", "2020-03-15 21:00", "manual"),
    ])


def test_the_notice_table_matches_the_fed_press_releases() -> None:
    by_at = {n.at_et[:10]: n for n in uc.FOMC_SCHEDULE_NOTICES}
    assert "2024-08-09" in by_at and "2025-09-05" in by_at and "2026-11-12" in by_at
    assert "2026-01-28" in by_at["2024-08-09"].statements and "2027-01-27" in by_at["2024-08-09"].statements
    assert "2027-12-08" in by_at["2025-09-05"].statements
    rev = by_at["2026-11-12"]           # revised 2027: no decision on 27 Oct, 8 Dec replaced by 17 Nov
    assert "2027-11-17" in rev.statements and "2027-12-08" not in rev.statements and "2027-10-27" not in rev.statements
    for n in uc.FOMC_SCHEDULE_NOTICES:
        assert n.url.startswith("https://www.federalreserve.gov/newsevents/pressreleases/monetary")
        assert set(n.press) <= set(n.statements)
        at = pd.Timestamp(n.at_et)
        assert all(pd.Timestamp(d) > at for d in n.statements)     # a notice lists meetings after itself


def test_notice_rows_are_appended_and_never_change_a_published_row() -> None:
    cal = _cal()
    add = uc.schedule_notice_rows(cal, VINTAGE)
    got = dict(zip(add["event_id"], add["available_utc"]))
    assert got["fomc_statement:2012-03-13"] == pd.Timestamp("2011-03-25 20:00", tz="UTC")     # 16:00 EDT
    assert got["fomc_statement:2012-08-01"] == pd.Timestamp("2012-05-16 18:00", tz="UTC")     # the revision
    assert got["fomc_press_conference:2014-03-19"] == pd.Timestamp("2013-04-05 15:30", tz="UTC")
    assert got["fomc_statement:2020-03-18"] == pd.Timestamp("2019-05-17 14:00", tz="UTC")     # until its cancellation
    assert "fomc_statement:2011-09-21" not in got           # not on any notice with that day: the 7-day stamp stays
    assert "fomc_press_conference:2014-04-30" not in got    # notices of 2014 list quarterly press conferences only
    assert not any(e.startswith("fomc_minutes") for e in got)
    assert set(add["status"]) == {uc.SCHEDULED} and set(add["source"]) == {"fed"}
    assert (pd.to_datetime(add["vintage"]) == VINTAGE.tz_localize(None)).all()
    merged = uc.add_schedule_notices(cal, VINTAGE)
    uc.check_keeps_history(cal, merged)                      # every published row kept unchanged
    assert len(merged) == len(cal) + len(add)
    assert len(uc.add_schedule_notices(merged, VINTAGE)) == len(merged)     # idempotent: a rerun adds nothing
    pd.testing.assert_frame_equal(uc.count_table(merged), uc.count_table(cal))   # counts read the latest status


def test_known_timeline_sees_a_meeting_from_its_notice_and_drops_it_at_its_cancellation() -> None:
    merged = uc.add_schedule_notices(_cal(), VINTAGE)
    kt = uc.known_timeline(merged[merged["event_id"] == "fomc_statement:2020-03-18"])
    at = pd.DatetimeIndex(kt["available_utc"])
    before = kt[at < pd.Timestamp("2020-03-15 21:00", tz="UTC")]
    after = kt[at >= pd.Timestamp("2020-03-15 21:00", tz="UTC")]
    assert before["available_utc"].min() == pd.Timestamp("2019-05-17 14:00", tz="UTC")
    assert (before["next_event_utc"] == pd.Timestamp("2020-03-18 18:00", tz="UTC")).all()
    assert after["next_event_utc"].isna().all()


@pytest.fixture
def raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "calendar").mkdir()
    _cal().to_parquet(tmp_path / "calendar" / "us_calendar.parquet")
    monkeypatch.setenv("GOLDBOT_RAW", str(tmp_path))
    return tmp_path


def test_the_catalogue_reads_a_late_published_schedule_only_from_its_publication(raw: Path) -> None:
    ev, av, _ = DA.us_events_avail("confirm", ("fomc_statement",))
    k = {int(e): int(a) for e, a in zip(ev, av)}
    # 1 Aug 2012 was first published on 16 May 2012 (the revision); the old 1-Dec floor read it from 2011-12-01
    assert k[_ns("2012-08-01 16:15")] == _ns("2012-05-16 18:00")
    assert k[_ns("2012-03-13 18:15")] == _ns("2011-03-25 20:00")
    assert k[_ns("2011-09-21 18:15")] == _ns("2011-09-14 18:15")   # on no notice with that day: 7-day stamp
    assert k[_ns("2020-03-18 18:00")] == _ns("2019-05-17 14:00")
    assert len(ev) == len(np.unique(ev))                          # one interval per meeting, not one per row

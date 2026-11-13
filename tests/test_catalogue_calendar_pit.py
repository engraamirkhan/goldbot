"""Catalogue scheduled events read point in time (quant review of BACKLOG 152): a cancellation ends an event once it is
published, the 2020 unscheduled FOMC statements are known only when issued, and the Fed's meeting schedule is known
from its publication floor (BACKLOG 235), so S07's next-meeting skip reads published meetings."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import us_calendar as uc
from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import data as DA
from goldbot.research.catalogue import detectors_catalogue as DC
from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue.data import DAY, Bars

BIG = np.iinfo(np.int64).max


def _ns(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value)


def _row(eid: str, ev: str, status: str, av: str, source: str) -> dict:
    t = eid.split(":")[0]
    return {"event_id": eid, "event_type": t, "tier": 1, "release_id": -1, "event_utc": pd.Timestamp(ev, tz="UTC"),
            "status": status, "available_utc": pd.Timestamp(av, tz="UTC"), "ts_utc": pd.Timestamp(av, tz="UTC"),
            "vintage": pd.Timestamp("2026-01-01"), "source": source}


@pytest.fixture
def raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    rows = [
        _row("nfp:2013-09-06", "2013-09-06 12:30", "scheduled", "2013-08-30 12:30", "fred"),
        _row("nfp:2013-10-04", "2013-10-04 12:30", "scheduled", "2013-09-27 12:30", "manual"),
        _row("nfp:2013-10-04", "2013-10-04 12:30", "cancelled", "2013-10-01 04:00", "manual"),
        _row("fomc_statement:2020-01-29", "2020-01-29 19:00", "scheduled", "2020-01-22 19:00", "fed"),
        _row("fomc_statement:2020-03-18", "2020-03-18 18:00", "scheduled", "2020-03-11 18:00", "manual"),
        _row("fomc_statement:2020-03-18", "2020-03-18 18:00", "cancelled", "2020-03-15 21:00", "manual"),
    ]
    (tmp_path / "calendar").mkdir()
    pd.DataFrame(rows).to_parquet(tmp_path / "calendar" / "us_calendar.parquet")
    monkeypatch.setenv("GOLDBOT_RAW", str(tmp_path))
    return tmp_path


def _get(ev: np.ndarray, av: np.ndarray, until: np.ndarray, at: str) -> tuple[int, int]:
    k = np.flatnonzero(ev == _ns(at))
    assert len(k) == 1, at
    return int(av[k[0]]), int(until[k[0]])


def test_a_cancelled_release_ends_when_its_cancellation_is_published(raw: Path) -> None:
    ev, av, until = DA.us_events_avail("confirm", ("nfp",))
    assert _get(ev, av, until, "2013-10-04 12:30") == (_ns("2013-09-27 12:30"), _ns("2013-10-01 04:00"))
    assert _get(ev, av, until, "2013-09-06 12:30")[1] == BIG


def test_unscheduled_fomc_statements_are_known_only_when_issued(raw: Path) -> None:
    ev, av, until = DA.us_events_avail("confirm", ("fomc_statement",))
    for at in ("2020-03-03 15:00", "2020-03-15 21:00"):        # 10:00 ET and 17:00 ET (EDT from 8 March)
        assert _get(ev, av, until, at) == (_ns(at), BIG)
    assert _get(ev, av, until, "2020-03-18 18:00")[1] == _ns("2020-03-15 21:00")


def test_fed_meeting_schedule_is_known_from_its_publication_floor(raw: Path) -> None:
    """BACKLOG 235: a scheduled FOMC meeting from the Fed's calendar (source fed) is known from 1 Dec of the prior year,
    not 7 days ahead; a hand-checked (manual) row keeps its own stamp."""
    ev, av, _ = DA.us_events_avail("confirm", ("fomc_statement",))
    assert _get(ev, av, _, "2020-01-29 19:00")[0] == _ns("2019-12-01")
    assert _get(ev, av, _, "2020-03-18 18:00")[0] == _ns("2020-03-11 18:00")


def test_us_calendar_lists_the_2020_unscheduled_statements() -> None:
    rows = uc.unscheduled_rows(pd.Timestamp("2026-01-01"))
    got = set(zip(rows["event_id"], rows["event_utc"]))
    assert ("fomc_statement:2020-03-03", pd.Timestamp("2020-03-03 15:00", tz="UTC")) in got
    assert ("fomc_statement:2020-03-15", pd.Timestamp("2020-03-15 21:00", tz="UTC")) in got
    assert (rows["available_utc"] == rows["event_utc"]).all() and set(rows["status"]) == {uc.SCHEDULED}
    assert uc.expected_count("fomc_statement", 2020) == (9, 9)   # 8 scheduled - 1 cancelled + 2 unscheduled
    cal = uc.calendar_frame({}, pd.Timestamp("2026-01-01", tz="UTC"))
    assert {"fomc_statement:2020-03-03", "fomc_statement:2020-03-15"} <= set(cal["event_id"])


# ------------------------------------------------------------------ detectors and truncation honour `until`
def _daily(start: str, n: int, inst: str = "US500") -> Bars:
    from goldbot.data.timeutil import epoch_ns
    ts = epoch_ns(pd.bdate_range(start, periods=n, tz="UTC"))
    c = 100 + np.cumsum(np.random.default_rng(5).normal(0, 1, n))
    o = np.r_[c[0], c[:-1]]
    z = np.zeros(n)
    return Bars(inst, "1d", ts, ts + DAY, o, np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5, c, z,
                np.zeros(n, bool), np.zeros(n, np.int64), z)


def test_s06_does_not_trade_a_release_cancelled_before_the_decision() -> None:
    b = _daily("2013-07-01", 100)
    ev = np.array([_ns("2013-10-04 12:30")])
    j = int(np.flatnonzero((b.t // DAY) == _ns("2013-10-03") // DAY)[0])
    live = K.Ctx(window="x", events={"s06_all": ev}, avail={"s06_all": np.array([_ns("2013-09-27")])})
    gone = K.Ctx(window="x", events={"s06_all": ev}, avail={"s06_all": np.array([_ns("2013-09-27")])},
                 until={"s06_all": np.array([_ns("2013-10-01 04:00")])})
    assert j in set(DC.s06_macro_day(b, {"events": "s06_all"}, live).i.tolist())
    assert j not in set(DC.s06_macro_day(b, {"events": "s06_all"}, gone).i.tolist())


def test_truncate_ctx_hides_a_cancellation_published_after_the_cut() -> None:
    from tests.test_eval2_truncation import ST
    ctx = K.Ctx(window="x", events={"s06_all": np.array([10, 20])}, avail={"s06_all": np.array([1, 2])},
                until={"s06_all": np.array([5, 15])})
    c = ST.truncate_ctx(ctx, 8, scheduled_events=False)
    assert c.until["s06_all"].tolist() == [5, BIG]


def test_add_holidays_refuses_a_window_past_the_calendar_end() -> None:
    with pytest.raises(ValueError, match="ends"):
        D.add_holidays(K.Ctx(window="x"), end_ns=_ns("2028-01-02"))
    D.add_holidays(K.Ctx(window="x"), end_ns=_ns("2028-01-01"))           # through 2027-12-31: fine

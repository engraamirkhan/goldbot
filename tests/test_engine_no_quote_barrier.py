"""Backlog 164: the dead-feed time barrier counts a decision bar when any of its minutes is expected open.

Design rule (engine/downtime.py, backlog 161): a base bar counts toward a hold when any of its minutes is in
`SessionTable.expected_open` (the engine's session table less the exchange holidays, the history gap guard's calendar).
The no-quote time barrier (`Engine._host_clock_exits`) counted a bar only when its start was open: the bar the daily
break ends inside (break end 23:02 UTC in March, inside the 23:00 bar) was not counted, and a CME holiday was. The two
counts now share one rule (`downtime.open_bars`), so the no-quote barrier and the downtime catch-up agree on a hold."""
from __future__ import annotations

import pandas as pd

from goldbot.data.calendar import SessionTable
from goldbot.engine import downtime
from goldbot.engine.runner import QUOTE_DEAD_S
from tests.test_engine_downtime_catch_up import M15, SEC, _counted_trade, _engine

WED_BREAK = pd.Timestamp("2025-03-05 21:45", tz="UTC")    # the last close before Wednesday's daily break (UTC+2)
GOOD_THU = pd.Timestamp("2025-04-17 20:00", tz="UTC")     # Thursday before Good Friday 2025 (CME metals closed)


def test_a_bar_the_daily_break_ends_inside_counts_toward_the_no_quote_barrier():
    """21:45 to 23:15 UTC: the 21:45 bar (open to the 21:59 break) and the 23:00 bar (the market reopens at 23:02)
    count; the four bars wholly inside the break do not. The start-only rule counted 1."""
    assert downtime.open_bars(SessionTable(), SEC, WED_BREAK, WED_BREAK + 6 * M15) == 2


def test_a_cme_holiday_never_counts_toward_the_no_quote_barrier():
    """Thursday 20:00 UTC to Good Friday 20:00 UTC: Thursday's bars to the 20:59 break (4); Friday's session is a CME
    holiday. The start-only rule on the plain table counted 91."""
    assert downtime.open_bars(SessionTable(), SEC, GOOD_THU, GOOD_THU + pd.Timedelta(days=1)) == 4


def test_the_no_quote_barrier_and_the_downtime_catch_up_count_the_same_bars():
    """Over the same span the dead-feed barrier's whole bars from a close are that close's missed closes plus the bar
    that closes at the end: one rule for both."""
    for start, hours in ((WED_BREAK, 30), (GOOD_THU, 80), (pd.Timestamp("2025-03-07 21:00", tz="UTC"), 60)):
        end = start + pd.Timedelta(hours=hours)
        last = SessionTable().expected_open(pd.date_range(end - M15, periods=15, freq="60s")).any()
        assert downtime.open_bars(SessionTable(), SEC, start, end) == \
            downtime.missed_closes(SessionTable(), SEC, start, end) + int(last)


def test_no_bars_before_a_whole_bar_has_passed_and_none_backwards():
    assert downtime.open_bars(SessionTable(), SEC, WED_BREAK, WED_BREAK + pd.Timedelta(minutes=14)) == 0
    assert downtime.open_bars(SessionTable(), SEC, WED_BREAK, WED_BREAK - M15) == 0


def test_a_session_table_that_cannot_answer_counts_no_bars(caplog):
    """Fail safe, as before: the time exit then waits for a bar close; the broker's stop stays in place."""

    class Broken:
        def expected_open(self, utc_ts: pd.DatetimeIndex) -> None:
            raise ValueError("no table")

    assert downtime.open_bars(Broken(), SEC, WED_BREAK, WED_BREAK + 10 * M15) == 0   # type: ignore[arg-type]
    assert "priceless time barrier" in caplog.text


def test_the_no_quote_barrier_never_fires_on_a_cme_holidays_hours(tmp_path):
    """A trade 3 of 10 bars into its hold when the feed dies on the Thursday close before Good Friday: on
    Friday at 12:00 UTC (before the weekend cut) the dead-feed barrier has counted 4 bars (7 of 10), so it does not
    close the trade; the start-only rule counted Friday's hours and closed it."""
    pb, pid, _ = _counted_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    tr = eng.open[pid]
    tr.max_bars, tr.bars_through = 10, GOOD_THU
    now = GOOD_THU + pd.Timedelta(hours=16)
    eng._quote_invalid_since = now - pd.Timedelta(seconds=QUOTE_DEAD_S)
    eng._host_clock_exits(now)
    assert pid in eng.open

"""The north-star day of the owner's reports (goldbot/ops/report_days.py; ADR 0004 section 6): one opportunity day,
D-1 22:00 to D 22:00 UTC, used by the monthly report and the performance report alike. The loss caps keep their own
day (timeutil.risk_day, 00:00 UTC): this lane does not move a limit."""
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.timeutil import ROLLOVER_UTC_H, opportunity_day, risk_day
from goldbot.features import day_type
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.monthly_report import build_monthly_report
from goldbot.ops.perf_report import build_performance_report
from goldbot.ops.report_days import (
    DAY_BOUNDARY,
    day_before,
    day_bounds,
    day_buckets,
    month_days,
    report_day,
    weekdays,
)
from goldbot.risk.gate import AccountState, new_day


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _ny(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York").tz_convert("UTC")


# ---------------------------------------------------------------------------------------------- the boundary
def test_the_report_day_rolls_at_22_utc_to_the_nanosecond():
    assert report_day(_utc("2026-10-07 21:59:59.999999999")) == "2026-10-07"
    assert report_day(_utc("2026-10-07 22:00")) == "2026-10-08"
    assert report_day(_utc("2026-10-07 23:59")) == "2026-10-08"
    assert report_day(_utc("2026-10-08 00:00")) == "2026-10-08"


def test_a_non_utc_instant_is_read_as_the_same_utc_instant():
    assert report_day(pd.Timestamp("2026-10-07 23:30", tz="Europe/London")) == "2026-10-08"   # 22:30 UTC (BST)
    assert report_day(pd.Timestamp("2026-10-07 22:30", tz="Europe/London")) == "2026-10-07"   # 21:30 UTC


def test_there_is_one_opportunity_day_definition_shared_with_the_day_type_features():
    assert day_type.opportunity_day is opportunity_day and day_type.ROLLOVER_UTC_H == ROLLOVER_UTC_H == 22
    assert "22:00" in DAY_BOUNDARY and "UTC" in DAY_BOUNDARY


@pytest.mark.parametrize(("friday_close", "sunday_reopen", "friday", "monday"), [
    # US DST starts Sun 8 Mar 2026: the Friday before closes 17:00 EST = 22:00 UTC, the reopen is 18:00 EDT = 22:00 UTC
    ("2026-03-06 17:00", "2026-03-08 18:00", "2026-03-06", "2026-03-09"),
    # the US-EU gap week (US on summer time, Europe not yet): 17:00 EDT = 21:00 UTC
    ("2026-03-27 17:00", "2026-03-29 18:00", "2026-03-27", "2026-03-30"),
    # US DST ends Sun 1 Nov 2026: Friday closes 17:00 EDT = 21:00 UTC, Sunday reopens 18:00 EST = 23:00 UTC
    ("2026-10-30 17:00", "2026-11-01 18:00", "2026-10-30", "2026-11-02"),
])
def test_dst_weeks_keep_the_friday_close_on_friday_and_the_sunday_reopen_on_monday(friday_close, sunday_reopen,
                                                                                   friday, monday):
    last = _ny(friday_close) - pd.Timedelta(seconds=1)
    assert report_day(last) == friday
    assert report_day(_ny(sunday_reopen)) == monday
    assert weekdays(friday, monday) == 2                  # Saturday and Sunday are not trading days


@pytest.mark.parametrize("date", ["2026-03-04", "2026-03-10", "2026-03-31", "2026-10-28", "2026-11-03"])
def test_the_daily_break_holds_the_boundary_in_both_us_dst_regimes(date):
    """17:00-18:00 New York is 21:00-22:00 UTC in summer and 22:00-23:00 UTC in winter: the last minute before the
    break is on day D and the first after it on D+1, with no bar of a session split across two days."""
    d = pd.Timestamp(date)
    assert report_day(_ny(f"{date} 16:59")) == date
    assert report_day(_ny(f"{date} 18:00")) == f"{d + pd.Timedelta(days=1):%Y-%m-%d}"


def test_weekdays_and_month_days_count_monday_to_friday_opportunity_days_inclusive():
    assert month_days("2026-10") == ("2026-10-01", "2026-10-31")
    assert month_days("2026-02") == ("2026-02-01", "2026-02-28")
    assert weekdays(*month_days("2026-10")) == 22
    assert weekdays("2026-10-05", "2026-10-09") == 5 and weekdays("2026-10-10", "2026-10-11") == 0
    assert weekdays("2026-10-09", "2026-10-08") == 0


def test_day_buckets_group_items_by_the_opportunity_day_of_their_instant_in_order():
    items = [("a", _utc("2026-10-07 21:59")), ("b", _utc("2026-10-07 22:00")), ("c", _utc("2026-10-08 12:00"))]
    got = day_buckets(items, lambda x: x[1])
    assert {k: [x[0] for x in v] for k, v in got.items()} == {"2026-10-07": ["a"], "2026-10-08": ["b", "c"]}


def test_day_bounds_are_the_22_utc_instants_report_day_puts_on_that_day():
    start, end = day_bounds("2026-10-08")
    assert (start, end) == (_utc("2026-10-07 22:00"), _utc("2026-10-08 22:00"))
    assert report_day(start) == report_day(end - pd.Timedelta(1, "ns")) == "2026-10-08"
    assert report_day(start - pd.Timedelta(1, "ns")) == "2026-10-07" and report_day(end) == "2026-10-09"


@pytest.mark.parametrize(("now", "before"), [
    ("2026-10-15 21:59", "2026-10-14"),        # still Thursday's opportunity day: the day before is Wednesday
    ("2026-10-15 22:00", "2026-10-15"),        # Friday's day has begun: Thursday's is complete
    ("2026-10-19 06:45", "2026-10-18"),        # Monday morning: the day before is Sunday's (the reopen is Monday's)
    ("2026-11-01 23:30", "2026-11-01"),        # a winter Sunday reopen (23:00 UTC) is on Monday 2 Nov
])
def test_day_before_is_the_opportunity_day_before_the_one_holding_the_instant(now, before):
    assert day_before(_utc(now)) == before


# ---------------------------------------------------------------------------------------------- both reports agree
def _ct(pid: int, exit_utc: str, pnl: float, r: float, **kw: Any) -> ClosedTrade:
    ex = _utc(exit_utc)
    fields: dict[str, Any] = dict(account_id="icm-demo", broker="icm", mode="demo", exit_utc=ex,
                                  entry_utc=ex - pd.Timedelta(minutes=30), ret=0.0, pnl=pnl, equity_before=1000.0,
                                  position_id=pid, r=r, pnl_basis="net", pnl_currency="GBP", commission=0.5, swap=0.0)
    fields.update(kw)
    return ClosedTrade(**fields)


def _seed(state: Path) -> None:
    for t in [
        _ct(1, "2026-10-05 12:00", 30.0, 3.0),                      # Mon 5
        _ct(2, "2026-10-05 21:59", 25.0, 2.5),                      # Mon 5, the last minute before the roll
        _ct(3, "2026-10-05 22:00", -8.0, -1.0),                     # Tue 6 (UTC date Mon 5)
        _ct(4, "2026-10-05 23:30", -60.0, -6.0),                    # Tue 6
        _ct(5, "2026-10-06 23:00", 40.0, 4.0, pnl_basis="gross"),   # Wed 7, gross only: in no day total
        _ct(6, "2026-10-04 22:30", 5.0, 0.5),                       # Sun 22:30 UTC, the reopen: Mon 5
    ]:
        append_closed_trade(state, t)
    (state / "engine_icm-demo.json").write_text('{"account": "icm-demo", "currency": "GBP", "equity": 1100.0}')


def test_the_monthly_and_performance_reports_put_every_close_on_the_same_day(tmp_path):
    _seed(tmp_path)
    now = _utc("2026-10-10 06:00")
    [m] = build_monthly_report(tmp_path, month="2026-10", now=now).accounts
    [p] = build_performance_report(tmp_path, start="2026-10-01", end="2026-10-09", now=now).accounts
    monthly = {d.day: d.pnl for d in m.best_days}
    assert monthly == {"2026-10-05": pytest.approx(60.0), "2026-10-06": pytest.approx(-68.0)}
    assert p.days.traded_days == len(monthly) == 2
    assert p.days.best_day == "2026-10-05" and p.days.best_day_pnl == pytest.approx(60.0)
    assert p.days.worst_day == "2026-10-06" and p.days.worst_day_pnl == pytest.approx(-68.0)
    assert m.days_50_plus == p.days.days_50_plus == 1
    assert m.trading_days == p.days.weekdays == 7             # Thu 1 .. Fri 9 October
    assert m.gross_only == 1 and p.overall.gross_only == 1


def test_the_performance_report_drawdown_keeps_the_risk_day_of_the_loss_caps(tmp_path):
    """The loss caps reset at 00:00 UTC (timeutil.risk_day): the closes at 22:00 and 23:30 UTC on Mon 5 Oct are on
    opportunity day Tue 6 (-68) but on risk day Mon 5, with the Monday trades (30 + 25 - 8 - 60 = -13)."""
    _seed(tmp_path)
    [p] = build_performance_report(tmp_path, start="2026-10-01", end="2026-10-09", now=_utc("2026-10-10 06:00")).accounts
    assert p.drawdown.worst_day == "2026-10-05" and p.drawdown.worst_day_loss == pytest.approx(13.0)
    assert f"{risk_day(pd.DatetimeIndex([_utc('2026-10-05 23:30')]))[0]:%Y-%m-%d}" == "2026-10-05"


def test_the_risk_day_still_resets_the_day_start_equity_at_00_utc_not_at_the_22_utc_roll():
    st = AccountState(equity=1000.0, balance_closed_hwm=1000.0, day_start_equity=1000.0, week_start_equity=1000.0,
                      open_positions=0, margin_used=0.0, last_tick_age_s=0.0, spread_points=0.0)
    st.equity = 990.0
    assert new_day(st, _utc("2026-10-05 22:30"), _utc("2026-10-05 00:05")) is False
    assert st.day_start_equity == 1000.0
    assert new_day(st, _utc("2026-10-06 00:00"), _utc("2026-10-05 00:05")) is True
    assert st.day_start_equity == 990.0


def test_the_configured_risk_day_anchor_is_the_hard_coded_risk_day():
    """`risk_day_anchor` is read by no code (docs/handoff.d, the risk-day anchor finding): the loss caps reset at
    timeutil.risk_day, 00:00 UTC. Until a lane makes the setting real, the shipped value must say what the code does."""
    a = load_settings().risk_day_anchor
    assert (a.tz, a.time) == ("UTC", "00:00")

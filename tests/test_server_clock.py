"""Server clock zones (Europe/Athens vs NY+7) across the US-EU gap weeks, and the offset probe that measures which one
the broker runs (goldbot/data/timeutil.py, goldbot/ops/server_clock.py, health.check_server_clock, preflight)."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from goldbot.data.calendar import SessionTable
from goldbot.data.timeutil import (
    NY_PLUS_7,
    NewYorkPlus7,
    server_to_utc,
    server_utc_offset,
    server_wall,
    server_zone,
    utc_to_server,
)
from goldbot.execution.broker import Tick
from goldbot.ops import accounts, health, server_clock
from goldbot.ops.health import AccountRef, HealthContext

ATHENS = "Europe/Athens"
# 2026: US DST from Sun 8 Mar, EU from Sun 29 Mar; EU back on Sun 25 Oct, US on Sun 1 Nov
MARCH_GAP = pd.Timestamp("2026-03-18 12:00")          # Wednesday inside the March gap (server wall clock)
NOV_GAP = pd.Timestamp("2026-10-28 12:00")            # Wednesday inside the Oct/Nov gap
OUTSIDE = [pd.Timestamp("2026-02-11 12:00"), pd.Timestamp("2026-04-15 12:00"), pd.Timestamp("2026-07-15 12:00"),
           pd.Timestamp("2026-11-11 12:00")]


def utc(server_ts: pd.Timestamp, tz: str) -> pd.Timestamp:
    return server_to_utc(pd.DatetimeIndex([server_ts]), tz)[0]


# ------------------------------------------------------------------------------------------------ zones
def test_ny7_and_athens_differ_by_one_hour_only_in_the_gap_weeks():
    assert utc(MARCH_GAP, NY_PLUS_7) == pd.Timestamp("2026-03-18 09:00", tz="UTC")     # UTC+3 (US on EDT)
    assert utc(MARCH_GAP, ATHENS) == pd.Timestamp("2026-03-18 10:00", tz="UTC")        # UTC+2 (EU still on EET)
    assert utc(NOV_GAP, NY_PLUS_7) == pd.Timestamp("2026-10-28 09:00", tz="UTC")       # UTC+3 (US still on EDT)
    assert utc(NOV_GAP, ATHENS) == pd.Timestamp("2026-10-28 10:00", tz="UTC")          # UTC+2 (EU back on EET)
    for ts in OUTSIDE:
        assert utc(ts, NY_PLUS_7) == utc(ts, ATHENS)


def test_ny7_switches_at_0900_server_time_on_the_us_dates():
    assert utc(pd.Timestamp("2026-03-08 08:30"), NY_PLUS_7) == pd.Timestamp("2026-03-08 06:30", tz="UTC")   # +2
    assert utc(pd.Timestamp("2026-03-08 10:30"), NY_PLUS_7) == pd.Timestamp("2026-03-08 07:30", tz="UTC")   # +3
    assert utc(pd.Timestamp("2026-11-01 07:30"), NY_PLUS_7) == pd.Timestamp("2026-11-01 04:30", tz="UTC")   # +3
    assert utc(pd.Timestamp("2026-11-01 09:30"), NY_PLUS_7) == pd.Timestamp("2026-11-01 07:30", tz="UTC")   # +2
    assert pd.isna(utc(pd.Timestamp("2026-11-01 08:30"), NY_PLUS_7))     # repeated hour: ambiguous, never guessed
    # 2027 gap weeks (the next ones the RUNBOOK names)
    assert server_utc_offset(NY_PLUS_7, pd.Timestamp("2027-03-17 12:00", tz="UTC")).total_seconds() == 3 * 3600
    assert server_utc_offset(ATHENS, pd.Timestamp("2027-03-17 12:00", tz="UTC")).total_seconds() == 2 * 3600
    assert server_utc_offset(NY_PLUS_7, pd.Timestamp("2027-03-29 12:00", tz="UTC")).total_seconds() == 3 * 3600
    assert server_utc_offset(ATHENS, pd.Timestamp("2027-03-29 12:00", tz="UTC")).total_seconds() == 3 * 3600


@pytest.mark.parametrize("tz", [NY_PLUS_7, ATHENS])
@pytest.mark.parametrize("start,end", [("2026-03-01", "2026-04-05"), ("2026-10-18", "2026-11-12"),
                                       ("2027-03-07", "2027-04-04")])
def test_server_wall_round_trips_every_hour_through_both_gap_periods(tz, start, end):
    walls = pd.date_range(start, end, freq="15min")
    back = server_wall(server_to_utc(walls, tz).dropna(), tz)
    kept = server_to_utc(walls, tz).notna()
    shifted = walls[kept] != back
    # only the spring-forward hour (nonexistent, shifted forward) may differ
    assert shifted.sum() <= 4
    if tz == ATHENS:
        aware = utc_to_server(server_to_utc(walls, tz).dropna(), tz)
        assert (aware.tz_localize(None) == back).all()    # the tz-aware form agrees with the wall-clock helper


def test_pandas_is_never_handed_the_custom_zone_silently():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-03-18 09:00", tz="UTC")])
    with pytest.raises(ValueError, match="server_wall"):
        utc_to_server(idx, NY_PLUS_7)
    with pytest.raises(Exception):                       # pandas would use one fixed offset all year: it must fail loudly
        idx.tz_convert(NewYorkPlus7()).tz_localize(None)


def test_new_york_plus_7_tzinfo_matches_the_helpers_for_python_datetimes():
    z = server_zone(NY_PLUS_7)
    assert isinstance(z, NewYorkPlus7) and z == NewYorkPlus7()
    gap = datetime(2026, 3, 18, 9, 0, tzinfo=timezone.utc).astimezone(z)
    assert (gap.hour, gap.utcoffset()) == (12, timedelta(hours=3))
    winter = datetime(2026, 1, 14, 10, 0, tzinfo=timezone.utc).astimezone(z)
    assert (winter.hour, winter.utcoffset()) == (12, timedelta(hours=2))
    assert datetime(2026, 3, 18, 12, 0, tzinfo=z).astimezone(timezone.utc).hour == 9
    assert z.tzname(None) == "NY+7" and z.utcoffset(None) is None


def test_unknown_server_tz_is_rejected_at_account_load():
    def acc(tz):
        return accounts.Account(account_id="a", broker="icm", mode="demo", server="S", login=None, terminal_path="x",
                                server_tz=tz, symbol="XAUUSD", magic_base=1, enabled=True)
    assert acc(NY_PLUS_7).server_tz == NY_PLUS_7
    assert acc(ATHENS).server_tz == ATHENS
    for bad in ("NY+8", "Europe/Atens", ""):
        with pytest.raises(ValueError, match="server_tz"):
            acc(bad)


def test_friday_weekend_cut_and_daily_break_follow_the_configured_clock_in_the_gap():
    friday = pd.Timestamp("2026-03-20 21:00", tz="UTC")    # NY+7: 00:00 Sat (closed); Athens: 23:00 Fri (open)
    assert not SessionTable(server_tz=NY_PLUS_7).is_open(pd.DatetimeIndex([friday]))[0]
    assert SessionTable(server_tz=ATHENS).is_open(pd.DatetimeIndex([friday]))[0]
    brk = pd.Timestamp("2026-10-28 21:30", tz="UTC")       # NY+7 00:30 (daily break); Athens 23:30 (open)
    assert not SessionTable(server_tz=NY_PLUS_7).is_open(pd.DatetimeIndex([brk]))[0]
    assert SessionTable(server_tz=ATHENS).is_open(pd.DatetimeIndex([brk]))[0]


# ------------------------------------------------------------------------------------------------ probe
NOW = pd.Timestamp("2026-03-18 12:00:00", tz="UTC")         # Wednesday in the March gap, market open
SATURDAY = pd.Timestamp("2026-03-21 12:00", tz="UTC")


def broker_tick(now: pd.Timestamp, broker_tz: str, configured_tz: str, lag_s: float = 1.0) -> Tick:
    """What the adapter returns: the broker stamps in ITS clock, the adapter converts with the CONFIGURED zone."""
    real = now - pd.Timedelta(seconds=lag_s)
    raw = server_wall(real, broker_tz)
    return Tick(ts_utc=server_to_utc(raw, configured_tz)[0], bid=2000.0, ask=2000.3)


def test_probe_ok_when_the_configured_zone_matches_the_broker():
    p = server_clock.assess("icm-demo", NY_PLUS_7, broker_tick(NOW, NY_PLUS_7, NY_PLUS_7).ts_utc, NOW)
    assert p.status == "ok" and p.configured_offset_h == 3 and p.inferred_offset_h == 3
    assert server_clock.entry_block(p) is None


def test_probe_flags_athens_configured_on_a_ny7_broker_in_the_gap_as_an_entry_block():
    tick = broker_tick(NOW, NY_PLUS_7, ATHENS)
    p = server_clock.assess("icm-demo", ATHENS, tick.ts_utc, NOW)
    assert p.status == "mismatch" and p.configured_offset_h == 2 and p.inferred_offset_h == 3
    assert "reads UTC+3 but Europe/Athens gives UTC+2" in p.reason and "exits unaffected" in p.reason
    assert (tick.ts_utc - NOW) > pd.Timedelta(minutes=55)      # the tick looks an hour in the FUTURE: stale-tick rule blind
    assert "server clock mismatch" in (server_clock.entry_block(p) or "")


def test_probe_flags_ny7_configured_on_an_athens_broker_in_the_gap():
    p = server_clock.assess("icm-demo", NY_PLUS_7, broker_tick(NOW, ATHENS, NY_PLUS_7).ts_utc, NOW)
    assert p.status == "mismatch" and p.inferred_offset_h == 2 and "reads UTC+2 but NY+7 gives UTC+3" in p.reason


def test_probe_cannot_tell_the_zones_apart_outside_the_gap_and_says_ok():
    july = pd.Timestamp("2026-07-15 12:00", tz="UTC")
    p = server_clock.assess("icm-demo", ATHENS, broker_tick(july, NY_PLUS_7, ATHENS).ts_utc, july)
    assert p.status == "ok"


def test_probe_reports_a_stale_tick_while_trading_as_a_mismatch_not_an_offset():
    p = server_clock.assess("icm-demo", ATHENS, broker_tick(NOW, ATHENS, ATHENS, lag_s=20 * 60).ts_utc, NOW)
    assert p.status == "mismatch" and "stale by 20 min" in p.reason


def test_probe_does_not_guess_with_the_market_closed():
    p = server_clock.assess("icm-demo", ATHENS, broker_tick(SATURDAY, ATHENS, ATHENS, lag_s=12 * 3600).ts_utc, SATURDAY)
    assert p.status == "not_measured" and "market closed" in p.reason
    assert server_clock.entry_block(p) is None


def test_probe_never_raises_when_the_tick_read_fails():
    class Down:
        def last_tick(self, symbol):
            raise ConnectionError("http://bridge.invalid:8765 unreachable")
    p = server_clock.measure(Down(), "icm-demo", "XAUUSD", ATHENS, now=NOW)
    assert p.status == "not_measured" and "ConnectionError" in p.reason and "bridge.invalid" not in p.reason

    class Up:
        def last_tick(self, symbol):
            return broker_tick(NOW, ATHENS, ATHENS)
    assert server_clock.measure(Up(), "icm-demo", "XAUUSD", ATHENS, now=NOW).status == "ok"


# ------------------------------------------------------------------------------------------------ health / preflight
def hctx(tmp_path, reader, now=NOW, tz=ATHENS) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None, tick_reader=reader,
                         accounts=[AccountRef(account_id="icm-demo", server_tz=tz)])


def test_health_server_clock_check_fails_on_a_mismatch_and_is_in_the_report(tmp_path):
    ctx = hctx(tmp_path, lambda a, s: broker_tick(NOW, NY_PLUS_7, ATHENS))
    c = health.check_server_clock(ctx, ctx.accounts[0])
    assert c is not None and c.name == "server_clock:icm-demo" and c.status == "fail"
    assert "server_clock:icm-demo" in {x.name for x in health.run_checks(ctx).checks}
    ok = hctx(tmp_path, lambda a, s: broker_tick(NOW, NY_PLUS_7, NY_PLUS_7), tz=NY_PLUS_7)
    c_ok = health.check_server_clock(ok, ok.accounts[0])
    assert c_ok is not None and c_ok.status == "ok"


def test_health_server_clock_check_is_quiet_without_a_bridge_and_never_quotes_errors(tmp_path):
    assert health.check_server_clock(hctx(tmp_path, lambda a, s: None), AccountRef(account_id="icm-demo", server_tz=ATHENS)) is None
    assert health.check_server_clock(hctx(tmp_path, None), AccountRef(account_id="icm-demo", server_tz=ATHENS)) is None
    assert health.check_server_clock(hctx(tmp_path, lambda a, s: 1), AccountRef(account_id="icm-demo")) is None

    def boom(a, s):
        raise ConnectionError("http://bridge.invalid:8765 token=abc")
    c = health.check_server_clock(hctx(tmp_path, boom), AccountRef(account_id="icm-demo", server_tz=ATHENS))
    assert c is not None and c.status == "warn" and "bridge.invalid" not in c.reason and "abc" not in c.reason
    closed = hctx(tmp_path, lambda a, s: broker_tick(SATURDAY, ATHENS, ATHENS, lag_s=12 * 3600), now=SATURDAY)
    c_closed = health.check_server_clock(closed, closed.accounts[0])
    assert c_closed is not None and c_closed.status == "ok"                  # weekend: no alert noise

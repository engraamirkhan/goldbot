"""Exploration arm windows on city clocks (research note time-structure/sessions.md, A-4 local-clock audit): an arm's
`window_local` follows Europe/London and America/New_York through their own DST switches, including the UK/US gap
weeks (2026-03-09..03-27, 2026-10-26..10-30) and the switch days themselves; the frozen `window_utc` arms keep their
pre-registered UTC hours; S1 and S2, which the spec anchors on the local opens, stay on them in the gap weeks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from tests.test_count_owed_events import _bars_1m

ROOT = Path(__file__).resolve().parents[1]
ARMS = {a.id: a for a in A.load_arms(ROOT / "config" / "exploration_arms.yaml")}
NO = (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
# London open to the 16:00 New York close of the cash session: the window the "07-20 UTC" arms approximate in summer
LONDON_TO_NY = {"from": ["Europe/London", "08:00"], "to": ["America/New_York", "16:00"]}


def _arm(window: dict[str, list[str]], **extra: object) -> A.Arm:
    return A.Arm(id="TLOCAL", rule="random_bracket", timeframe="1m",
                 params={"minutes": list(range(60)), "window_local": window, "stop_usd": 3.0, "target_r": 1.0,
                         "hold_bars": 30, **extra})


def _at(text: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime([text], utc=True))


# (UTC date, London 08:00 in UTC, New York 16:00 in UTC): both zones' regimes, the gap weeks and the four switch days
REGIMES = [
    ("2026-01-14", "08:00", "21:00"),   # both on standard time
    ("2026-03-08", "08:00", "20:00"),   # US switch day (02:00 New York, 07:00 UTC): New York already on EDT
    ("2026-03-10", "08:00", "20:00"),   # March gap week: US on EDT, UK still on GMT
    ("2026-03-27", "08:00", "20:00"),   # last weekday of the March gap
    ("2026-03-29", "07:00", "20:00"),   # UK switch day (01:00 UTC): London on BST from 08:00 local
    ("2026-07-01", "07:00", "20:00"),   # both on summer time
    ("2026-10-25", "08:00", "20:00"),   # UK switch day (01:00 UTC): London back on GMT
    ("2026-10-27", "08:00", "20:00"),   # October gap week: UK on GMT, US still on EDT
    ("2026-11-01", "08:00", "21:00"),   # US switch day (02:00 New York, 06:00 UTC): New York back on EST
    ("2026-11-03", "08:00", "21:00"),   # both on standard time again
]


@pytest.mark.parametrize("day, open_utc, close_utc", REGIMES)
def test_a_local_window_opens_and_closes_on_the_city_clocks_in_every_regime_gap_week_and_switch_day(
        day: str, open_utc: str, close_utc: str) -> None:
    w = _arm(LONDON_TO_NY).params
    o, c = pd.Timestamp(f"{day} {open_utc}", tz="UTC"), pd.Timestamp(f"{day} {close_utc}", tz="UTC")
    one = pd.Timedelta(minutes=1)
    t = pd.DatetimeIndex([o - one, o, c, c + one])
    assert list(R.arm_window(t, w)) == [False, True, True, False]      # [from, to] inclusive, as `window_utc`


def test_the_frozen_utc_windows_keep_their_utc_hours_through_the_gap_weeks() -> None:
    s0 = ARMS["S0"].params
    for day in ("2026-03-10", "2026-07-01", "2026-10-27", "2026-11-03"):
        t = pd.DatetimeIndex(pd.to_datetime([f"{day} 06:59", f"{day} 07:00", f"{day} 19:50", f"{day} 19:51"], utc=True))
        assert list(R.arm_window(t, s0)) == [False, True, True, False]


def test_an_arm_window_on_one_city_alone_follows_that_city() -> None:
    w = _arm({"from": ["America/New_York", "08:30"], "to": ["America/New_York", "11:00"]}).params
    assert R.arm_window(_at("2026-10-27 12:30"), w)[0] and not R.arm_window(_at("2026-10-27 12:29"), w)[0]
    assert R.arm_window(_at("2026-11-03 13:30"), w)[0] and not R.arm_window(_at("2026-11-03 12:30"), w)[0]


def test_random_bracket_on_a_local_window_moves_its_utc_schedule_with_london_and_new_york() -> None:
    b1 = _bars_1m("2026-10-19", "2026-11-07")
    arm = _arm(LONDON_TO_NY)
    ctx = R.RuleContext(bars=b1.reset_index(drop=True), b1=b1.reset_index(drop=True), blackout_lo=NO[0],
                        blackout_hi=NO[1])
    c = R.candidates(arm, ctx)
    close = ctx.close_ts[c["idx"].to_numpy()]
    first = pd.Series(close).groupby(close.normalize()).min()
    last = pd.Series(close).groupby(close.normalize()).max()
    hhmm = {d.strftime("%m-%d"): (f.strftime("%H:%M"), la.strftime("%H:%M"))
            for d, f, la in zip(first.index, first, last, strict=True)}
    assert hhmm["10-21"] == ("07:00", "20:00")       # BST and EDT
    assert hhmm["10-27"] == ("08:00", "20:00")       # gap week: GMT and EDT
    assert hhmm["11-04"][0] == "08:00"               # GMT and EST: New York 16:00 is 21:00 UTC, but the common filter
    assert hhmm["11-04"][1] == "20:44"               #   (no entry 20:45-22:15 UTC, frozen in UTC) closes it first


def test_post_filter_applies_the_local_window_to_rule_candidates() -> None:
    b1 = _bars_1m("2026-10-26", "2026-10-28")
    arm = A.Arm(id="TLOCAL5", rule="ema_pullback", timeframe="1m",
                params={"stop_usd": 3.0, "target_r": 1.5, "hold_bars": 30, "window_local": LONDON_TO_NY})
    ctx = R.RuleContext(bars=b1.reset_index(drop=True), b1=b1.reset_index(drop=True), blackout_lo=NO[0],
                        blackout_hi=NO[1])
    raw = R.RULE_FUNCS[arm.rule](arm, ctx)
    kept = R.post_filter(arm, ctx, raw)
    close = ctx.close_ts[kept["idx"].to_numpy()]
    m = close.hour * 60 + close.minute
    assert len(kept) and (m >= 8 * 60).all() and (m <= 20 * 60).all()
    early = ctx.close_ts[raw["idx"].to_numpy()]
    assert ((early.hour == 7) & (early.dayofweek < 5)).any()     # the UTC 07:00 hour had candidates, now dropped


@pytest.mark.parametrize("window, why", [
    ({"from": ["Europe/Lndon", "08:00"], "to": ["America/New_York", "16:00"]}, "Europe/Lndon"),
    ({"from": ["Europe/London", "8am"], "to": ["America/New_York", "16:00"]}, "8am"),
    ({"from": ["Europe/London", "08:00"]}, "from.*to"),
])
def test_a_malformed_local_window_fails_when_the_arm_loads(window: dict[str, list[str]], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        _arm(window)


def test_an_arm_with_both_a_utc_and_a_local_window_fails_when_it_loads() -> None:
    with pytest.raises(ValueError, match="window_utc.*window_local"):
        _arm(LONDON_TO_NY, window_utc=["07:00", "20:00"])


@pytest.mark.parametrize("start, end", [("2026-03-02", "2026-04-04"), ("2026-10-19", "2026-11-07")])
def test_s1_and_s2_stay_on_the_local_opens_through_the_gap_weeks(start: str, end: str) -> None:
    b1 = _bars_1m(start, end)
    bars = resample_bars(b1, "5m").reset_index(drop=True)
    ctx = R.RuleContext(bars=bars, b1=b1.reset_index(drop=True), blackout_lo=NO[0], blackout_hi=NO[1])
    s1 = R.session_vwap_fade(ARMS["S1B"], ctx)
    loc = ctx.close_ts[s1["idx"].to_numpy()].tz_convert("Europe/London")
    assert len(s1) and (loc.hour * 60 + loc.minute >= 8 * 60 + 30).all()   # 30 min after 08:00 London, every week
    s2 = R.opening_range_break(ARMS["S2A"], ctx)
    assert len(s2)
    for close in ctx.close_ts[s2["idx"].to_numpy()]:
        opens = [pd.Timestamp(f"{close:%Y-%m-%d} {hm}", tz=tz).tz_convert("UTC")
                 for tz, hm in (("Europe/London", "08:00"), ("America/New_York", "08:30"))]
        # a break closes 4 to 21 bars after its session's local open (3-bar range, then the 18-bar break window)
        assert any(o + pd.Timedelta(minutes=20) <= close <= o + pd.Timedelta(minutes=105) for o in opens), close


def test_s1_gives_no_signal_on_the_bar_closing_at_midnight_utc_in_london_winter() -> None:
    """ARM-SPEC 1.1 "no signal after 20:00 UTC": on GMT the London day is the UTC day, so the 23:55 bar is still in the
    session and closes at 00:00, minute 0 of the next UTC day; it must not pass the 20:00 cut."""
    b1 = _bars_1m("2026-03-02", "2026-03-28")
    bars = resample_bars(b1, "5m").reset_index(drop=True)
    ctx = R.RuleContext(bars=bars, b1=b1.reset_index(drop=True), blackout_lo=NO[0], blackout_hi=NO[1])
    for arm in ("S1A", "S1B"):
        c = R.session_vwap_fade(ARMS[arm], ctx)
        close = ctx.close_ts[c["idx"].to_numpy()]
        start = close - pd.Timedelta(minutes=5)
        assert ((start.hour * 60 + start.minute) <= 19 * 60 + 55).all(), close[start.hour >= 20]

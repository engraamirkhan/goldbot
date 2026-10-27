"""goldbot.research.arm_rules_weekcut: PDC4 (pdc_break 4h) and DOND (donchian_break 1d) under the engine's Friday
weekend cut (ARM-SPEC docs/research/arms-2026-10-25.md 8.15, the way 8.14 (2) did it for WFRI): "the time exit is the
earlier of the frozen hold and the end of the week ... Losers close at the cut, winners hold under the cut's tightened
stop to that time exit. No entry after the cut". The cut itself is unchanged; only these arms' exits change."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import exploration
from goldbot.engine.runner import EngineConfig
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_weekcut as W
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
ENG: Any = SimpleNamespace(cfg=SimpleNamespace(weekend_cut="21:30", server_tz="Europe/Athens"))
FROZEN = {("pdc_break", "4h"): 32, ("donchian_break", "1d"): 10}


def _arm(rule: str, tf: str, arm_id: str = "X") -> A.Arm:
    hold = FROZEN.get((rule, tf), 32 if rule == "pdc_break" else 24)
    return A.Arm(id=arm_id, rule=rule, timeframe=tf,  # type: ignore[arg-type]
                 params={"stop_atr": 1.0, "target_r": 2.0, "hold_bars": hold} if rule == "pdc_break"
                 else {"hold_bars": hold})


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-01-02", "2023-12-29", ticks_per_minute=1, seed=11))


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def test_pdc4_and_dond_declare_the_friday_cut_and_pdc1_and_don4_do_not() -> None:
    assert R.follows_friday_cut(_arm("pdc_break", "4h")) and R.follows_friday_cut(_arm("donchian_break", "1d"))
    assert not R.follows_friday_cut(_arm("pdc_break", "1h")) and not R.follows_friday_cut(_arm("donchian_break", "4h"))


def test_the_cut_constants_are_the_engines() -> None:
    f = EngineConfig.model_fields
    assert f["server_tz"].default == W.CUT_TZ and f["weekend_cut"].default == "%02d:%02d" % W.CUT


@pytest.mark.parametrize(("rule", "tf"), sorted(FROZEN))
def test_pdc4_and_dond_enter_monday_to_thursday_where_the_frozen_hold_was_refused(b1, rule, tf) -> None:
    """Every candidate of the real rule passes the engine's weekend check with its capped hold; with the frozen hold
    the same entries were all refused (the reason the arms could never fill)."""
    ctx = _ctx(b1, tf)
    got = R.RULE_FUNCS[rule](_arm(rule, tf), ctx)
    assert len(got) > 5, "the fixture must give candidates, else the checks are vacuous"
    close = ctx.close_ts[got["idx"].to_numpy()]
    hold_s = (got["max_bars"].to_numpy() + 1) * tf_seconds(tf)
    for t, h in zip(close, hold_s, strict=True):
        assert not exploration.crosses_friday(ENG, t, int(h), follows_cut=True), t
        assert exploration.crosses_friday(ENG, t, FROZEN[(rule, tf)] * tf_seconds(tf)), t
    srv_day = set(close.tz_convert(W.CUT_TZ).dayofweek)
    assert (pd.DatetimeIndex(got["cut_at"]).tz_convert(W.CUT_TZ).dayofweek == 4).all()
    assert (close < pd.DatetimeIndex(got["cut_at"])).all()      # nothing decided at or after the week's cut
    if tf == "4h":
        assert {0, 1, 2, 3} <= srv_day                          # Monday to Thursday all enter
    else:                                                       # ~1 a month: the week test covers every weekday
        assert srv_day <= {0, 1, 2, 3}                          # Friday's settlement leaves no 1d bar in the week


@pytest.mark.parametrize("tf", ["4h", "1d"])
def test_no_capped_hold_reaches_the_weekend_in_any_week_of_the_year(tf) -> None:
    """Both DST regimes and the US/EU mismatch weeks: the time exit lands before the server's Saturday 00:00 less the
    slack, never after the cut's week; an entry at or after the cut, or with no bar left, gets no hold (no entry)."""
    step = pd.Timedelta(seconds=tf_seconds(tf))
    close = pd.date_range("2025-01-06 00:00", "2025-12-27 00:00", freq="1h", tz="UTC")
    frozen = FROZEN[("pdc_break", "4h") if tf == "4h" else ("donchian_break", "1d")]
    hold = W.week_hold(close, np.full(len(close), frozen), tf)
    cut, saturday = W.week_bounds(close)
    end = close + hold * step
    on = hold > 0
    assert (end[on] <= saturday[on] - W.WEEK_END_SLACK).all()
    assert not any(exploration.crosses_friday(ENG, t, int(h) * tf_seconds(tf), follows_cut=True)
                   for t, h in zip(close[on][::7], hold[on][::7], strict=True))
    assert (hold[np.asarray(close >= cut)] == 0).all() and (hold <= frozen).all()
    srv = close.tz_convert(W.CUT_TZ)
    weekday = np.asarray((srv.dayofweek < 4) & (srv.hour >= 2) & (srv.hour <= 22))
    assert (hold[weekday] > 0).all()                             # Monday to Thursday always has a hold
    # 128 h (PDC4) and 10 days (DOND) are longer than the trading week: the week's end always binds, so a Monday
    # morning entry holds to Friday (8.15 declares it: the frozen hold never reaches its own end)
    monday = np.asarray((srv.dayofweek == 0) & (srv.hour >= 2) & (srv.hour < 6))
    assert (hold[monday] < frozen).all() and (hold[monday] >= (28 if tf == "4h" else 4)).all()


def test_the_cut_falls_on_friday_2130_server_in_both_dst_regimes() -> None:
    close = pd.DatetimeIndex(["2025-01-07 08:00", "2025-03-11 08:00", "2025-07-08 08:00", "2025-10-28 08:00"],
                             tz="UTC")
    cut, saturday = W.week_bounds(close)
    srv = cut.tz_convert(W.CUT_TZ)
    assert (srv.dayofweek == 4).all() and (srv.hour == 21).all() and (srv.minute == 30).all()
    sat = saturday.tz_convert(W.CUT_TZ)
    assert (sat.dayofweek == 5).all() and (sat.hour == 0).all() and ((saturday - cut) == pd.Timedelta("2h30min")).all()


# ---------------------------------------------------------------- the history walk under the cut
def _path(prices: list[float], start: str = "2024-01-12 19:00") -> pd.DataFrame:
    p = np.asarray(prices, dtype=float)
    ts = pd.date_range(start, periods=len(p), freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": p, "high": p + 0.1, "low": p - 0.1, "close": p})


CUT_AT, EXIT_AT = pd.Timestamp("2024-01-12 19:30", tz="UTC"), pd.Timestamp("2024-01-12 21:30", tz="UTC")


@pytest.mark.parametrize("side", [1, -1])
def test_the_cut_still_closes_a_loser_of_either_side_at_the_cut(side) -> None:
    w = W.walk_cut(_path([100.0 - side * 0.5] * 200), 100.0, side, 3.0, CUT_AT, EXIT_AT, 2.0)
    assert w["kind"] == "weekend_loser" and w["exit"] == 100.0 - side * 0.5 and w["at"] == CUT_AT


@pytest.mark.parametrize("side", [1, -1])
def test_a_winner_of_either_side_holds_under_the_tightened_stop_to_the_time_exit(side) -> None:
    w = W.walk_cut(_path([100.0 + side * 4.0] * 200), 100.0, side, 3.0, CUT_AT, EXIT_AT, 2.0)
    assert w["kind"] == "time" and w["at"] == EXIT_AT and w["stop"] == 100.0 + side * 2.0
    fade = _path([100.0 + side * 4.0] * 40 + [100.0 + side * 1.0] * 160)
    w = W.walk_cut(fade, 100.0, side, 3.0, CUT_AT, EXIT_AT, 2.0)
    assert w["kind"] == "stop" and w["exit"] == 100.0 + side * 1.0 and w["stop"] == 100.0 + side * 2.0


def test_before_the_cut_the_frozen_stop_and_target_hold() -> None:
    w = W.walk_cut(_path([101.0] * 5 + [106.5] * 195), 100.0, 1, 3.0, CUT_AT, EXIT_AT, 2.0)
    assert w["kind"] == "target" and w["exit"] == 106.0 and w["stop"] == 97.0
    w = W.walk_cut(_path([99.0] * 5 + [96.5] * 195), 100.0, 1, 3.0, CUT_AT, EXIT_AT, 2.0)
    assert w["kind"] == "stop" and w["exit"] == 96.5 and w["at"] < CUT_AT


def test_a_hold_that_ends_before_the_cut_never_meets_it() -> None:
    w = W.walk_cut(_path([99.0] * 200), 100.0, 1, 3.0, EXIT_AT + pd.Timedelta(hours=1), CUT_AT, 2.0)
    assert w["kind"] == "time" and w["at"] == CUT_AT and w["stop"] == 97.0


def test_the_engine_time_exit_is_the_signal_close_plus_the_capped_hold() -> None:
    signal = pd.Timestamp("2025-03-04 20:00", tz="UTC")
    assert W.time_exit_at(signal, 15 * 4 * 3600) == pd.Timestamp("2025-03-07 08:00", tz="UTC")
    assert W.time_exit_at(None, 3600) is None

"""goldbot.research.arm_rules_levels `level_break`: H-71 G-1, the Asia-hours break of a prior-period level on 1h
(G1A) and its random-distance control (G1C), docs/research/prereg-2026-10-26-time-structure.md section 3. "A decision
on the close of bar i uses bars <= i only ... Levels exist from the bar that confirms them": previous-day, session,
previous-week and previous-month levels are known only after their period closes; appending bars never changes a past
candidate; the engine's window, extended with stored bars, decides as the history run."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_levels as L
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
CUT = pd.Timestamp("2024-01-10", tz="UTC")
LEVELS = ["pdh", "pdl", "pdc", "asia_hl", "london_hl", "ny_hl", "pwh", "pwl", "pmh", "pml",
          "round_10_50_100_within_3atr"]
G1: dict[str, Any] = {"levels": LEVELS, "level_window_local": [["17:00", "America/New_York"], ["08:00", "Europe/London"]],
                      "break_atr": 0.1, "stop_atr": 1.0, "target_r": 2.0, "hold_bars": 32, "one_per_day": True,
                      "min_stop_usd": 3.0, "max_stop_usd": 22.6}


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-08-01", "2024-01-31", ticks_per_minute=1, seed=11))


def _arm(arm_id: str = "G1A", **extra: Any) -> A.Arm:
    return A.Arm(id=arm_id, rule="level_break", timeframe="1h", params={**G1, **extra})


def _ctx(b1: pd.DataFrame) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, "1h").reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


ARMS: tuple[tuple[str, dict[str, Any]], ...] = (("G1A", {}), ("G1C", {"control": "random_distance_level"}))


def test_level_break_is_a_registered_arm_rule() -> None:
    assert "level_break" in A.RULES and "level_break" in R.RULE_FUNCS


@pytest.mark.parametrize(("arm_id", "extra"), ARMS)
def test_appending_bars_never_changes_a_past_candidate(b1, arm_id, extra) -> None:
    arm = _arm(arm_id, **extra)
    full_ctx = _ctx(b1)
    full = R.RULE_FUNCS["level_break"](arm, full_ctx)
    part = R.RULE_FUNCS["level_break"](arm, _ctx(b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)]))
    assert len(part) > 3, "the fixture must give candidates, else the check is vacuous"
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


def test_a_break_closes_a_tenth_of_an_atr_beyond_a_level_its_bar_touched_inside_the_asia_window(b1) -> None:
    """3.1: "the first 1h bar of day D in the window whose range touches a level and whose close is >= 0.1 x
    ATR14(1h) beyond it. Side = the break direction"; stop = level - side x 1.0 x ATR14(1h); one trade a day."""
    ctx = _ctx(b1)
    m = ctx.mid
    ev = L.level_events(m, ctx.b1, _arm())
    assert len(ev) > 10
    idx = ev["idx"].to_numpy(dtype=int)
    h, lo, c = (m[k].to_numpy(dtype=float)[idx] for k in ("high", "low", "close"))
    lvl, side, a = ev["level"].to_numpy(), ev["side"].to_numpy(), ev["atr"].to_numpy()
    assert ((lo <= lvl) & (lvl <= h)).all()
    assert (side * (c - lvl) >= 0.1 * a - 1e-9).all()
    np.testing.assert_allclose(a, atr(m, 14).to_numpy()[idx - 1])            # ATR known before the touch bar
    start = utc_index(m["ts_utc"])[idx]
    days = L.trading_day(start)
    assert len(set(days)) == len(days), "at most one trade per trading day"
    end = pd.DatetimeIndex([pd.Timestamp(np.datetime64(int(d), "D")).replace(hour=8).tz_localize("Europe/London")
                            for d in days]).tz_convert("UTC")
    assert (start < end).all()                          # opens between 17:00 New York (its day's start) and 08:00 London
    got = R.RULE_FUNCS["level_break"](_arm(), ctx)
    np.testing.assert_allclose(got["risk_usd"], np.abs(c - (lvl - side * a)))
    assert set(got["target_r"]) == {2.0} and set(got["max_bars"]) == {31}


def test_previous_week_and_month_levels_are_known_only_after_their_period_closes(b1) -> None:
    """Previous-week high/low is fixed at Friday 17:00 New York, the previous month's at its last trading day's
    close: no bar of the current week or month moves them."""
    ctx = _ctx(b1)
    m = ctx.mid
    table = L.day_levels(m, ctx.b1, LEVELS)
    day = L.trading_day(utc_index(m["ts_utc"]))
    hi = pd.Series(m["high"].to_numpy(float)).groupby(day).max()
    lo = pd.Series(m["low"].to_numpy(float)).groupby(day).min()
    d = pd.Timestamp("2024-01-17")                                          # a Wednesday: previous week 8-12 Jan
    k = int(np.datetime64(d.date(), "D").astype(np.int64))
    week = [int(np.datetime64(f"2024-01-{x:02d}", "D").astype(np.int64)) for x in range(8, 13)]
    month = [x for x in hi.index if np.datetime64(int(x), "D").astype("datetime64[M]") == np.datetime64("2023-12")]
    lv = dict(table[k]["levels"])
    assert lv["pwh"] == hi.loc[week].max() and lv["pwl"] == lo.loc[week].min()
    assert lv["pmh"] == hi.loc[month].max() and lv["pml"] == lo.loc[month].min()
    # change every bar of the current week and month: the levels do not move
    moved = m.copy()
    cur = np.asarray(day >= int(np.datetime64("2024-01-15", "D").astype(np.int64)))
    for col in ("open", "high", "low", "close"):
        moved.loc[cur, col] = moved.loc[cur, col] + 500.0
    assert dict(L.day_levels(moved, ctx.b1, LEVELS)[k]["levels"])["pwh"] == lv["pwh"]


def test_an_input_that_does_not_hold_the_whole_previous_month_gives_no_month_level(b1) -> None:
    """Point in time and fail closed: a period the input holds only part of has no level (never a partial high)."""
    short = b1[np.asarray(utc_index(b1["ts_utc"]) >= pd.Timestamp("2024-01-03", tz="UTC"))]
    ctx = _ctx(short)
    table = L.day_levels(ctx.mid, ctx.b1, LEVELS)
    k = int(np.datetime64("2024-01-10", "D").astype(np.int64))
    names = {n for n, _ in table[k]["levels"]}
    assert not names & {"pmh", "pml", "pwh", "pwl"}
    assert {"pdh", "pdl", "pdc"} <= names


def test_only_the_listed_level_classes_are_traded(b1) -> None:
    ctx = _ctx(b1)
    ev = L.level_events(ctx.mid, ctx.b1, _arm(levels=["pdh", "pdl"]))
    assert len(ev) > 0 and set(ev["cls"]) <= {"pdh", "pdl"}


def test_the_control_breaks_one_hashed_random_distance_level_a_day_on_the_same_window_and_bracket(b1) -> None:
    """3.2 G1C: "at 17:00 New York each day, draw a level class uniformly from the day's real levels and a sign from
    the frozen hash (arm id G1C). Control level = 17:00 price + sign x |17:00 price - that level|"."""
    ctx = _ctx(b1)
    m = ctx.mid
    arm = _arm("G1C", control="random_distance_level")
    ev = L.level_events(m, ctx.b1, arm)
    assert len(ev) > 5
    table = L.day_levels(m, ctx.b1, LEVELS)
    days = L.trading_day(utc_index(m["ts_utc"])[ev["idx"].to_numpy(dtype=int)])
    for k, level, cls in zip(days, ev["level"].to_numpy(dtype=float), ev["cls"], strict=True):
        ref = table[int(k)]["ref"]
        assert any(abs(abs(level - ref) - abs(v - ref)) < 1e-9 for _, v in table[int(k)]["levels"])
        assert cls == "control"
    real = L.level_events(m, ctx.b1, _arm())
    assert not real.merge(ev, on=["idx", "side", "level"]).shape[0] == len(ev)       # not the real levels


def test_a_foreign_window_or_level_class_is_refused() -> None:
    with pytest.raises(ValueError, match="level"):
        R.RULE_FUNCS["level_break"](_arm(levels=["pdh", "weekly_pivot"]),
                                    _ctx(ticks_to_1m(synthetic_ticks("2024-01-02", "2024-01-04", seed=1))))
    with pytest.raises(ValueError, match="17:00"):
        R.RULE_FUNCS["level_break"](_arm(level_window_local=[["18:00", "America/New_York"], ["08:00", "Europe/London"]]),
                                    _ctx(ticks_to_1m(synthetic_ticks("2024-01-02", "2024-01-04", seed=1))))


def test_the_rule_declares_the_stored_history_it_needs_beyond_the_engines_window() -> None:
    """The previous month's high needs up to two calendar months of 1h bars, more than the engine's ~40-day 1m memory:
    the engine extends the window from stored bars (`exploration_context`)."""
    from goldbot.engine.exploration_context import history_bars
    assert history_bars(_arm()) * 3600 > 62 * 86400 * 5 / 7

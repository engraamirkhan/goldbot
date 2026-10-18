"""ADR 0031: adaptive risk limits. The frozen rules: limits loosen only on demo fills after each strategy's freeze,
one step at a time, at the risk-day roll, after 3 qualifying days and 10 new demo trades, never within 5 days of a
tighten; they tighten at once (any time, may skip steps) on drawdown, a loss streak, costs, a size-down stage or a
weak statistic; they never leave [floor, ceiling]; a live account never rises above the defaults; anything missing or
corrupt gives the floors and a dq_event."""
from __future__ import annotations

import math
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.db.migrations import CORE, compat_back_to
from goldbot.risk.adaptive import (
    FIELDS,
    AdaptiveController,
    Evidence,
    LimitBounds,
    Limits,
    LimitsState,
    LimitsStore,
    TradeEvidence,
    changes_between,
    combined_lcb,
    daily_lcb,
    quarter_kelly_q,
    step,
)
from goldbot.risk.gate import Stage

D0 = date(2026, 11, 2)                          # a Monday
FREEZE_NS = int(pd.Timestamp("2026-10-01", tz="UTC").value)

FLOOR = Limits(daily_cap=0.02, weekly_cap=0.05, supervisor_daily_cap=0.015, supervisor_weekly_cap=0.04,
               min_lot_risk_cap=None)
DEMO_CEILING = Limits(daily_cap=0.05, weekly_cap=0.10, supervisor_daily_cap=0.0375, supervisor_weekly_cap=0.075,
                      min_lot_risk_cap=0.01)
DEMO = LimitBounds.build(FLOOR, DEMO_CEILING, mode="demo", supervisor_all_demo=True)
LIVE = LimitBounds.build(FLOOR, DEMO_CEILING, mode="live", supervisor_all_demo=False)


def _ns(day: date, hour: int = 12, minute: int = 0) -> int:
    return int(pd.Timestamp(day, tz="UTC").value) + (hour * 60 + minute) * 60 * 10**9


_seq = iter(range(10**9))


def _t(day: date, r: float, *, strategy: str = "A", source: str = "demo", hour: int = 12, exc: bool = False,
       minute: int | None = None) -> TradeEvidence:
    n = next(_seq)
    return TradeEvidence(id=f"{source}-{strategy}-{n}", strategy=strategy, source=source,  # type: ignore[arg-type]
                         exit_ns=_ns(day, hour, n % 60 if minute is None else minute), net_r=r,
                         min_lot_exception=exc)


def _good_days(first: date, days: int, *, strategy: str = "A", source: str = "demo", exc: bool = False,
               per_day: int = 2) -> list[TradeEvidence]:
    """A clear edge with day-to-day variation: daily sums alternate 1.0 and 0.5 R."""
    out: list[TradeEvidence] = []
    for i in range(days):
        rs = [1.5, -0.5] if i % 2 == 0 else [1.0, -0.5]
        out += [_t(first + timedelta(days=i), r, strategy=strategy, source=source, exc=exc) for r in rs[:per_day]]
    return out


def _bad_days(first: date, days: int, *, strategy: str = "A", source: str = "demo") -> list[TradeEvidence]:
    """No edge: daily sums alternate -0.5 and -0.2 R (never four full losses in a row)."""
    out: list[TradeEvidence] = []
    for i in range(days):
        rs = [0.5, -1.0] if i % 2 == 0 else [0.3, -0.5]
        out += [_t(first + timedelta(days=i), r, strategy=strategy, source=source) for r in rs]
    return out


def _ev(trades: list[TradeEvidence], day: date, *, hour: int = 0, minute: int = 5, dd: float = 0.0,
        stage: Stage = Stage.NORMAL, cost: float | None = 1.0, atr_med: float | None = 5.0,
        atr_p90: float | None = 8.0, freeze: dict[str, int] | None = None) -> Evidence:
    return Evidence(account_id="icm-demo", as_of_ns=_ns(day, hour, minute), trades=tuple(t for t in trades if t.exit_ns <= _ns(day, hour, minute)),
                    freeze_ns=freeze if freeze is not None else {"A": FREEZE_NS, "B": FREEZE_NS},
                    drawdown=dd, stage=stage, cost_ratio_20d=cost, atr15_median_20d=atr_med, atr15_p90_1y=atr_p90)


def _at(limits: Limits, **kw: float | None) -> Limits:
    return limits.model_copy(update=kw)


def _state(limits: Limits, day: date, **kw: object) -> LimitsState:
    return LimitsState(account_id="icm-demo", day=day, limits=limits, **kw)  # type: ignore[arg-type]


def _run(trades: list[TradeEvidence], first: date, days: int, prev: LimitsState | None = None,
         bounds: LimitBounds = DEMO, **ev_kw: object) -> list[LimitsState]:
    """Evaluate at each risk-day roll from `first` for `days` days; returns the state after each."""
    out: list[LimitsState] = []
    for i in range(days):
        d = first + timedelta(days=i)
        prev, _ = step(prev, _ev(trades, d, **ev_kw), d, bounds)  # type: ignore[arg-type]
        out.append(prev)
    return out


# ---- the statistic --------------------------------------------------------------------------------------------------

def test_the_statistic_is_on_daily_sums_so_correlated_same_day_trades_do_not_inflate_n():
    base = _good_days(D0, 30)
    s = daily_lcb(base)
    assert s is not None and s.n_days == 30 and s.n_trades == 60
    sums = np.array([1.0 if i % 2 == 0 else 0.5 for i in range(30)])
    want = sums.mean() - stats.t.ppf(0.80, 29) * sums.std(ddof=1) / math.sqrt(30)
    assert s.lcb == pytest.approx(want)
    # every trade taken twice the same day: 120 trades, still 30 days, and the LCB scales exactly x2 (a per-trade
    # statistic would see n=120 and shrink the standard error)
    doubled = base + [t.model_copy(update={"id": t.id + "-twin"}) for t in base]
    s2 = daily_lcb(doubled)
    assert s2 is not None and s2.n_days == 30 and s2.n_trades == 120
    assert s2.lcb == pytest.approx(2 * want)


def test_a_strategy_with_fewer_than_two_days_has_no_evidence():
    assert daily_lcb([_t(D0, 1.0), _t(D0, 2.0)]) is None


def test_strategies_combine_by_the_minimum_per_strategy_lcb():
    a, b = _good_days(D0, 30, strategy="A"), _bad_days(D0, 30, strategy="B")
    la, lb = daily_lcb(a), daily_lcb(b)
    assert la is not None and lb is not None and lb.lcb < 0 < la.lcb
    assert combined_lcb(a + b) == pytest.approx(lb.lcb)


def test_quarter_kelly_uses_the_per_trade_equivalent_lcb_and_clamps_q_to_0_1():
    trades = _good_days(D0, 30)
    s = daily_lcb(trades)
    assert s is not None
    e_r2 = float(np.mean([t.net_r ** 2 for t in trades]))
    daily_fraction = (s.lcb / 2.0) / (4 * e_r2) * 2.0           # f = (LCB / trades_per_day) / 4E[r2], x trades/day
    assert quarter_kelly_q(trades) == pytest.approx(min(1.0, daily_fraction / 0.03))
    assert quarter_kelly_q(_bad_days(D0, 30)) == 0.0
    assert quarter_kelly_q([]) == 0.0


# ---- loosening ------------------------------------------------------------------------------------------------------

def test_insufficient_demo_trades_keep_the_floors():
    few = _good_days(D0, 29) + [_t(D0 + timedelta(days=29), 1.0)]          # 59 demo trades, a strong edge
    states = _run(few, D0 + timedelta(days=30), 10)
    assert all(s.limits == FLOOR for s in states)


def test_a_contributing_strategy_with_fewer_than_20_demo_trades_blocks_loosening():
    trades = _good_days(D0, 30, strategy="A") + _good_days(D0, 9, strategy="B")    # B: 18 trades
    states = _run(trades, D0 + timedelta(days=30), 10)
    assert all(s.limits == FLOOR for s in states)


def test_loosening_needs_three_days_and_ten_new_trades_and_moves_one_step():
    trades = _good_days(D0, 30)                                           # 60 demo trades, strong edge
    start = D0 + timedelta(days=30)
    s1, s2, s3 = _run(trades, start, 3)
    assert s1.limits == FLOOR and s2.limits == FLOOR
    assert s3.limits.daily_cap == pytest.approx(0.025) and s3.limits.weekly_cap == pytest.approx(0.06)
    assert s3.limits.supervisor_daily_cap == pytest.approx(0.015 + 0.0225 / 6)
    assert s3.limits.supervisor_weekly_cap == pytest.approx(0.04 + 0.035 / 5)
    # three more qualifying days but no new demo trade: no second step
    s6 = _run(trades, start + timedelta(days=3), 3, prev=s3)[-1]
    assert s6.limits == s3.limits
    # 9 new trades are not enough, the 10th is
    more = trades + [_t(start + timedelta(days=5), 0.6) for _ in range(9)]
    assert _run(more, start + timedelta(days=6), 1, prev=s6)[-1].limits == s3.limits
    more.append(_t(start + timedelta(days=6), 0.6))
    s8 = _run(more, start + timedelta(days=7), 1, prev=s6)[-1]
    assert s8.limits.daily_cap == pytest.approx(0.03) and s8.limits.weekly_cap == pytest.approx(0.07)


def test_a_day_that_falls_short_resets_the_three_day_count():
    trades = _good_days(D0, 30)
    start = D0 + timedelta(days=30)
    s1, s2 = _run(trades, start, 2)
    # day 3: losing shadow trades hold the target at the floors (no limit moves, so no tighten) and reset the count
    shadow = [_t(D0 + timedelta(days=i), r, source="shadow") for i in range(30) for r in (-1.0, -0.5, 0.2)]
    d3 = start + timedelta(days=2)
    s3, reasons = step(s2, _ev(trades + shadow, d3), d3, DEMO)
    assert s3.up_streak["daily_cap"] == 0 and s3.tightened_day is None and s3.limits == FLOOR
    s4, s5 = _run(trades, start + timedelta(days=3), 2, prev=s3)
    assert s5.limits == FLOOR and s5.up_streak["daily_cap"] == 2           # only 2 qualifying days since
    assert _run(trades, start + timedelta(days=5), 1, prev=s5)[-1].limits.daily_cap == pytest.approx(0.025)


def test_loosening_happens_only_at_the_risk_day_roll_never_intraday():
    trades = _good_days(D0, 30)
    start = D0 + timedelta(days=30)
    s1, s2 = _run(trades, start, 2)
    d = start + timedelta(days=1)
    for hour in (3, 9, 15, 21):                                             # same risk day, later evaluations
        s2, _ = step(s2, _ev(trades, d, hour=hour), d, DEMO)
    assert s2.limits == FLOOR and s2.up_streak["daily_cap"] == 2


def test_the_ceiling_is_never_exceeded_and_the_floor_never_undershot():
    trades = _good_days(D0, 30)
    prev: LimitsState | None = None
    seen: list[Limits] = []
    for i in range(80):
        d = D0 + timedelta(days=30 + i)
        trades += [_t(d - timedelta(days=1), r) for r in (1.5, -0.5, 1.0, -0.5, 1.2)]
        prev, _ = step(prev, _ev(trades, d), d, DEMO)
        seen.append(prev.limits)
    assert seen[-1] == DEMO_CEILING.model_copy(update={"min_lot_risk_cap": None})
    for lim in seen:
        for f in FIELDS:
            v, lo, hi = getattr(lim, f), getattr(FLOOR, f), getattr(DEMO_CEILING, f)
            if f == "min_lot_risk_cap":
                assert v is None or v <= hi + 1e-12
            else:
                assert lo - 1e-12 <= v <= hi + 1e-12


def test_random_evidence_never_leaves_the_bounds():
    rng = random.Random(7)
    prev: LimitsState | None = None
    trades: list[TradeEvidence] = []
    for i in range(200):
        d = D0 + timedelta(days=i)
        trades += [_t(d - timedelta(days=1), rng.uniform(-1.2, 2.0), strategy=rng.choice("AB"),
                      source=rng.choice(("demo", "demo", "shadow")), exc=rng.random() < 0.3)
                   for _ in range(rng.randint(0, 4))]
        ev = _ev(trades, d, hour=rng.choice((0, 12)), dd=rng.choice((0.0, 0.0, 0.0, 0.05)),
                 cost=rng.choice((1.0, 1.0, 1.15, 1.3, None)), atr_med=rng.uniform(3, 9))
        prev, _ = step(prev, ev, d, DEMO)
        lim = prev.limits
        assert FLOOR.daily_cap <= lim.daily_cap <= DEMO_CEILING.daily_cap + 1e-12
        assert FLOOR.weekly_cap <= lim.weekly_cap <= DEMO_CEILING.weekly_cap + 1e-12
        assert FLOOR.supervisor_daily_cap <= lim.supervisor_daily_cap <= DEMO_CEILING.supervisor_daily_cap + 1e-12
        assert FLOOR.supervisor_weekly_cap <= lim.supervisor_weekly_cap <= DEMO_CEILING.supervisor_weekly_cap + 1e-12
        assert lim.min_lot_risk_cap is None or lim.min_lot_risk_cap <= 0.01 + 1e-12


def test_a_live_account_never_rises_above_the_defaults():
    trades = _good_days(D0, 40)
    states = _run(trades, D0 + timedelta(days=40), 30, bounds=LIVE)
    assert all(s.limits == FLOOR for s in states)
    # a state loosened on demo, read under live bounds, is clamped to the defaults at once
    loose = _state(_at(DEMO_CEILING), D0 + timedelta(days=70))
    d = D0 + timedelta(days=70)
    s, reasons = step(loose, _ev(trades, d, hour=10), d, LIVE)
    assert s.limits == FLOOR and "bounds" in reasons and s.tightened_day == d          # a drop starts the cooldown


def test_live_bounds_equal_the_defaults_whatever_the_demo_ceilings():
    assert LIVE.ceiling == FLOOR
    assert LimitBounds.build(FLOOR, DEMO_CEILING, mode="demo", supervisor_all_demo=False).ceiling == \
        DEMO_CEILING.model_copy(update={"supervisor_daily_cap": 0.015, "supervisor_weekly_cap": 0.04})


def test_a_ceiling_below_the_floor_is_the_floor():
    low = _at(FLOOR, daily_cap=0.01, weekly_cap=0.03)
    b = LimitBounds.build(FLOOR, low, mode="demo", supervisor_all_demo=True)
    assert b.ceiling.daily_cap == 0.02 and b.ceiling.weekly_cap == 0.05


# ---- tightening -----------------------------------------------------------------------------------------------------

def _loose(day: date) -> LimitsState:
    return _state(_at(DEMO_CEILING), day)


LOOSE_TRADES = _good_days(D0, 40, exc=True)       # supports every field at its ceiling (min-lot included)


def test_tightening_is_immediate_intraday_and_skips_steps():
    d = D0 + timedelta(days=40)
    trades = list(LOOSE_TRADES)
    s, reasons = step(_loose(d), _ev(trades, d, hour=14, dd=0.05), d, DEMO)
    assert s.limits == FLOOR and "dd_floor" in reasons and s.tightened_day == d


def test_drawdown_of_exactly_4_percent_gives_the_floors_and_just_under_does_not():
    d = D0 + timedelta(days=40)
    trades = list(LOOSE_TRADES)
    assert step(_loose(d), _ev(trades, d, hour=14, dd=0.04), d, DEMO)[0].limits == FLOOR
    assert step(_loose(d), _ev(trades, d, hour=14, dd=0.0399), d, DEMO)[0].limits == _at(DEMO_CEILING)


def test_cost_ratio_of_exactly_1_25_steps_down_once_and_above_gives_the_floors():
    d = D0 + timedelta(days=40)
    trades = list(LOOSE_TRADES)
    prev = _loose(d - timedelta(days=1))
    one_down, reasons = step(prev, _ev(trades, d, cost=1.25), d, DEMO)
    assert one_down.limits.daily_cap == pytest.approx(0.045) and one_down.limits.weekly_cap == pytest.approx(0.09)
    assert one_down.limits.min_lot_risk_cap == pytest.approx(0.008) and "cost_step" in reasons
    assert step(prev, _ev(trades, d, cost=1.2501), d, DEMO)[0].limits == FLOOR
    assert step(prev, _ev(trades, d, cost=1.10), d, DEMO)[0].limits.daily_cap == pytest.approx(0.045)
    assert step(prev, _ev(trades, d, cost=1.0999), d, DEMO)[0].limits == _at(DEMO_CEILING)
    # the one-step rule applies once per risk day: a later evaluation the same day does not step again
    again, _ = step(one_down, _ev(trades, d, hour=15, cost=1.25), d, DEMO)
    assert again.limits == one_down.limits


def test_four_consecutive_losses_give_the_floors_today_and_tomorrow():
    d = D0 + timedelta(days=40)
    trades = list(LOOSE_TRADES) + [_t(d, -0.8, hour=9, minute=m) for m in range(4)]
    s, reasons = step(_loose(d), _ev(trades, d, hour=10), d, DEMO)
    assert s.limits == FLOOR and "loss_streak_floor" in reasons and s.floors_until == d + timedelta(days=1)
    nxt = d + timedelta(days=1)
    trades.append(_t(nxt, 2.0, hour=1))                                     # the streak is broken...
    s2, reasons2 = step(s, _ev(trades, nxt, hour=2), nxt, DEMO)
    assert s2.limits == FLOOR and "floors_until" in reasons2                  # ...but tomorrow stays at the floors


def test_three_losses_or_a_loss_above_minus_0_8_do_not_trigger_the_streak():
    d = D0 + timedelta(days=40)
    trades = list(LOOSE_TRADES) + [_t(d, -1.0, hour=9, minute=m) for m in range(3)] + [_t(d, -0.79, hour=9, minute=5)]
    s, _ = step(_loose(d), _ev(trades, d, hour=10), d, DEMO)
    assert s.limits == _at(DEMO_CEILING)


def test_size_down_forces_the_floors_until_cleared():
    trades = _good_days(D0, 30)
    start = D0 + timedelta(days=30)
    for stage in (Stage.SIZE_DOWN, Stage.HALTED):
        s, reasons = step(_loose(start), _ev(trades, start, hour=8, stage=stage), start, DEMO)
        assert s.limits == FLOOR and "stage_floor" in reasons
    held = _run(trades, start, 10, prev=_loose(start - timedelta(days=1)), stage=Stage.SIZE_DOWN)
    assert all(s.limits == FLOOR for s in held)
    # cleared: the cooldown (5 days) and three qualifying days later it loosens again
    after = _run(trades, start + timedelta(days=10), 6, prev=held[-1])
    assert all(s.limits == FLOOR for s in after[:5]) and after[5].limits.daily_cap == pytest.approx(0.025)


def test_cooldown_blocks_loosening_within_5_days_of_a_tighten():
    trades = _good_days(D0, 30)
    start = D0 + timedelta(days=30)
    t_day = start + timedelta(days=1)
    s0, _ = step(None, _ev(trades, start), start, DEMO)
    s1, _ = step(s0, _ev(trades, t_day, hour=10, dd=0.05), t_day, DEMO)     # a tighten with limits at the floors
    assert s1.tightened_day == t_day
    after = _run(trades, t_day + timedelta(days=1), 5, prev=s1)            # days +1..+5: blocked
    assert all(s.limits == FLOOR for s in after) and after[-1].up_streak["daily_cap"] >= 3
    s6 = _run(trades, t_day + timedelta(days=6), 1, prev=after[-1])[-1]
    assert s6.limits.daily_cap == pytest.approx(0.025)


def test_a_weak_statistic_tightens_to_its_target_at_once():
    trades = _good_days(D0, 30, strategy="A") + _bad_days(D0, 30, strategy="B")
    d = D0 + timedelta(days=31)
    s, reasons = step(_loose(d), _ev(trades, d, hour=9), d, DEMO)
    assert s.limits == FLOOR and "target_below" in reasons


# ---- shadow ---------------------------------------------------------------------------------------------------------

def test_shadow_trades_never_loosen():
    trades = _good_days(D0, 60, source="shadow")
    assert all(s.limits == FLOOR for s in _run(trades, D0 + timedelta(days=60), 20))


def test_shadow_trades_can_tighten():
    demo = _good_days(D0, 30, strategy="A", exc=True)
    shadow = [_t(D0 + timedelta(days=i), r, source="shadow") for i in range(30) for r in (-1.0, -0.5, 0.2)]
    d = D0 + timedelta(days=31)
    assert step(_loose(d), _ev(demo, d, hour=9), d, DEMO)[0].limits == _at(DEMO_CEILING)
    s, reasons = step(_loose(d), _ev(demo + shadow, d, hour=9), d, DEMO)
    assert s.limits.daily_cap < DEMO_CEILING.daily_cap and "target_below" in reasons
    # and a shadow loss streak floors the limits
    streak = demo + [_t(d, -1.0, source="shadow", hour=8, minute=m) for m in range(4)]
    assert step(_loose(d), _ev(streak, d, hour=9), d, DEMO)[0].limits == FLOOR


# ---- evidence filters -----------------------------------------------------------------------------------------------

def test_trades_before_the_freeze_date_are_not_evidence():
    trades = _good_days(D0, 30)
    late_freeze = {"A": _ns(D0 + timedelta(days=20))}                       # only the last ~10 days count
    states = _run(trades, D0 + timedelta(days=30), 10, freeze=late_freeze)
    assert all(s.limits == FLOOR for s in states)


def test_trades_closed_on_the_decision_day_are_not_in_the_statistic():
    trades = _good_days(D0, 29) + [_t(D0 + timedelta(days=29), 1.0, hour=0, minute=1),
                                   _t(D0 + timedelta(days=29), 1.0, hour=0, minute=2)]   # 60 only counting today's
    states = _run(trades, D0 + timedelta(days=29), 3)
    # day 29 itself sees 58 demo trades (today's two are after the cut), so the 3-day count starts on day 30
    assert states[0].up_streak["daily_cap"] == 0
    assert states[2].limits == FLOOR and states[2].up_streak["daily_cap"] == 2


# ---- min-lot exception ----------------------------------------------------------------------------------------------

def test_min_lot_cap_stays_off_without_positive_exception_evidence():
    trades = _good_days(D0, 30) + _bad_days(D0, 10, strategy="A", source="demo")
    for t in trades[-20:]:
        trades.append(t.model_copy(update={"id": t.id + "-x", "min_lot_exception": True}))
    states = _run(trades, D0 + timedelta(days=30), 12)
    assert all(s.limits.min_lot_risk_cap is None for s in states)


def test_min_lot_cap_steps_0_6_then_0_8_then_1_0_and_is_forced_off_by_high_atr():
    trades = _good_days(D0, 30, exc=True)
    start = D0 + timedelta(days=30)
    seen: list[float | None] = []
    prev: LimitsState | None = None
    for i in range(12):
        d = start + timedelta(days=i)
        trades += [_t(d - timedelta(days=1), r, exc=True) for r in (1.5, -0.5, 1.0, -0.5, 1.2)]
        prev, _ = step(prev, _ev(trades, d), d, DEMO)
        seen.append(prev.limits.min_lot_risk_cap)
    assert [v for i, v in enumerate(seen) if i == 0 or v != seen[i - 1]] == [None, 0.006, 0.008, 0.01]
    assert prev is not None
    d = start + timedelta(days=11)
    off, reasons = step(prev, _ev(trades, d, hour=13, atr_med=8.0, atr_p90=8.0), d, DEMO)
    assert off.limits.min_lot_risk_cap is None and "atr_minlot_off" in reasons
    assert step(prev, _ev(trades, d, hour=13, atr_med=None), d, DEMO)[0].limits.min_lot_risk_cap is None


# ---- fail closed ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["missing", "nan", "no_freeze", "duplicate", "future_exit"])
def test_corrupt_or_missing_evidence_gives_the_floors(bad: str):
    d = D0 + timedelta(days=40)
    trades = _good_days(D0, 40)
    ev: Evidence | None = _ev(trades, d, hour=9)
    assert ev is not None
    if bad == "missing":
        ev = None
    elif bad == "nan":
        ev = ev.model_copy(update={"trades": (*ev.trades, _t(d, float("nan"), hour=1))})
    elif bad == "no_freeze":
        ev = ev.model_copy(update={"trades": (*ev.trades, _t(d, 0.5, strategy="Z", hour=1))})
    elif bad == "duplicate":
        ev = ev.model_copy(update={"trades": (*ev.trades, ev.trades[0])})
    elif bad == "future_exit":
        ev = ev.model_copy(update={"trades": (*ev.trades, _t(d, 0.5, hour=23))})
    s, reasons = step(_loose(d), ev, d, DEMO)
    assert s.limits == FLOOR and "evidence_invalid" in reasons and s.tightened_day == d


def test_a_decision_day_before_the_last_one_is_invalid_evidence():
    d = D0 + timedelta(days=40)
    s, reasons = step(_loose(d), _ev(_good_days(D0, 40), d - timedelta(days=1)), d - timedelta(days=1), DEMO)
    assert s.limits == FLOOR and "evidence_invalid" in reasons


def test_backtest_or_oof_sources_are_refused_by_the_evidence_record():
    with pytest.raises(ValueError):
        TradeEvidence(id="x", strategy="A", source="backtest", exit_ns=1, net_r=1.0)  # type: ignore[arg-type]


# ---- determinism and persistence ------------------------------------------------------------------------------------

def _replay() -> list[tuple[LimitsState, tuple[str, ...]]]:
    rng = random.Random(11)
    trades: list[TradeEvidence] = []
    prev: LimitsState | None = None
    out = []
    for i in range(60):
        d = D0 + timedelta(days=i)
        trades += [TradeEvidence(id=f"t{i}-{k}", strategy="AB"[k % 2], source="demo", exit_ns=_ns(d - timedelta(days=1), 10, k),
                                 net_r=rng.choice((1.5, 1.0, -0.5, -0.3))) for k in range(4)]
        prev, reasons = step(prev, _ev(trades, d), d, DEMO)
        out.append((prev, reasons))
    return out


def test_replay_from_the_same_inputs_is_deterministic():
    a, b = _replay(), _replay()
    assert a == b
    assert any(s.limits != FLOOR for s, _ in a)                            # the replay does move


def test_changes_record_field_old_new_reasons_and_evidence_ids():
    trades = _good_days(D0, 30)
    s2, s3 = _run(trades, D0 + timedelta(days=30), 3)[1:]
    ev = _ev(trades, D0 + timedelta(days=32))
    _, reasons = step(s2, ev, D0 + timedelta(days=32), DEMO)
    rows = changes_between(s2, s3, reasons, ev, ts_ns=123)
    by_field = {c.field: c for c in rows}
    assert set(by_field) == {"daily_cap", "weekly_cap", "supervisor_daily_cap", "supervisor_weekly_cap"}
    c = by_field["daily_cap"]
    assert c.old == 0.02 and c.new == pytest.approx(0.025) and "loosen" in c.reasons and c.ts_ns == 123
    assert c.account_id == "icm-demo" and c.day == D0 + timedelta(days=32)
    assert set(c.evidence_ids) == {t.id for t in trades}


def test_the_limits_tables_are_an_additive_core_migration():
    names = [m.name for m in CORE]
    assert "adaptive_limits" in names and CORE[names.index("adaptive_limits")].additive
    assert compat_back_to(CORE, len(CORE)) == 1


def test_state_and_changes_round_trip_through_core_db_and_changes_are_append_only(tmp_path: Path):
    store = LimitsStore(tmp_path)
    trades = _good_days(D0, 30)
    states = _run(trades, D0 + timedelta(days=30), 3)
    ev = _ev(trades, D0 + timedelta(days=32))
    rows = changes_between(states[1], states[2], ("daily_cap:loosen",), ev, ts_ns=5)
    store.save(states[2], rows)
    assert store.load("icm-demo") == states[2]
    assert store.load("other") is None
    got = store.changes("icm-demo")
    assert [c.field for c in got] == [c.field for c in rows] and got[0].evidence_ids == rows[0].evidence_ids
    db = Database.open(tmp_path, "core")
    with db.write() as conn, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE adaptive_changes SET new = 1.0")
    with db.write() as conn, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM adaptive_changes")


# ---- the controller -------------------------------------------------------------------------------------------------

class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def __call__(self, code: str, detail: str) -> None:
        self.events.append((code, detail))


def test_the_controller_persists_each_step_and_loosens_across_restarts(tmp_path: Path):
    trades = _good_days(D0, 30)
    start = D0 + timedelta(days=30)
    out = []
    for i in range(3):
        ctl = AdaptiveController(tmp_path, DEMO, dq=_Sink())                # a fresh process each day
        out.append(ctl.current("icm-demo", _ev(trades, start + timedelta(days=i))))
    assert out[0] == FLOOR and out[2].daily_cap == pytest.approx(0.025)
    assert {c.field for c in LimitsStore(tmp_path).changes("icm-demo")} >= {"daily_cap", "weekly_cap"}


def test_the_controller_gives_floors_and_a_dq_event_for_evidence_that_does_not_validate(tmp_path: Path):
    sink = _Sink()
    ctl = AdaptiveController(tmp_path, DEMO, dq=sink)
    assert ctl.current("icm-demo", {"as_of_ns": "garbage"}) == FLOOR
    assert ctl.current("icm-demo", None) == FLOOR
    assert [c for c, _ in sink.events] == ["adaptive_limits_evidence_invalid"] * 2


def test_the_controller_gives_floors_and_a_dq_event_when_the_table_is_unreadable(tmp_path: Path):
    trades = _good_days(D0, 30)
    sink = _Sink()
    ctl = AdaptiveController(tmp_path, DEMO, dq=sink)
    ctl.store.save(_loose(D0 + timedelta(days=30)), [])
    with Database.open(tmp_path, "core").write() as conn:
        conn.execute("UPDATE adaptive_state SET body = '{\"account_id\": 1}'")
    assert ctl.current("icm-demo", _ev(trades, D0 + timedelta(days=31))) == FLOOR
    assert [c for c, _ in sink.events] == ["adaptive_limits_state_unreadable"]


def test_floors_and_bounds_come_from_the_settings_defaults():
    s = load_settings()
    floor = Limits.defaults(s.risk)
    assert floor == FLOOR

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
from goldbot.ops.accounts import Account
from goldbot.risk.adaptive import (
    FIELDS,
    AdaptiveChange,
    AdaptiveController,
    AdaptiveState,
    AdaptiveStore,
    Evidence,
    LimitBounds,
    Limits,
    TradeEvidence,
    changes_between,
    combined_lcb,
    daily_lcb,
    k_day,
    k_dd,
    k_vol,
    quarter_kelly_q,
    step,
    strategy_f,
    strategy_risk,
    stricter_risk,
)
from goldbot.risk.gate import RiskLimits, Stage
from goldbot.risk.limits_profile import (
    DEFAULT_PROFILE,
    LimitsEvidence,
    RiskCeilings,
    RiskLimitsCurrent,
    RiskOverrides,
    clamp,
)

D0 = date(2026, 11, 2)                          # a Monday
FREEZE_NS = int(pd.Timestamp("2026-10-01", tz="UTC").value)

FLOOR = Limits(daily_cap=0.02, weekly_cap=0.05, supervisor_daily_cap=0.015, supervisor_weekly_cap=0.04,
               min_lot_risk_cap=None)
DEMO_CEILING = Limits(daily_cap=0.05, weekly_cap=0.10, supervisor_daily_cap=0.0375, supervisor_weekly_cap=0.075,
                      min_lot_risk_cap=0.01)
DEMO_A1 = LimitBounds.build(FLOOR, DEMO_CEILING, mode="demo", supervisor_all_demo=True)     # production
DEMO = DEMO_A1.model_copy(update={"explore_min_lot": False})     # the caps rules alone (amendment A1 exploration off)
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
        atr_p90: float | None = 8.0, freeze: dict[str, int] | None = None, vol_med: float | None = 5.0,
        vol_p90: float | None = 8.0) -> Evidence:
    return Evidence(account_id="icm-demo", as_of_ns=_ns(day, hour, minute), trades=tuple(t for t in trades if t.exit_ns <= _ns(day, hour, minute)),
                    freeze_ns=freeze if freeze is not None else {"A": FREEZE_NS, "B": FREEZE_NS},
                    drawdown=dd, stage=stage, cost_ratio_20d=cost, atr15_median_20d=atr_med, atr15_p90_1y=atr_p90,
                    vol_median_20d=vol_med, vol_p90_1y=vol_p90)


def _at(limits: Limits, **kw: float | None) -> Limits:
    return limits.model_copy(update=kw)


def _state(limits: Limits, day: date, **kw: object) -> AdaptiveState:
    return AdaptiveState(account_id="icm-demo", day=day, limits=limits, **kw)  # type: ignore[arg-type]


def _run(trades: list[TradeEvidence], first: date, days: int, prev: AdaptiveState | None = None,
         bounds: LimitBounds = DEMO, **ev_kw: object) -> list[AdaptiveState]:
    """Evaluate at each risk-day roll from `first` for `days` days; returns the state after each."""
    out: list[AdaptiveState] = []
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


def _q_s(trades: list[TradeEvidence]) -> float:
    """Amendment A1 by hand: min(LCB/(4·E[r²]), N̄·LCB/(4·E[D²])) / 3%, clamped to [0, 1]."""
    st = daily_lcb(trades)
    assert st is not None
    sums: dict[date, float] = {}
    for t in trades:
        k = date(1970, 1, 1) + timedelta(days=t.exit_ns // (86_400 * 10**9))
        sums[k] = sums.get(k, 0.0) + t.net_r
    e_r2 = float(np.mean([t.net_r ** 2 for t in trades]))
    e_d2 = float(np.mean([v ** 2 for v in sums.values()]))
    nbar = len(trades) / len(sums)
    return min(1.0, max(0.0, min(st.lcb / (4 * e_r2), nbar * st.lcb / (4 * e_d2)) / 0.03))


def _days_of(first: date, pairs: list[tuple[float, float]], days: int, strategy: str = "A") -> list[TradeEvidence]:
    return [_t(first + timedelta(days=i), r, strategy=strategy) for i in range(days) for r in pairs[i % len(pairs)]]


W, L = 1.2, -0.9
INDEPENDENT = [(W, L), (L, W), (W, W), (L, L)]       # wins and losses spread over the days
CLUSTERED = [(W, W), (L, L)]                         # the same trades, losses bunched on the same days


def test_quarter_kelly_is_per_strategy_the_smaller_of_the_trade_and_day_terms():
    trades = _days_of(D0, CLUSTERED, 200)
    assert 0.0 < quarter_kelly_q(trades) < 1.0
    assert quarter_kelly_q(trades) == pytest.approx(_q_s(trades))
    assert quarter_kelly_q(_bad_days(D0, 30)) == 0.0
    assert quarter_kelly_q([]) == 0.0


def test_clustered_same_day_losses_lower_q_against_the_same_trades_spread_out():
    spread, clustered = _days_of(D0, INDEPENDENT, 200), _days_of(D0, CLUSTERED, 200)
    assert sorted(t.net_r for t in spread) == sorted(t.net_r for t in clustered)
    q_spread, q_clustered = quarter_kelly_q(spread), quarter_kelly_q(clustered)
    assert 0.0 < q_clustered < q_spread
    assert q_clustered < 1.0                             # the day term binds, not the clamp


def test_q_is_the_minimum_over_strategies_and_e_r2_is_not_pooled():
    a = _days_of(D0, CLUSTERED, 200, strategy="A")
    b = _days_of(D0, [(0.4, 0.3), (0.2, 0.3)], 200, strategy="B")      # tiny r: pooled with A it would shrink E[r²]
    assert quarter_kelly_q(a + b) == pytest.approx(min(_q_s(a), _q_s(b)))
    assert quarter_kelly_q(a + b) == pytest.approx(_q_s(a))


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
    prev: AdaptiveState | None = None
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
    prev: AdaptiveState | None = None
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
    assert DEMO_A1.explore_min_lot and not LIVE.explore_min_lot
    assert not LimitBounds(floor=FLOOR, ceiling=DEMO_CEILING).explore_min_lot      # only `build` turns exploration on
    assert LimitBounds.build(FLOOR, DEMO_CEILING, mode="demo", supervisor_all_demo=False).ceiling == \
        DEMO_CEILING.model_copy(update={"supervisor_daily_cap": 0.015, "supervisor_weekly_cap": 0.04})


def test_a_ceiling_below_the_floor_is_the_floor():
    low = _at(FLOOR, daily_cap=0.01, weekly_cap=0.03)
    b = LimitBounds.build(FLOOR, low, mode="demo", supervisor_all_demo=True)
    assert b.ceiling.daily_cap == 0.02 and b.ceiling.weekly_cap == 0.05


# ---- tightening -----------------------------------------------------------------------------------------------------

def _loose(day: date) -> AdaptiveState:
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

def _exc_days(first: date, days: int, pairs: list[tuple[float, float]]) -> list[TradeEvidence]:
    return [_t(first + timedelta(days=i), r, exc=True) for i in range(days) for r in pairs[i % len(pairs)]]


def test_min_lot_exploration_gives_1_percent_on_demo_from_the_first_day_and_never_on_live():
    trades = _good_days(D0, 5)
    d = D0 + timedelta(days=5)
    s, reasons = step(None, _ev(trades, d), d, DEMO_A1)
    assert s.limits.min_lot_risk_cap == 0.01 and "minlot_explore" in reasons
    assert s.limits.daily_cap == FLOOR.daily_cap and s.limits.weekly_cap == FLOOR.weekly_cap   # caps at the floors
    assert step(None, _ev(trades, d), d, LIVE)[0].limits.min_lot_risk_cap is None


def test_exploration_ends_at_60_exception_trades_and_then_the_lcb_rule_decides():
    weak = _exc_days(D0, 29, [(1.0, -1.05), (1.1, -1.2)]) + [_t(D0 + timedelta(days=29), 1.0, exc=True)]   # 59
    d = D0 + timedelta(days=30)
    s59, _ = step(_state(_at(FLOOR, min_lot_risk_cap=0.01), d - timedelta(days=1)), _ev(weak, d), d, DEMO_A1)
    assert s59.limits.min_lot_risk_cap == 0.01                           # still exploring: 59 exception trades
    weak.append(_t(d, -1.05, hour=0, minute=1, exc=True))                # the 60th; cumulative R about -2.6
    s60, reasons = step(s59, _ev(weak, d, hour=1), d, DEMO_A1)
    assert s60.limits.min_lot_risk_cap is None and "target_below" in reasons


def test_exploration_stops_when_the_running_cumulative_r_touches_minus_10R_even_if_it_recovers():
    # 12 x -1R then 7 x +1R, never evaluated in between: the total is -5R but the running low was -12R
    path = [-1.0] * 12 + [1.0] * 7
    trades = [_t(D0 + timedelta(days=i // 3), r, exc=True, minute=i) for i, r in enumerate(path)]
    d = D0 + timedelta(days=10)
    s, reasons = step(_state(_at(FLOOR, min_lot_risk_cap=0.01), d - timedelta(days=1)), _ev(trades, d), d, DEMO_A1)
    assert s.limits.min_lot_risk_cap is None and s.minlot_explore_stopped and "minlot_explore_stop" in reasons


def test_exploration_stops_for_good_at_minus_10R_cumulative():
    losers = [_t(D0 + timedelta(days=i // 2), -0.5, exc=True) for i in range(19)]    # -9.5R: keeps exploring
    d = D0 + timedelta(days=10)
    s, _ = step(None, _ev(losers, d), d, DEMO_A1)
    assert s.limits.min_lot_risk_cap == 0.01
    losers.append(_t(d, -0.5, hour=0, minute=1, exc=True))               # exactly -10R
    s, reasons = step(s, _ev(losers, d, hour=1), d, DEMO_A1)
    assert s.limits.min_lot_risk_cap is None and "minlot_explore_stop" in reasons and s.minlot_explore_stopped
    losers += [_t(d + timedelta(days=k), 2.0, exc=True) for k in range(5)]          # recovers above -10R
    later = _run(losers, d + timedelta(days=5), 5, prev=s, bounds=DEMO_A1)
    assert all(x.limits.min_lot_risk_cap is None for x in later)       # a re-test is a new trial


@pytest.mark.parametrize("kw,code", [({"atr_med": 9.0}, "atr_minlot_off"), ({"stage": Stage.SIZE_DOWN}, "stage_floor"),
                                     ({"cost": 1.3}, "cost_floor"), ({"dd": 0.05}, "dd_floor")])
def test_exploration_pauses_while_a_condition_holds_and_resumes_at_the_next_roll(kw: dict, code: str):
    trades = _good_days(D0, 5)
    d = D0 + timedelta(days=5)
    s, _ = step(None, _ev(trades, d), d, DEMO_A1)
    paused, reasons = step(s, _ev(trades, d, hour=10, **kw), d, DEMO_A1)
    assert paused.limits.min_lot_risk_cap is None and code in reasons
    same_day, _ = step(paused, _ev(trades, d, hour=11), d, DEMO_A1)
    assert same_day.limits.min_lot_risk_cap is None                      # intraday only tightens
    nxt = d + timedelta(days=1)
    resumed, reasons = step(same_day, _ev(trades, nxt), nxt, DEMO_A1)
    assert resumed.limits.min_lot_risk_cap == 0.01 and "minlot_explore" in reasons   # no 5-day cooldown


def test_exploration_pauses_for_the_loss_streak_today_and_tomorrow_only():
    d = D0 + timedelta(days=5)
    trades = _good_days(D0, 5) + [_t(d, -1.0, hour=8, minute=m) for m in range(4)]
    s, _ = step(_state(_at(FLOOR, min_lot_risk_cap=0.01), d - timedelta(days=1)), _ev(trades, d, hour=9), d, DEMO_A1)
    assert s.limits.min_lot_risk_cap is None
    trades.append(_t(d, 1.0, hour=10))
    s, _ = step(s, _ev(trades, d + timedelta(days=1)), d + timedelta(days=1), DEMO_A1)
    assert s.limits.min_lot_risk_cap is None                             # tomorrow still paused
    s, _ = step(s, _ev(trades, d + timedelta(days=2)), d + timedelta(days=2), DEMO_A1)
    assert s.limits.min_lot_risk_cap == 0.01


def test_exploration_keeps_its_fixed_cap_through_a_cost_step():
    trades = _good_days(D0, 5)
    d = D0 + timedelta(days=5)
    s, _ = step(_state(_at(FLOOR, min_lot_risk_cap=0.01), d - timedelta(days=1)), _ev(trades, d, cost=1.2), d, DEMO_A1)
    assert s.limits.min_lot_risk_cap == 0.01


def test_min_lot_cap_stays_off_after_exploration_without_positive_exception_evidence():
    trades = _good_days(D0, 30) + _exc_days(D0, 30, [(0.5, -1.0), (0.3, -0.5)])     # 60 exception trades, LCB < 0
    states = _run(trades, D0 + timedelta(days=30), 12, bounds=DEMO_A1)
    assert all(s.limits.min_lot_risk_cap is None for s in states)


def test_min_lot_cap_steps_0_6_then_0_8_then_1_0_and_is_forced_off_by_high_atr():
    trades = _good_days(D0, 30, exc=True)
    start = D0 + timedelta(days=30)
    seen: list[float | None] = []
    prev: AdaptiveState | None = None
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

def _replay() -> list[tuple[AdaptiveState, tuple[str, ...]]]:
    rng = random.Random(11)
    trades: list[TradeEvidence] = []
    prev: AdaptiveState | None = None
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
    store = AdaptiveStore(tmp_path)
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
    assert {c.field for c in AdaptiveStore(tmp_path).changes("icm-demo")} >= {"daily_cap", "weekly_cap"}


def test_the_controller_logs_the_first_day_exploration_cap_against_the_floors(tmp_path: Path):
    d = D0 + timedelta(days=5)
    ctl = AdaptiveController(tmp_path, DEMO_A1, dq=_Sink())
    assert ctl.current("icm-demo", _ev(_good_days(D0, 5), d)).min_lot_risk_cap == 0.01
    (row,) = AdaptiveStore(tmp_path).changes("icm-demo")
    assert (row.field, row.old, row.new) == ("min_lot_risk_cap", None, 0.01) and "minlot_explore" in row.reasons


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


# ---- dynamic per-strategy limits (ADR 0039, docs/design/dynamic-limits-2026-10-19.md) -------------------------------
# "r_s = base + w(f_s - base), w = n_days/(n_days+20), quantised to 0.1% steps, clamped [0.25%, ceiling]; loosen only
# with demo n >= 20 and the 0031 3-day/cooldown rules; shadow may only tighten; LCB <= 0 gives 0.25%"

DYN = LimitBounds.build(FLOOR, DEMO_CEILING, mode="demo", supervisor_all_demo=True, dynamic=True,
                        risk_base=0.005, risk_max=0.01).model_copy(update={"explore_min_lot": False})
DYN_LIVE = LimitBounds.build(FLOOR, DEMO_CEILING, mode="live", supervisor_all_demo=False, dynamic=True,
                             risk_base=0.005, risk_max=0.01)


def _edge_days(first: date, days: int, m: float, *, strategy: str = "A", source: str = "demo") -> list[TradeEvidence]:
    """A small, steady edge: two trades a day at m+1 and m-1 R (each daily sum 2m), so the per-trade f is about m/4
    (the per-trade LCB, m, over 4·E[r²])."""
    return [_t(first + timedelta(days=i), r, strategy=strategy, source=source) for i in range(days)
            for r in (m + 1.0, m - 1.0)]


def test_strategy_risk_shrinks_to_base_with_few_days():
    few, many = _edge_days(D0, 2, 0.04), _edge_days(D0, 40, 0.04)
    f = strategy_f(many)
    assert f is not None and f == pytest.approx(0.01, rel=0.01)
    t2, t40 = strategy_risk(few, 0.005), strategy_risk(many, 0.005)
    assert t2.w == pytest.approx(2 / 22) and t40.w == pytest.approx(40 / 60)
    assert t2.r == pytest.approx(0.005)                       # 0.5% + 0.09 x 0.5% = 0.545%: quantised down to 0.5%
    assert t40.r == pytest.approx(0.008)                      # 0.5% + 0.667 x 0.5% = 0.833% -> 0.8%
    assert strategy_risk(_edge_days(D0, 1, 0.04), 0.005).r == 0.005
    assert strategy_risk([], 0.005).reason == "no_evidence"   # fewer than 2 days: the base


def test_strategy_risk_quantises_down_so_rounding_never_raises_risk():
    lower = strategy_risk(_edge_days(D0, 40, 0.016), 0.005)  # f about 0.4%: 0.5% - 0.667 x 0.1% = 0.433% -> 0.4%
    assert lower.r == pytest.approx(0.004)


def test_negative_lcb_gives_quarter_percent():
    t = strategy_risk(_bad_days(D0, 30), 0.005)
    assert t.r == pytest.approx(0.0025) and t.reason == "lcb_not_positive" and t.lcb is not None and t.lcb <= 0


def test_strategy_risk_never_above_one_percent():
    # f is clamped to the 1% limit before shrinking: 0.5% + 0.75 x 0.5% = 0.875% -> 0.8% after 60 days
    assert strategy_risk(_good_days(D0, 60), 0.005, ceiling=0.05).r == pytest.approx(0.008)
    rng = random.Random(5)
    for _ in range(100):
        trades = [_t(D0 + timedelta(days=rng.randrange(40)), rng.choice((2.0, 1.0, -0.4, -1.0))) for _ in range(60)]
        r = strategy_risk(trades, 0.005).r
        assert 0.0025 - 1e-12 <= r <= 0.01 + 1e-12


def _run_dyn(trades: list[TradeEvidence], first: date, days: int, bounds: LimitBounds = DYN,
             prev: AdaptiveState | None = None, **kw: object) -> list[AdaptiveState]:
    return _run(trades, first, days, prev=prev, bounds=bounds, **kw)


def test_strategy_risk_rises_one_step_after_three_qualifying_days_on_demo_fills():
    trades = _edge_days(D0, 40, 0.04)                                  # target 0.8%
    states = _run_dyn(trades, D0 + timedelta(days=40), 6)
    assert [s.limits.strategy_risk["A"] for s in states[:2]] == [0.005, 0.005]
    assert states[2].limits.strategy_risk["A"] == pytest.approx(0.006)   # one 0.1% step on the 3rd day
    # no 10 new demo trades since: it holds
    assert all(s.limits.strategy_risk["A"] == pytest.approx(0.006) for s in states[3:])
    term = states[2].risk_terms["A"]
    assert term.reason == "loosen" and term.target == pytest.approx(0.008) and term.n_days == 40 and term.why


def test_strategy_risk_never_rises_with_fewer_than_20_demo_trades():
    trades = _edge_days(D0, 9, 0.04)                                   # 18 demo trades
    states = _run_dyn(trades, D0 + timedelta(days=9), 8)
    assert all(s.limits.strategy_risk["A"] == 0.005 for s in states)
    assert states[-1].risk_terms["A"].reason == "demo_too_few"


def test_shadow_only_tightens_strategy_risk():
    good_shadow = _edge_days(D0, 40, 0.04, strategy="A", source="shadow")
    assert all(s.limits.strategy_risk["A"] == 0.005 for s in _run_dyn(good_shadow, D0 + timedelta(days=40), 8))
    bad_shadow = _bad_days(D0, 30, strategy="A", source="shadow")
    s = _run_dyn(_edge_days(D0, 40, 0.04) + bad_shadow, D0 + timedelta(days=40), 1)[0]
    assert s.limits.strategy_risk["A"] == pytest.approx(0.0025) and s.risk_terms["A"].reason == "tighten"


def test_strategy_risk_tightens_at_once_and_waits_out_the_cooldown_before_rising():
    d = D0 + timedelta(days=40)
    prev = _state(FLOOR.model_copy(update={"strategy_risk": {"A": 0.008}}), d - timedelta(days=1))
    s, reasons = step(prev, _ev(_bad_days(D0, 30), d, hour=10), d, DYN)          # intraday, any time
    assert s.limits.strategy_risk["A"] == pytest.approx(0.0025) and "strategy_risk:A:tighten" in reasons
    good = _edge_days(D0, 40, 0.04) + _edge_days(d, 10, 0.04)
    later = _run_dyn(good, d + timedelta(days=1), 10, prev=s)
    rises = [i for i, x in enumerate(later) if x.limits.strategy_risk["A"] > 0.0025 + 1e-12]
    assert rises and (later[rises[0]].day - d).days > 5                          # never within 5 days of the tighten


def test_live_strategy_risk_never_above_default():
    assert DYN_LIVE.risk_ceiling == 0.005
    states = _run_dyn(_good_days(D0, 40), D0 + timedelta(days=40), 10, bounds=DYN_LIVE)
    assert all(s.limits.strategy_risk["A"] <= 0.005 for s in states)
    d = D0 + timedelta(days=70)
    loose = _state(FLOOR.model_copy(update={"strategy_risk": {"A": 0.009}}), d)
    s, _ = step(loose, _ev(_good_days(D0, 40), d, hour=10), d, DYN_LIVE)
    assert s.limits.strategy_risk["A"] == pytest.approx(0.005)


def test_flag_off_keeps_todays_fixed_values():
    states = _run(_edge_days(D0, 40, 0.04) + _bad_days(D0, 30, strategy="B"), D0 + timedelta(days=40), 5, bounds=DEMO)
    assert all(s.limits.strategy_risk == {} and s.risk_terms == {} for s in states)
    d = D0 + timedelta(days=41)
    prev = _state(FLOOR.model_copy(update={"strategy_risk": {"A": 0.008}}), d - timedelta(days=1))
    s, _ = step(prev, _ev(_edge_days(D0, 40, 0.04), d), d, DEMO)
    assert s.limits.strategy_risk == {}                                          # the rollback: switch the flag off


def test_corrupt_evidence_gives_fixed_values():
    d = D0 + timedelta(days=40)
    s, reasons = step(None, None, d, DYN)
    assert s.limits.strategy_risk == {} and "evidence_invalid" in reasons
    prev = _state(FLOOR.model_copy(update={"strategy_risk": {"A": 0.008, "B": 0.0025}}), d - timedelta(days=1))
    s, _ = step(prev, None, d, DYN)
    assert s.limits.strategy_risk == {"A": 0.005, "B": 0.0025}     # never above today's 0.5%, a tightened one stays
    s, _ = step(prev, _ev(_good_days(D0, 30), d, dd=0.05), d, DYN)
    assert s.limits.strategy_risk == {"A": 0.005, "B": 0.0025} and s.risk_terms["A"].reason == "floors"


def test_vol_regime_at_p90_steps_daily_down():
    d = D0 + timedelta(days=40)
    prev = _state(FLOOR.model_copy(update={"daily_cap": 0.03}), d - timedelta(days=1))
    trades = LOOSE_TRADES
    at, reasons = step(prev, _ev(trades, d, vol_med=8.0, vol_p90=8.0), d, DYN)             # >= p90: one step down
    assert at.limits.daily_cap == pytest.approx(0.025) and "daily_cap:vol_step" in reasons and at.vol_high
    below, _ = step(prev, _ev(trades, d, vol_med=7.99, vol_p90=8.0), d, DYN)
    assert below.limits.daily_cap == pytest.approx(0.03) and below.vol_high is False
    flag_off, _ = step(prev, _ev(trades, d, vol_med=8.0, vol_p90=8.0), d, DEMO)
    assert flag_off.limits.daily_cap == pytest.approx(0.03)
    unknown, _ = step(prev, _ev(trades, d, vol_med=None, vol_p90=None), d, DYN)
    assert unknown.limits.daily_cap == pytest.approx(0.03) and unknown.vol_high is None   # unknown: today's rules
    # while the regime holds: one step only (no ratchet) and no loosening
    held = _run_dyn(trades, d + timedelta(days=1), 8, prev=at, vol_med=9.0, vol_p90=8.0)
    assert all(s.limits.daily_cap == pytest.approx(0.025) for s in held)


def test_strategy_risk_changes_are_logged_with_their_reasons():
    d = D0 + timedelta(days=40)
    prev = _state(FLOOR.model_copy(update={"strategy_risk": {"A": 0.008}}), d - timedelta(days=1))
    new, reasons = step(prev, _ev(_bad_days(D0, 30), d), d, DYN)
    rows = [c for c in changes_between(prev, new, reasons, None, ts_ns=1) if c.field.startswith("strategy_risk:")]
    assert [(c.field, c.old, c.new) for c in rows] == [("strategy_risk:A", 0.008, 0.0025)]
    assert "tighten" in rows[0].reasons and not any(r.startswith("A:") for r in rows[0].reasons)


def test_restart_keeps_strategy_risk(tmp_path: Path):
    trades = _edge_days(D0, 40, 0.04)
    start = D0 + timedelta(days=40)
    out = [AdaptiveController(tmp_path, DYN, dq=_Sink()).current("icm-demo", _ev(trades, start + timedelta(days=i)))
           for i in range(4)]
    assert out[2].strategy_risk["A"] == pytest.approx(0.006) and out[3].strategy_risk["A"] == pytest.approx(0.006)
    st = AdaptiveStore(tmp_path).load("icm-demo")
    assert st is not None and st.risk_terms["A"].r == pytest.approx(0.006)


def test_intraday_factors_only_ever_tighten():
    assert k_vol(True) == 0.5 and k_vol(False) == 1.0 and k_vol(None) == 1.0
    assert k_dd(None) == 1.0 and k_dd(0.0) == 1.0 and k_dd(0.02) == 1.0
    assert k_dd(0.03) == pytest.approx(0.75) and k_dd(0.04) == 0.5 and k_dd(0.2) == 0.5
    assert k_day(0.0, 50.0) == 1.0 and k_day(24.99, 50.0) == 1.0 and k_day(25.0, 50.0) == 0.5
    assert k_day(10.0, 0.0) == 1.0 and k_day(float("nan"), 50.0) == 0.5          # unknown loss: the strict side
    assert stricter_risk({"A": 0.008, "B": 0.003}, {"A": 0.006, "C": 0.009}, 0.005) == \
        {"A": 0.006, "B": 0.003, "C": 0.005}


# ---- ADR 0039: the published terms and the clamp (limits_profile), the nightly job's bounds and notice -------------

_OV = RiskOverrides(profile="demo-2026-10-18", ceilings=RiskCeilings(daily=0.05, weekly=0.10, min_lot_risk_cap=0.01))


def _account(mode: str = "demo", overrides: RiskOverrides | None = _OV, *, dynamic: bool = False) -> Account:
    a = Account(account_id="icm-demo", broker="icm", mode=mode, server="s", login=1,  # type: ignore[arg-type]
                terminal_path="t", server_tz="UTC", symbol="XAUUSD", magic_base=260100, enabled=True,
                daily_loss_limit_abs=50.0, risk_overrides=overrides)
    # lane C adds `Account.dynamic_limits`; the job reads it with getattr so either merge order works
    return a.model_copy(update={"dynamic_limits": True}) if dynamic else a


def _current(**kw: object) -> RiskLimitsCurrent:
    return RiskLimitsCurrent(profile="default", daily_cap=0.02, weekly_cap=0.05, reason="t", **kw)  # type: ignore[arg-type]


def test_clamp_keeps_demo_strategy_risk_within_quarter_percent_and_the_one_percent_limit():
    d = RiskLimits()
    cur = _current(strategy_risk={"A": 0.009, "B": 0.001, "C": 0.004, "D": float("nan")})
    out = clamp(cur, d, _account(), LimitsEvidence())
    assert out.strategy_risk == {"A": 0.009, "B": 0.0025, "C": 0.004}           # an unusable value: the base (dropped)
    assert out.profile == "demo-2026-10-18"                                     # above the base: demo evidence
    assert clamp(_current(strategy_risk={"C": 0.004}), d, _account(), LimitsEvidence()).profile == DEFAULT_PROFILE


def test_clamp_never_raises_strategy_risk_above_the_default_off_demo_or_when_reverted():
    d = RiskLimits()
    cur = _current(strategy_risk={"A": 0.009, "B": 0.003})
    assert clamp(cur, d, _account("live", None), LimitsEvidence()).strategy_risk == {"A": 0.005, "B": 0.003}
    reverted = clamp(cur, d, _account(), LimitsEvidence(reverted=True))
    assert reverted.strategy_risk == {} and reverted.risk_terms == {}


def test_bounds_for_turns_dynamic_risk_on_only_for_a_flagged_demo_account():
    from goldbot.ops.limits_job import bounds_for
    s = load_settings()
    on = bounds_for(s, _account(dynamic=True), [_account(dynamic=True)])
    assert on.dynamic and on.risk_base == pytest.approx(0.005) and on.risk_ceiling == pytest.approx(0.01)
    assert not bounds_for(s, _account(), [_account()]).dynamic                   # the flag defaults off
    live = bounds_for(s, _account("live", None, dynamic=True), [_account("live", None)])
    assert live.risk_ceiling == pytest.approx(live.risk_base)                   # live: tighten only


def test_the_notice_names_per_trade_risk_changes_without_ids():
    from goldbot.ops.limits_job import change_notice
    rows = [AdaptiveChange(account_id="icm-demo", day=D0, field="strategy_risk:v20261019-1", old=0.008, new=0.0025,
                           reasons=("tighten",), evidence_ids=(), ts_ns=0),
            AdaptiveChange(account_id="icm-demo", day=D0, field="strategy_risk:v2", old=None, new=0.006,
                           reasons=("loosen",), evidence_ids=(), ts_ns=0)]
    text = change_notice("icm-demo", rows, equity=1_000.0, currency="GBP", limits=FLOOR,
                         at=pd.Timestamp("2026-11-02 23:05", tz="UTC"), tz="Europe/London")
    assert "per-trade risk 0.8% -> 0.25%" in text and "the default -> 0.6%" in text
    assert "v20261019" not in text and len(text.splitlines()) <= 2


# ---- quant review of the integrated dynamic limits (2026-10-19) -----------------------------------------------------

def _per_day(first: date, days: int, per_day: list[float]) -> list[TradeEvidence]:
    return [_t(first + timedelta(days=i), r) for i in range(days) for r in per_day]


def test_per_trade_f_uses_the_per_trade_lower_bound_not_the_daily_one():
    # 3 trades a day: A1's day-unit fraction would size every trade as if it were the whole day (3x too high)
    trades = _per_day(D0, 40, [1.2, -0.9, 0.0]) + _per_day(D0 + timedelta(days=40), 40, [1.0, -1.0, 0.3])
    st = daily_lcb(trades)
    assert st is not None and st.lcb > 0
    nbar = st.n_trades / st.n_days
    e_r2 = sum(t.net_r ** 2 for t in trades) / len(trades)
    sums: dict[int, float] = {}
    for t in trades:
        k = t.exit_ns // (86_400 * 10**9)
        sums[k] = sums.get(k, 0.0) + t.net_r
    e_d2 = sum(v ** 2 for v in sums.values()) / len(sums)
    expected = min((st.lcb / nbar) / (4 * e_r2), st.lcb / (4 * e_d2))
    assert strategy_f(trades) == pytest.approx(expected)
    q = quarter_kelly_q(trades)                                  # ADR 0031's caps keep A1's day-unit fraction
    assert q * 0.03 == pytest.approx(min(expected * nbar, 0.03))


def test_f_is_invariant_when_each_day_is_split_into_more_independent_trades_of_the_same_daily_edge():
    one = _per_day(D0, 40, [1.1, -0.9])                          # per trade: the edge 0.1R, 2 a day
    two = _per_day(D0, 40, [1.1, -0.9, 1.1, -0.9])               # the same trades, twice as many a day
    f1, f2 = strategy_f(one), strategy_f(two)
    assert f1 is not None and f2 is not None and f2 == pytest.approx(f1, rel=0.05)


def test_two_or_three_very_good_days_cannot_reach_the_cap():
    for days in (2, 3):
        t = strategy_risk(_good_days(D0, days), 0.005)
        assert t.f is not None and t.f > 0.01                   # the raw fraction is far above the cap
        assert t.r == pytest.approx(0.005)                       # clamped before shrinking: 0.5% + w x 0.5% < 0.6%


def test_quarantined_trades_are_not_strategy_risk_evidence_but_stay_in_adr_0031():
    bad_q = [t.model_copy(update={"quarantined": True}) for t in _bad_days(D0, 30)]
    d = D0 + timedelta(days=40)
    s, _ = step(None, _ev(_edge_days(D0, 40, 0.04) + bad_q, d), d, DYN)
    assert s.limits.strategy_risk["A"] == 0.005 and s.risk_terms["A"].reason != "tighten"
    same, _ = step(None, _ev(_edge_days(D0, 40, 0.04) + _bad_days(D0, 30), d), d, DYN)
    assert same.limits.strategy_risk["A"] == 0.0025                 # the same trades unquarantined do tighten


def test_demo_and_shadow_evidence_mark_trades_opened_before_the_history_fix(tmp_path: Path):
    from types import SimpleNamespace

    from goldbot.execution.costs import settings_swap
    from goldbot.ops.gates_phase import ClosedTrade
    from goldbot.ops.limits_job import EVIDENCE_START_UTC, demo_evidence, shadow_evidence
    start = EVIDENCE_START_UTC
    before_ns = int(pd.Timestamp("2026-11-01", tz="UTC").value)

    def closed(entry: pd.Timestamp | None, exit_: pd.Timestamp, n: int) -> ClosedTrade:
        return ClosedTrade(account_id="icm-demo", broker="icm", mode="demo", exit_utc=exit_, ret=0.001, pnl=1.0,
                           equity_before=1000.0, position_id=n, entry_utc=entry, r=0.5, model_version="v1")

    trades = [closed(start - pd.Timedelta(minutes=1), start + pd.Timedelta(hours=1), 1),
              closed(start, start + pd.Timedelta(hours=1), 2),
              closed(None, start - pd.Timedelta(minutes=5), 3),
              closed(None, start + pd.Timedelta(hours=2), 4)]
    imputer = SimpleNamespace(charge=lambda t: SimpleNamespace(r=0.0))
    demo, problems = demo_evidence(trades, "icm-demo", imputer, {"v1": 0}, before_ns)  # type: ignore[arg-type]
    assert not problems and [t.quarantined for t in demo] == [True, False, True, False]

    def sh(entry: pd.Timestamp) -> SimpleNamespace:
        return SimpleNamespace(taken=True, exit_ts=entry + pd.Timedelta(hours=1), ret=0.001, entry=4000.0,
                               stop=3990.0, side=1, entry_ts=entry)

    book = SimpleNamespace(books={"v1": SimpleNamespace(closed=[sh(start - pd.Timedelta(hours=1)), sh(start)])})
    out = shadow_evidence(book, {"v1": 0}, extra_cost_usd=0.0, swap=settings_swap(load_settings()),  # type: ignore[arg-type]
                          before_ns=before_ns)
    assert [t.quarantined for t in out] == [True, False]


def _bars_with_a_session_spike(days: int, end: pd.Timestamp, *, recent_scale: float = 1.0,
                               recent_days: int = 20) -> pd.DataFrame:
    """15m bars with a strong intraday pattern: one session hour a day has 5x the range. The last `recent_days`
    days are scaled by `recent_scale` (a volatile regime)."""
    ts = pd.date_range(end - pd.Timedelta(days=days), end - pd.Timedelta(minutes=15), freq="15min", tz="UTC")
    rng = np.random.default_rng(3)
    spike = np.where(ts.hour == 13, 5.0, 1.0) * rng.uniform(0.9, 1.1, len(ts))
    spike = np.where(ts >= end - pd.Timedelta(days=recent_days), spike * recent_scale, spike)
    close = 4000.0 + np.zeros(len(ts))
    return pd.DataFrame({"ts_utc": ts, "open": close, "high": close + spike, "low": close - spike, "close": close})


def test_the_vol_regime_compares_like_with_like_daily_median_atr15():
    from goldbot.ops.limits_job import atr15_daily_stats, atr15_stats
    end = pd.Timestamp("2026-11-02", tz="UTC")
    before = int(end.value)
    calm = _bars_with_a_session_spike(370, end)
    med, p90 = atr15_daily_stats(calm, before)
    assert med is not None and p90 is not None and med < p90                     # an ordinary month: no regime
    hot = _bars_with_a_session_spike(370, end, recent_scale=1.6)
    med, p90 = atr15_daily_stats(hot, before)
    assert med is not None and p90 is not None and med >= p90                    # 60% more volatile: the regime
    bar_med, bar_p90 = atr15_stats(hot, before)                                  # per-bar p90 is the session hour:
    assert bar_med is not None and bar_p90 is not None and bar_med < bar_p90     # it would almost never fire
    assert atr15_daily_stats(calm.iloc[-1000:], before) == (None, None)          # less than a year: unknown

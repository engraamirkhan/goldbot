"""RiskGate and Supervisor: hard limits no model output can override (design: Risk management, Supervisor)."""
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import Stage, new_day
from goldbot.risk.supervisor import Supervisor, SupervisorLimits


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                                open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="trend-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0,
                                cost_atr=0.1, multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


def _stage(st: AccountState) -> Stage:
    return st.stage


# ---------------------------------------------------------------------------------------------- RiskGate: blocks
@pytest.mark.parametrize("state_kw, reason", [
    (dict(owner_halt=True), "owner_halt"),
    (dict(supervisor_halt=True), "supervisor_halt"),
    (dict(dq_error=True), "data_quality_error"),
    (dict(in_blackout=True), "news_blackout"),
    (dict(last_tick_age_s=20.5), "stale_data"),
    (dict(spread_points=45.1), "spread_too_wide"),
    (dict(open_positions=2), "max_positions"),
    (dict(equity=9_800), "daily_cap"),                                     # exactly 2% down blocks
    (dict(equity=9_500, day_start_equity=9_500), "weekly_cap"),           # exactly 5% down blocks
    (dict(equity=8_800, day_start_equity=8_800, week_start_equity=8_800), "drawdown_halt"),
])
def test_each_account_condition_blocks_entries_on_its_own(state_kw, reason):
    d = RiskGate().check(_intent(), _state(**state_kw))
    assert not d.allowed and d.lots == 0.0
    assert d.reasons == [reason]


@pytest.mark.parametrize("state_kw", [
    dict(last_tick_age_s=20.0),          # exactly at the 20 s limit is still fresh
    dict(spread_points=45.0),            # exactly at the spread limit is allowed
    dict(open_positions=1),
    dict(equity=9_801),                  # 1.99% daily loss
])
def test_values_just_inside_the_limits_are_allowed(state_kw):
    assert RiskGate().check(_intent(), _state(**state_kw)).allowed


def test_every_failing_reason_is_reported_not_just_the_first():
    st = _state(owner_halt=True, supervisor_halt=True, dq_error=True, in_blackout=True, last_tick_age_s=99,
                spread_points=99, open_positions=5, equity=9_000, day_start_equity=10_000, week_start_equity=10_000)
    d = RiskGate().check(_intent(p=0.1, cost_atr=1.0), st)
    assert not d.allowed
    assert set(d.reasons) >= {"owner_halt", "supervisor_halt", "data_quality_error", "news_blackout", "stale_data",
                              "spread_too_wide", "max_positions", "daily_cap", "weekly_cap", "negative_ev",
                              "target_below_cost_floor"}
    assert "drawdown_halt" not in d.reasons        # 10% drawdown is size-down, not halt


def test_target_must_clear_two_and_a_half_times_round_trip_cost():
    # EV is positive (p high) but target 1.5 ATR < 2.5 x 0.7 ATR cost
    d = RiskGate().check(_intent(p=0.9, cost_atr=0.7), _state())
    assert d.reasons == ["target_below_cost_floor"]
    assert RiskGate().check(_intent(p=0.9, cost_atr=0.6), _state()).allowed    # 1.5 == 2.5 x 0.6


def test_zero_expected_value_is_refused():
    # p*T - (1-p)*S - c == 0 exactly: break-even trades never pass
    d = RiskGate().check(_intent(p=0.5, target_atr=1.0, stop_atr=1.0, cost_atr=0.0), _state())
    assert "negative_ev" in d.reasons


# ---------------------------------------------------------------------------------------------- RiskGate: sizing
def test_model_multiplier_is_bounded_to_quarter_and_one_and_a_half():
    g = RiskGate()
    hi = g.check(_intent(multiplier=10.0), _state())
    lo = g.check(_intent(multiplier=0.0001), _state())
    # $10k x 0.5% x 1.5 = $75 over $4 x 100 oz -> 0.1875 -> 0.18 ; x 0.25 -> 0.03125 -> 0.03
    assert hi.allowed and hi.lots == 0.18
    assert lo.allowed and lo.lots == 0.03


def test_size_down_stage_halves_risk_and_caps_the_multiplier_at_half():
    st = _state(equity=9_100, day_start_equity=9_100, week_start_equity=9_100)      # 9% drawdown
    d = RiskGate().check(_intent(multiplier=1.5), st)
    assert _stage(st) == Stage.SIZE_DOWN and d.allowed
    # 9100 x 0.25% x 0.5 = 11.375 / 400 = 0.0284 -> 0.02
    assert d.lots == 0.02
    assert d.risk_fraction <= 0.005 * 0.5 * 0.5 * 1.2


def test_stop_distance_is_floored_at_broker_stop_level_plus_spread():
    d = RiskGate().check(_intent(stops_level_points=500), _state(spread_points=25))
    assert d.allowed and d.stop_distance == pytest.approx(5.25)     # (500 + 25) points x $0.01 > 1.0 x $4 ATR
    d2 = RiskGate().check(_intent(), _state())
    assert d2.stop_distance == pytest.approx(4.0)


def test_lots_are_rounded_down_to_the_volume_step_and_capped_at_volume_max():
    d = RiskGate().check(_intent(volume_step=0.1), _state())     # raw 0.125 -> 0.1
    assert d.lots == pytest.approx(0.1)
    d2 = RiskGate().check(_intent(volume_max=0.05), _state())
    assert d2.allowed and d2.lots == pytest.approx(0.05)


def test_min_lot_is_used_only_while_it_keeps_risk_within_one_point_two_times_target():
    g = RiskGate()
    ok = g.check(_intent(atr_usd=55.0), _state())       # raw 0.0091 lots: 0.01 lot risks 0.55% <= 0.6%
    assert ok.allowed and ok.lots == 0.01
    skipped = g.check(_intent(atr_usd=70.0), _state())  # raw 0.0071 lots: 0.01 lot would risk 0.7% > 0.6%
    assert not skipped.allowed and skipped.reasons == ["min_lot_exceeds_risk"]
    assert skipped.stop_distance == pytest.approx(70.0)


def test_entries_that_would_take_margin_level_below_300_percent_are_refused():
    g = RiskGate()
    big = g.check(_intent(atr_usd=0.5), _state())       # 1.0 lot x 100 oz x 2400 / 20 = $12k margin on $10k equity
    assert not big.allowed and big.reasons == ["margin_level_floor"]
    # an otherwise fine 0.12 lot trade ($1,440 margin) is refused when existing margin already uses most headroom
    assert g.check(_intent(), _state(margin_used=1_500)).allowed
    crowded = g.check(_intent(), _state(margin_used=2_000))
    assert not crowded.allowed and crowded.reasons == ["margin_level_floor"]


@pytest.mark.parametrize("equity", [2_000.0, 10_000.0, 55_555.0])
@pytest.mark.parametrize("atr_usd", [1.3, 4.0, 11.7, 31.0])
@pytest.mark.parametrize("mult", [0.1, 0.8, 1.5, 3.0])
def test_allowed_trades_never_risk_more_than_one_point_two_times_the_target(equity, atr_usd, mult):
    st = _state(equity=equity, balance_closed_hwm=equity, day_start_equity=equity, week_start_equity=equity)
    d = RiskGate().check(_intent(atr_usd=atr_usd, multiplier=mult), st)
    if d.allowed:
        clamped = min(max(mult, 0.25), 1.5)
        assert d.risk_fraction <= 1.2 * 0.005 * clamped + 1e-12
        assert d.lots >= 0.01 and abs(d.lots * 100 - round(d.lots * 100)) < 1e-9     # whole volume steps
        assert d.risk_fraction == pytest.approx(d.lots * d.stop_distance * 100 / equity, rel=1e-6)


def test_risk_per_trade_is_capped_by_the_hard_maximum():
    g = RiskGate(RiskLimits(risk_per_trade=0.05, max_risk_per_trade=0.01))
    d = g.check(_intent(), _state())
    # $10k x 1% = $100 / $400 per lot = 0.25 lots, never the configured 5% (1.25 lots)
    assert d.allowed and 0.24 <= d.lots <= 0.25
    assert d.risk_fraction <= 0.01


def test_a_raw_size_that_is_an_exact_number_of_steps_is_kept():
    g = RiskGate(RiskLimits(risk_per_trade=0.01))
    assert g.check(_intent(), _state()).lots == 0.25        # $100 / ($4 x 100 oz) = exactly 0.25 lots


# ---------------------------------------------------------------------------------------------- stage machine
def test_size_down_clears_only_below_five_percent_drawdown():
    g = RiskGate()
    st = _state(equity=9_100)
    assert g.update_stage(st) == Stage.SIZE_DOWN
    st.equity = 9_400                     # 6%: still size-down (hysteresis)
    assert g.update_stage(st) == Stage.SIZE_DOWN
    st.equity = 9_510                     # 4.9%: clears
    assert g.update_stage(st) == Stage.NORMAL
    st.equity = 9_400                     # 6% from NORMAL does not trip size-down
    assert g.update_stage(st) == Stage.NORMAL


def test_halt_is_sticky_and_rearm_needs_the_exact_phrase():
    g = RiskGate()
    st = _state(equity=8_700)
    assert g.update_stage(st) == Stage.HALTED
    st.equity = 10_500
    assert g.update_stage(st) == Stage.HALTED
    for wrong in ("rearm", "REARM ", "", "yes"):
        assert not g.rearm(st, wrong)
        assert _stage(st) == Stage.HALTED
    assert not g.check(_intent(), st).allowed
    assert g.rearm(st, "REARM")
    assert _stage(st) == Stage.NORMAL and st.balance_closed_hwm == 10_500


def test_drawdown_is_measured_from_closed_balance_high_water_mark():
    g = RiskGate()
    assert g.update_stage(_state(balance_closed_hwm=0)) == Stage.NORMAL       # unknown HWM never trips
    assert g.update_stage(_state(equity=8_800, balance_closed_hwm=10_000)) == Stage.HALTED


def test_new_day_resets_day_start_equity_at_utc_midnight():
    st = _state(equity=9_900)
    t0 = pd.Timestamp("2025-03-05 23:59", tz="UTC")
    assert new_day(st, t0, None) and st.day_start_equity == 9_900
    st.equity = 9_700
    assert not new_day(st, t0 + pd.Timedelta(seconds=30), t0) and st.day_start_equity == 9_900
    assert new_day(st, pd.Timestamp("2025-03-06 00:00:01", tz="UTC"), t0) and st.day_start_equity == 9_700
    # an aware non-UTC clock is converted, not read as wall time
    st.equity = 9_600
    ny = pd.Timestamp("2025-03-06 20:30", tz="America/New_York")              # 01:30 UTC on the 7th
    assert new_day(st, ny, pd.Timestamp("2025-03-06 12:00", tz="UTC")) and st.day_start_equity == 9_600


# ---------------------------------------------------------------------------------------------- Supervisor
def _engine_file(d: Path, account: str, *, equity: float, day0: float, wk0: float, hwm: float, age_s: float = 1.0) -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "ts": time.time() - age_s, "equity": equity, "day_start_equity": day0,
        "week_start_equity": wk0, "balance_closed_hwm": hwm}))


def test_combined_daily_cap_halts_although_each_account_is_inside_its_own_cap(tmp_path):
    _engine_file(tmp_path, "icm", equity=4_920, day0=5_000, wk0=5_000, hwm=5_000)
    _engine_file(tmp_path, "vantage", equity=4_920, day0=5_000, wk0=5_000, hwm=5_000)
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and s["reasons"] == ["combined_daily_cap"]            # 1.6% combined >= 1.5%
    assert s["combined_equity"] == pytest.approx(9_840)
    # each account alone is only 1.6% down: the per-account gate would still allow entries
    assert RiskGate().check(_intent(), _state(equity=4_920, balance_closed_hwm=5_000, day_start_equity=5_000,
                                              week_start_equity=5_000)).allowed
    assert Supervisor.engine_should_halt(tmp_path) == (True, "combined_daily_cap")


def test_combined_weekly_cap_and_drawdown_stages(tmp_path):
    _engine_file(tmp_path, "a", equity=9_590, day0=9_590, wk0=10_000, hwm=10_000)
    s = Supervisor(tmp_path).evaluate()
    assert s["reasons"] == ["combined_weekly_cap"] and not s["size_down"]

    _engine_file(tmp_path, "a", equity=9_150, day0=9_150, wk0=9_150, hwm=10_000)        # 8.5%
    s = Supervisor(tmp_path).evaluate()
    assert not s["halt"] and s["size_down"] and s["drawdown"] == pytest.approx(0.085)

    _engine_file(tmp_path, "a", equity=8_790, day0=8_790, wk0=8_790, hwm=10_000)        # 12.1%
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and s["reasons"] == ["combined_drawdown_halt"] and s["size_down"]


def test_custom_supervisor_limits_are_applied(tmp_path):
    _engine_file(tmp_path, "a", equity=9_950, day0=10_000, wk0=10_000, hwm=10_000)
    assert not Supervisor(tmp_path).evaluate()["halt"]
    assert Supervisor(tmp_path, SupervisorLimits(daily_cap=0.004)).evaluate()["reasons"] == ["combined_daily_cap"]


def test_healthy_engines_write_a_fresh_no_halt_heartbeat(tmp_path):
    _engine_file(tmp_path, "a", equity=10_100, day0=10_000, wk0=10_000, hwm=10_000)
    s = Supervisor(tmp_path).evaluate()
    assert not s["halt"] and s["reasons"] == [] and s["stale_engines"] == []
    assert Supervisor.engine_should_halt(tmp_path) == (False, "")
    assert not list(tmp_path.glob("*.tmp"))                                  # atomic replace left nothing behind
    assert json.loads((tmp_path / "supervisor.json").read_text())["halt"] is False


def test_silent_engines_are_reported_stale_and_torn_files_are_skipped(tmp_path):
    _engine_file(tmp_path, "fresh", equity=5_000, day0=5_000, wk0=5_000, hwm=5_000)
    _engine_file(tmp_path, "silent", equity=5_000, day0=5_000, wk0=5_000, hwm=5_000, age_s=600)
    (tmp_path / "engine_torn.json").write_text('{"account": "torn", "equity": 12')
    s = Supervisor(tmp_path).evaluate()
    assert s["stale_engines"] == ["silent"]
    assert s["combined_equity"] == pytest.approx(10_000)


def test_no_engines_means_no_losses_and_no_halt(tmp_path):
    s = Supervisor(tmp_path / "new").evaluate()
    assert not s["halt"] and s["day_loss"] == 0.0 and s["drawdown"] == 0.0


def test_engines_fail_closed_without_a_fresh_readable_supervisor_heartbeat(tmp_path):
    assert Supervisor.engine_should_halt(tmp_path) == (True, "no_supervisor_heartbeat")
    (tmp_path / "supervisor.json").write_text("{not json")
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_state_unreadable")
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": time.time() - 61, "halt": False, "reasons": []}))
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_heartbeat_stale")
    assert Supervisor.engine_should_halt(tmp_path, max_age_s=120) == (False, "")
    (tmp_path / "supervisor.json").write_text(json.dumps({"halt": False}))                # no ts at all
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_heartbeat_stale")
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": time.time(), "halt": True,
                                                          "reasons": ["combined_weekly_cap", "combined_drawdown_halt"]}))
    assert Supervisor.engine_should_halt(tmp_path) == (True, "combined_weekly_cap,combined_drawdown_halt")

"""Demo accounts without limits in the RiskGate (owner decision 2026-11-25: "there are no limits anymore for demo
accounts", then "allow absolutely anything"; ADR 0097).

`RiskLimits.demo_unlimited` (set by `ops.services.engine.engine_limits` only for a demo probe_only account whose
accounts.yaml says `demo_unlimited: true`, against its real terminal) lifts every cap on that account: the open-position
cap, the daily, weekly and absolute daily loss limits, the drawdown stages and kill switch, the news and entry windows,
the absolute spread cap, the throttle, the heat and combined-exposure caps, the minimum-lot risk rule and the
exploration arm's per-trade cap. It opens nothing unless the intent is demo by the one predicate (`gate.is_demo`): the
registry, the phase file, the terminal and probe_only, so a live account is never touched.

Kept, because they are not limits but structure or learning hygiene: the RiskGate as the only path, a stop on every
order (`spread_over_stop` keeps the stop outside the spread, or the R label is noise), halts (owner, supervisor,
disabled account), rollover, the weekend, data and terminal integrity, the exploration fence (minimum lot, registered
arm, demo terminal), a growth arm's own sizing cap, and the demo margin-safety floor (`risk.demo_floor`: equity at
least 20 % of the high-water mark, margin level after the trade at least 200 % on the broker's figure), which the
owner can switch off in one setting."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import DemoFloor
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage
from tests.test_gate_exploration import ARM, NOW, _arm, _state

FREE: dict[str, Any] = dict(probe_only=True, exploration_arms={ARM: 3.0}, demo_unlimited=True)


def _free(**kw: Any) -> RiskLimits:
    return RiskLimits(**{**FREE, **kw})


def _check(intent: Intent, limits: RiskLimits, margin: Any = None, **state: Any):
    return RiskGate(limits).check(intent, _state(**state), NOW, margin_required=margin)


CAPPED_STATES = [
    (dict(open_positions=2), "max_positions"),
    (dict(equity=975.0, balance_closed_hwm=975.0), "daily_cap"),
    (dict(equity=940.0, balance_closed_hwm=940.0, day_start_equity=940.0), "weekly_cap"),
    (dict(stage=Stage.HALTED), "drawdown_halt"),
    (dict(in_blackout=True, blackout_kind="tier1"), "news_blackout"),
    (dict(in_blackout=True, blackout_kind="news_shock"), "news_blackout"),
    (dict(spread_points=60.0), "spread_too_wide"),
    (dict(tier2_blackout=True), "tier2_blackout"),
]


def test_the_default_limits_are_not_unlimited_and_the_floor_is_on() -> None:
    lim = RiskLimits()
    assert lim.demo_unlimited is False
    assert lim.demo_floor == DemoFloor() and lim.demo_floor.enabled
    assert (lim.demo_floor.min_equity_frac, lim.demo_floor.min_margin_level) == (0.2, 2.0)


@pytest.mark.parametrize("state, reason", CAPPED_STATES)
def test_every_account_cap_refuses_an_exploration_entry_without_the_flag(tmp_path, state, reason) -> None:
    lim = _free(demo_unlimited=False, tier2={"enabled": True, "before_min": 5, "after_min": 15})
    d = _check(_arm(tmp_path), lim, **state)
    assert not d.allowed and reason in d.reasons


@pytest.mark.parametrize("state, reason", CAPPED_STATES)
def test_with_demo_unlimited_no_account_cap_refuses_a_demo_entry(tmp_path, state, reason) -> None:
    lim = _free(tier2={"enabled": True, "before_min": 5, "after_min": 15})
    d = _check(_arm(tmp_path), lim, **state)
    assert d.allowed, (reason, d.reasons)


def test_with_demo_unlimited_the_throttle_heat_and_open_risk_projection_do_not_refuse(tmp_path) -> None:
    lim = _free(heat={"enabled": True, "max_open_risk": 0.0001, "max_same_direction": 1}, project_open_risk=True,
                daily_loss_limit_abs=1.0, multi_symbol=True)
    d = _check(_arm(tmp_path), lim, open_long=3, open_risk=None, day_start_equity=0.0)
    assert d.allowed, d.reasons
    capped = _check(_arm(tmp_path), lim.model_copy(update={"demo_unlimited": False}), open_long=3, open_risk=None)
    assert not capped.allowed


def test_with_demo_unlimited_the_absolute_daily_loss_limit_and_combined_exposure_do_not_refuse(tmp_path) -> None:
    assert _check(_arm(tmp_path), _free(daily_loss_limit_abs=1.0)).allowed
    assert _check(_arm(tmp_path), _free(), other_lots=10.0).allowed
    assert not _check(_arm(tmp_path), _free(demo_unlimited=False, daily_loss_limit_abs=1.0)).allowed
    assert not _check(_arm(tmp_path), _free(demo_unlimited=False), other_lots=10.0).allowed


def test_with_demo_unlimited_the_arm_per_trade_cap_no_longer_refuses_but_the_lot_stays_the_minimum(tmp_path) -> None:
    d = _check(_arm(tmp_path, atr_usd=30.0), _free())                   # £30 at the stop against a £3 cap
    assert d.allowed and d.lots == pytest.approx(0.01), d.reasons
    big = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0, week_start_equity=10_000.0)
    capped = _check(_arm(tmp_path, atr_usd=30.0), _free(demo_unlimited=False), **big)
    assert not capped.allowed and capped.reasons == ["exploration_arm_cap"]


def test_with_demo_unlimited_the_minimum_lot_is_placed_whatever_its_risk_against_equity(tmp_path) -> None:
    """A learning probe (cost probe, mirror) above the risk per trade: the minimum lot, not `min_lot_exceeds_risk`."""
    probe = _arm(tmp_path, purpose="signal", learning_probe=True, p=0.9, target_atr=3.0, atr_usd=200.0, arm_id=None)
    d = _check(probe, _free(), equity=1_000.0)
    assert d.allowed and d.lots == pytest.approx(0.01), d.reasons
    capped = _check(probe, _free(demo_unlimited=False), equity=1_000.0)
    assert not capped.allowed and "min_lot_exceeds_risk" in capped.reasons


def test_with_demo_unlimited_the_drawdown_stage_machine_never_moves_so_no_kill_switch_fires() -> None:
    st = _state(equity=500.0, balance_closed_hwm=1_000.0)
    assert RiskGate(_free()).stage_step(st) == Stage.NORMAL
    assert RiskGate(_free(demo_unlimited=False)).stage_step(
        _state(equity=500.0, balance_closed_hwm=1_000.0)) == Stage.HALTED


@pytest.mark.parametrize("state, reason", [
    (dict(owner_halt=True), "owner_halt"),
    (dict(supervisor_halt=True), "supervisor_halt"),
    (dict(account_disabled=True), "account_disabled"),
    (dict(in_rollover=True), "rollover"),
    (dict(weekend=True), "weekend"),
    (dict(stale_bars=True), "stale_data"),
    (dict(market_closed=True), "market_closed"),
    (dict(unconfirmed_order=True), "unconfirmed_order_pending"),
    (dict(equity_unconfirmed=True), "equity_unconfirmed"),
    (dict(account_class="unknown"), "account_class_unknown"),
    (dict(drift_halt=True), "drift_system_halt"),
    (dict(dq_error=True), "data_quality_error"),
    (dict(tick_error=True), "tick_error"),
    (dict(terminal_account_changed=True), "terminal_account_changed"),
    (dict(clock_mismatch=True), "server_clock_mismatch"),
    (dict(bridge_incompatible=True), "bridge_incompatible"),
    (dict(exposure_unknown=True), "exposure_unknown"),
    (dict(data_recovering=True), "data_recovering"),
    (dict(last_tick_age_s=3_600.0), "stale_data"),
    (dict(tick_ahead_s=3_600.0), "tick_from_future"),
])
def test_with_demo_unlimited_halts_market_hours_and_data_integrity_still_refuse(tmp_path, state, reason) -> None:
    d = _check(_arm(tmp_path), _free(), **state)
    assert not d.allowed and reason in d.reasons


def test_with_demo_unlimited_a_stop_inside_the_spread_is_still_refused(tmp_path) -> None:
    """Learning hygiene: a stop the spread and round trip eat is stopped at once; its R label would be noise."""
    d = _check(_arm(tmp_path, atr_usd=0.3), _free(), spread_points=20.0)
    assert not d.allowed and "spread_over_stop" in d.reasons


def test_with_demo_unlimited_the_exploration_fence_still_refuses(tmp_path) -> None:
    for intent, reason in ((_arm(tmp_path, volume_max=0.05), "exploration_not_min_lot"),
                           (_arm(tmp_path, arm_id="unknown"), "exploration_arm_unregistered")):
        d = _check(intent, _free())
        assert not d.allowed and reason in d.reasons


@pytest.mark.parametrize("change", [
    dict(terminal_trade_mode="real"),
    dict(account_mode="live"),
    dict(phase={"gates_passed": ["paper_to_tiny_live"]}),
])
def test_the_flag_opens_nothing_unless_the_intent_is_demo_by_the_one_predicate(tmp_path, change) -> None:
    """Registry, phase file and terminal: any non-demo fact puts every cap back (fail closed)."""
    phase = change.pop("phase", None)
    d = _check(_arm(tmp_path, phase, **change), _free(), open_positions=2)
    assert not d.allowed and "max_positions" in d.reasons


def test_the_flag_opens_nothing_on_an_account_that_is_not_probe_only(tmp_path) -> None:
    d = _check(_arm(tmp_path), _free(probe_only=False), open_positions=2)
    assert not d.allowed and "max_positions" in d.reasons


# ---------------------------------------------------------------- the demo margin-safety floor
def test_the_floor_refuses_entries_once_equity_is_below_a_fifth_of_the_high_water_mark(tmp_path) -> None:
    d = _check(_arm(tmp_path), _free(), equity=199.0, balance_closed_hwm=1_000.0, day_start_equity=199.0,
               week_start_equity=199.0)
    assert not d.allowed and "demo_equity_floor" in d.reasons
    ok = _check(_arm(tmp_path), _free(), lambda s, lots, p: 10.0, equity=201.0, balance_closed_hwm=1_000.0, day_start_equity=201.0,
                week_start_equity=201.0)
    assert ok.allowed, ok.reasons


def test_the_floor_refuses_an_entry_that_would_take_the_margin_level_below_200_percent(tmp_path) -> None:
    def broker(side: int, lots: float, price: float) -> float:
        return 10.0                                     # the broker's own margin for 0.01 lot
    assert _check(_arm(tmp_path), _free(), broker, margin_used=490.0).allowed            # 1000 / 500 = 200 %
    d = _check(_arm(tmp_path), _free(), broker, margin_used=495.0)                       # 1000 / 505 < 200 %
    assert not d.allowed and d.reasons == ["demo_margin_floor"]


def test_the_floor_uses_the_brokers_margin_not_the_retail_1_to_20_figure(tmp_path) -> None:
    """At 1:20, 0.01 lot of gold at 2400 is £120 of margin and the 300 % floor would refuse here; the demo floor reads
    the broker's own figure (£10), which is what the broker holds."""
    capped = _check(_arm(tmp_path), _free(demo_unlimited=False), lambda s, lots, p: 10.0, margin_used=300.0)
    assert not capped.allowed and "margin_level_floor" in capped.reasons
    assert _check(_arm(tmp_path), _free(), lambda s, lots, p: 10.0, margin_used=300.0).allowed


def test_without_a_broker_margin_figure_the_floor_falls_back_to_the_1_to_20_figure(tmp_path) -> None:
    d = _check(_arm(tmp_path), _free(), None, margin_used=400.0)                 # 1000 / (400 + 120) < 200 %
    assert not d.allowed and d.reasons == ["demo_margin_floor"]


def test_the_owner_can_switch_the_floor_off_in_one_setting(tmp_path) -> None:
    off = _free(demo_floor=DemoFloor(enabled=False))
    assert _check(_arm(tmp_path), off, equity=100.0, balance_closed_hwm=1_000.0, day_start_equity=100.0,
                  week_start_equity=100.0).allowed
    assert _check(_arm(tmp_path), off, lambda s, lots, p: 10.0, margin_used=495.0).allowed


def test_the_floor_never_applies_to_an_account_without_the_flag(tmp_path) -> None:
    d = _check(_arm(tmp_path), _free(demo_unlimited=False), equity=1_000.0, balance_closed_hwm=10_000.0)
    assert "demo_equity_floor" not in d.reasons


def test_the_account_level_list_published_as_entry_blocks_leaves_the_lifted_caps_out() -> None:
    gate = RiskGate(_free())
    blocks = gate.account_reasons(_state(open_positions=9, equity=900.0, balance_closed_hwm=900.0), NOW)
    assert "max_positions" not in blocks and "daily_cap" not in blocks
    assert "weekend" in gate.account_reasons(_state(weekend=True), NOW)


def test_a_growth_arm_keeps_its_own_per_trade_cap_with_demo_unlimited(tmp_path) -> None:
    """Sizing is the experts' choice: the minimum lot, and a growth arm keeps the sizing it already has (its cap)."""
    lim = _free(growth_arms=frozenset({ARM}))
    d = _check(_arm(tmp_path, atr_usd=30.0, volume_max=1.0), lim)
    assert not d.allowed and "exploration_arm_cap" in d.reasons


def _operational(tmp_path: Path, gates: list[str], mode: str = "demo") -> Any:
    from goldbot.risk.gate import OperationalIntent
    phase = tmp_path / "phase_state.json"
    phase.write_text(json.dumps({"gates_passed": gates}))
    return OperationalIntent(side=1, price=2400.0, account_mode=mode, phase_file=Path(phase))


def test_an_operational_order_on_an_unlimited_demo_account_keeps_no_cap_either(tmp_path) -> None:
    """Trading-safety M2: the bridge smoke would be refused `max_positions` once exploration fills the account, so
    operational evidence would starve; on a demo account (registry and phase file, ADR 0021 F1) it is unlimited too."""
    st = _state(open_positions=2, equity=975.0, balance_closed_hwm=975.0)
    d = RiskGate(_free()).check_operational(_operational(tmp_path, []), st, pd.Timestamp(NOW), lambda s, lots, p: 10.0)
    assert d.allowed, d.reasons
    capped = RiskGate(_free(demo_unlimited=False)).check_operational(_operational(tmp_path, []), st,
                                                                     pd.Timestamp(NOW), lambda s, lots, p: 10.0)
    assert not capped.allowed and "max_positions" in capped.reasons


@pytest.mark.parametrize("gates, mode", [(["paper_to_tiny_live"], "demo"), ([], "live")])
def test_an_operational_order_off_demo_keeps_every_cap_whatever_the_flag(tmp_path, gates, mode) -> None:
    d = RiskGate(_free()).check_operational(_operational(tmp_path, gates, mode), _state(open_positions=2),
                                            pd.Timestamp(NOW), None)
    assert not d.allowed and "operational_not_demo" in d.reasons and "max_positions" in d.reasons


# ---------------------------------------------------------------- the engineering capacity ceiling (H2)
def test_the_capacity_ceiling_refuses_an_entry_at_the_measured_open_position_count(tmp_path) -> None:
    from goldbot.risk.gate import CAPACITY_CEILING
    assert CAPACITY_CEILING == 300                    # ADR 0097: the exit SLO test's load (tests/test_exit_first.py)
    d = _check(_arm(tmp_path), _free(), open_positions=CAPACITY_CEILING)
    assert not d.allowed and d.reasons == ["capacity_ceiling"]
    assert _check(_arm(tmp_path), _free(), open_positions=CAPACITY_CEILING - 1).allowed
    capped = _check(_arm(tmp_path), _free(demo_unlimited=False, max_positions=1_000), open_positions=CAPACITY_CEILING)
    assert "capacity_ceiling" not in capped.reasons    # every other account keeps its own max_positions instead


# ---------------------------------------------------------------- L1: switching the flag off
def test_switching_the_flag_off_lets_the_next_read_take_the_computed_stage_at_once_failing_closed() -> None:
    """Trading-safety L1: the stage machine does not move while unlimited; the first read after the flag goes off
    jumps straight to the stage the drawdown says (here HALTED, which only the owner's /rearm clears)."""
    gate = RiskGate(_free())
    st = _state(equity=500.0, balance_closed_hwm=1_000.0)
    assert gate.stage_step(st) == Stage.NORMAL
    gate.limits = gate.limits.model_copy(update={"demo_unlimited": False})
    assert gate.stage_step(st) == Stage.HALTED and st.stage == Stage.HALTED


# ---------------------------------------------------------------- L4: the lot is on the volume step
@pytest.mark.parametrize("free", [True, False])
def test_the_sized_lot_is_floored_to_the_instruments_volume_step_never_rounded_to_hundredths(tmp_path, free) -> None:
    """Trading-safety L4: `round(lots, 2)` turned a 0.001-step minimum lot (some crypto CFDs) into 0.0 and could round
    a lot up past what was sized. The lot is floored to the instrument's own step."""
    intent = _arm(tmp_path, volume_min=0.001, volume_max=0.001, volume_step=0.001, atr_usd=3.0)
    lim = _free(demo_unlimited=free, exploration_arms={ARM: 100.0})
    d = _check(intent, lim, lambda s, lots, p: 1.0, equity=10_000.0, balance_closed_hwm=10_000.0,
               day_start_equity=10_000.0, week_start_equity=10_000.0)
    assert d.allowed, d.reasons
    assert d.lots == pytest.approx(0.001)


# ---------------------------------------------------------------- the minimum-lot rule for wide (swing) stops
def test_a_swing_arm_whose_minimum_lot_risks_more_than_the_1_percent_exception_is_placed_when_unlimited(tmp_path):
    """The 1 % `min_lot_risk_cap` exception (BACKLOG 25) refuses a wide swing stop at the minimum lot on a small
    account; on an unlimited demo account the minimum lot goes whatever its risk against equity (the floor binds)."""
    swing = _arm(tmp_path, atr_usd=150.0, timeframe="1d")            # 0.01 x 100 x 150 = 150 = 1.5 % of 10,000
    big = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0, week_start_equity=10_000.0)
    capped = _check(swing, _free(demo_unlimited=False, min_lot_risk_cap=0.01, exploration_arms={ARM: 200.0}),
                    lambda s, lots, p: 10.0, **big)
    assert not capped.allowed and "min_lot_exceeds_risk" in capped.reasons
    d = _check(swing, _free(min_lot_risk_cap=0.01, exploration_arms={ARM: 200.0}), lambda s, lots, p: 10.0, **big)
    assert d.allowed and d.lots == pytest.approx(0.01), d.reasons


def test_state_fields_used_by_the_floor_exist() -> None:
    assert {"equity", "balance_closed_hwm", "margin_used"} <= set(AccountState.model_fields)

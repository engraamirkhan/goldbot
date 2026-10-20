"""The RiskGate's cost-measurement purpose (ADR 0037 Amendment 5, owner-approved 2026-10-20: "Allow, demo only"). A
scheduled cost probe carries no signal, so the learning-probe fence's expected-value requirement
(`time_exit_ev_unsupported`, `negative_gross_edge`) cannot apply to it; the purpose skips ONLY that, and only for a
minimum-lot demo probe on a verified demo terminal with a stop <= $3/oz, a hold <= 60 s and room in the probe budgets.
Every account limit, halt and window still applies, and any other order claiming the purpose is refused."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage

NOW = pd.Timestamp("2026-10-26 10:00", tz="UTC")


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=1_000, balance_closed_hwm=1_000, day_start_equity=1_000,
                                week_start_equity=1_000, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=20.0)
    return AccountState(**{**base, **kw})


def _probe(tmp_path: Path, phase: Any = None, **kw: Any) -> Intent:
    """A scheduled cost-measurement probe as goldbot/engine/cost_probe.py sends it: no signal (p 0.5), a time exit,
    0.01 lot, a $3 stop, a 30 s hold, inside both probe budgets."""
    phase_file = tmp_path / "phase_state.json"
    if phase is not None:
        phase_file.write_text(json.dumps(phase))
    base: dict[str, Any] = dict(agent_id="cost_probe:scheduled", side=1, p=0.5, target_atr=1.0, stop_atr=1.0,
                                atr_usd=3.0, cost_atr=0.1, multiplier=1.0, price=2400.0, timeframe="15m",
                                family="cost_measurement", has_target=False, volume_min=0.01, volume_max=0.01,
                                learning_probe=True, account_mode="demo", phase_file=phase_file,
                                purpose="cost_measurement", terminal_trade_mode="demo", hold_s=30,
                                probe_headroom=10.0, probe_max_loss=4.0)
    return Intent(**{**base, **kw})


def _check(intent: Intent, limits: RiskLimits | None = None, **state: Any):
    return RiskGate(limits).check(intent, _state(**state), NOW)


def test_a_scheduled_cost_probe_on_a_demo_terminal_is_allowed_at_the_minimum_lot(tmp_path):
    d = _check(_probe(tmp_path))
    assert d.allowed, d
    assert d.lots == pytest.approx(0.01) and d.stop_distance <= 3.0 + 1e-9


def test_without_the_purpose_the_same_probe_is_refused_for_want_of_an_edge(tmp_path):
    d = _check(_probe(tmp_path, purpose="signal"))
    assert not d.allowed and "time_exit_ev_unsupported" in d.reasons and "negative_gross_edge" in d.reasons


@pytest.mark.parametrize("terminal", ["real", "contest", None])
def test_refused_unless_the_terminal_itself_reports_demo(tmp_path, terminal):
    d = _check(_probe(tmp_path, terminal_trade_mode=terminal))
    assert not d.allowed and "cost_measurement_terminal_not_demo" in d.reasons


@pytest.mark.parametrize("mode, phase", [("live", None), (None, None), ("demo", {"gates_passed": ["paper_to_tiny_live"]})])
def test_refused_on_a_live_account_or_after_the_live_gate(tmp_path, mode, phase):
    d = _check(_probe(tmp_path, phase, account_mode=mode))
    assert not d.allowed and "learning_probe_not_demo" in d.reasons


def test_refused_above_the_minimum_lot(tmp_path):
    d = _check(_probe(tmp_path, volume_max=0.02))
    assert not d.allowed and "learning_probe_not_min_lot" in d.reasons


def test_refused_without_a_protective_stop(tmp_path):
    d = _check(_probe(tmp_path, stop_atr=0.0))
    assert not d.allowed and "invalid:stop_atr" in d.reasons


def test_refused_with_a_stop_wider_than_three_dollars(tmp_path):
    d = _check(_probe(tmp_path, atr_usd=3.5))
    assert not d.allowed and "cost_measurement_stop_above_cap" in d.reasons


def test_refused_when_the_stops_level_floors_the_stop_beyond_three_dollars(tmp_path):
    d = _check(_probe(tmp_path, stops_level_points=400.0))       # $4 stops level: the sized stop is wider than $3
    assert not d.allowed and "cost_measurement_stop_above_cap" in d.reasons


@pytest.mark.parametrize("hold", [61, 3600, None, 0])
def test_refused_unless_the_planned_hold_is_at_most_sixty_seconds(tmp_path, hold):
    d = _check(_probe(tmp_path, hold_s=hold))
    assert not d.allowed and "cost_measurement_hold" in d.reasons


def test_a_sixty_second_hold_is_allowed(tmp_path):
    assert _check(_probe(tmp_path, hold_s=60)).allowed


@pytest.mark.parametrize("budget", [
    {"probe_headroom": 0.0}, {"probe_headroom": 2.0}, {"probe_headroom": None},     # cost-probe budget
    {"probe_max_loss": 2.0}, {"probe_max_loss": None},                               # dynamic probe budget
])
def test_refused_when_a_probe_budget_cannot_take_the_loss_at_the_stop(tmp_path, budget):
    d = _check(_probe(tmp_path, **budget))                     # $3 x 0.01 x 100 oz = $3 at the stop
    assert not d.allowed and "cost_measurement_budget" in d.reasons


@pytest.mark.parametrize("state, reason", [
    ({"owner_halt": True}, "owner_halt"),
    ({"stage": Stage.HALTED}, "drawdown_halt"),                # the 12% kill switch
    ({"drift_halt": True}, "drift_system_halt"),
    ({"in_blackout": True}, "news_blackout"),
    ({"equity": 970}, "daily_cap"),
])
def test_every_halt_window_and_cap_still_refuses_it(tmp_path, state, reason):
    d = _check(_probe(tmp_path), **state)
    assert not d.allowed and any(r.startswith(reason) for r in d.reasons), d


def test_the_open_risk_projection_still_refuses_it(tmp_path):
    d = _check(_probe(tmp_path), RiskLimits(daily_loss_limit_abs=20.0, project_open_risk=True), open_risk=18.0)
    assert not d.allowed and "daily_loss_limit_abs" in d.reasons


def test_an_order_that_is_not_a_probe_cannot_claim_the_purpose(tmp_path):
    d = _check(_probe(tmp_path, learning_probe=False, agent_id="breakout-g0-abc", family="breakout"))
    assert not d.allowed and "cost_measurement_not_probe" in d.reasons


def test_a_mirror_learning_probe_is_unchanged(tmp_path):
    """A mirror probe (purpose signal) still needs its positive gross edge and a reachable target."""
    mirror = dict(agent_id="cost_probe:breakout-g0-x", p=0.42, target_atr=1.5, atr_usd=4.0, cost_atr=0.3, timeframe="1h",
                  family="breakout", has_target=True, purpose="signal", terminal_trade_mode=None, hold_s=None,
                  probe_headroom=None, probe_max_loss=None)
    assert _check(_probe(tmp_path, **mirror)).allowed
    d = _check(_probe(tmp_path, **{**mirror, "p": 0.3}))
    assert not d.allowed and "negative_gross_edge" in d.reasons
    d = _check(_probe(tmp_path, **{**mirror, "has_target": False}))
    assert not d.allowed and "time_exit_ev_unsupported" in d.reasons

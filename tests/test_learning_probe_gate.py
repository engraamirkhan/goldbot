"""The demo learning probe's gate path (owner 2026-10-19: the demo account is for learning and testing strategies;
the research and program directors' ruling, ADR 0037 amendment). `Intent.learning_probe` skips only the edge checks
after costs; the gate re-checks the demo lock itself (F1's rule), the minimum lot and a positive gross edge, and keeps
every account and sizing limit."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits

NOW = pd.Timestamp("2026-10-26 10:00", tz="UTC")


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000,
                                week_start_equity=10_000, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    return AccountState(**{**base, **kw})


def _probe(tmp_path: Path, phase: Any = None, **kw: Any) -> Intent:
    """A minimum-lot learning probe whose EV after costs is negative but whose gross edge is positive."""
    phase_file = tmp_path / "phase_state.json"
    if phase is not None:
        phase_file.write_text(phase if isinstance(phase, str) else json.dumps(phase))
    base: dict[str, Any] = dict(agent_id="cost_probe:breakout-g0-x", side=1, p=0.42, target_atr=1.5, stop_atr=1.0,
                                atr_usd=4.0, cost_atr=0.3, multiplier=1.0, price=2400.0, timeframe="1h",
                                family="breakout", volume_min=0.01, volume_max=0.01, learning_probe=True,
                                account_mode="demo", phase_file=phase_file)
    return Intent(**{**base, **kw})


def test_a_learning_probe_skips_the_edge_after_costs_and_nothing_else(tmp_path):
    probe = _probe(tmp_path)
    assert probe.p * probe.target_atr - (1 - probe.p) * probe.stop_atr - probe.cost_atr <= 0     # negative EV
    assert RiskGate().check(probe, _state(), NOW).allowed
    d = RiskGate().check(probe.model_copy(update={"learning_probe": False}), _state(), NOW)
    assert not d.allowed and "negative_ev" in d.reasons                    # the same order, not a probe: refused


def test_a_learning_probe_below_the_cost_floor_or_on_a_standard_account_passes(tmp_path):
    assert RiskGate().check(_probe(tmp_path, p=0.8, target_atr=0.5), _state(), NOW).allowed
    assert RiskGate().check(_probe(tmp_path, timeframe="15m"), _state(account_class="standard"), NOW).allowed


@pytest.mark.parametrize("mode, phase, allowed", [
    ("demo", None, True),                                                         # no phase file: phase 0
    ("demo", {"phase": 2, "gates_passed": ["foundation_to_backtest", "backtest_to_paper"]}, True),
    ("live", None, False),
    (None, None, False),                                                          # the caller did not say: refuses
    ("demo", {"gates_passed": ["paper_to_tiny_live"]}, False),                    # after the live gate: never
    ("demo", "{not json", False),                                                 # corrupt: refuses
])
def test_the_gate_checks_the_demo_lock_itself(tmp_path, mode, phase, allowed):
    d = RiskGate().check(_probe(tmp_path, phase, account_mode=mode), _state(), NOW)
    assert d.allowed is allowed, d
    assert ("learning_probe_not_demo" in d.reasons) is (not allowed)


def test_a_learning_probe_without_a_phase_file_path_refuses(tmp_path):
    d = RiskGate().check(_probe(tmp_path, phase_file=None), _state(), NOW)
    assert not d.allowed and "learning_probe_not_demo" in d.reasons


def test_a_learning_probe_is_the_minimum_lot_only(tmp_path):
    d = RiskGate().check(_probe(tmp_path, volume_max=2.0), _state(), NOW)
    assert not d.allowed and "learning_probe_not_min_lot" in d.reasons


def test_a_learning_probe_needs_a_positive_edge_before_costs(tmp_path):
    d = RiskGate().check(_probe(tmp_path, p=0.3), _state(), NOW)              # 0.3 x 1.5 < 0.7 x 1.0
    assert not d.allowed and "negative_gross_edge" in d.reasons


def test_a_time_exit_rule_still_refuses(tmp_path):
    d = RiskGate().check(_probe(tmp_path, has_target=False), _state(), NOW)
    assert not d.allowed and "time_exit_ev_unsupported" in d.reasons


@pytest.mark.parametrize("state, limits, reason", [
    ({"supervisor_halt": True}, {}, "supervisor_halt"),
    ({"equity": 9_800}, {}, "daily_cap"),
    ({"open_risk": 80.0}, {"daily_loss_limit_abs": 60.0, "project_open_risk": True}, "daily_loss_limit_abs"),
])
def test_every_account_limit_still_applies_to_a_learning_probe(tmp_path, state, limits, reason):
    d = RiskGate(RiskLimits(**limits)).check(_probe(tmp_path), _state(**state), NOW)
    assert not d.allowed and any(r.startswith(reason) for r in d.reasons), d

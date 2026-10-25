"""The RiskGate's exploration purpose (exploration-arms plan 2026-10-25, lane L1). An exploration arm trades to MEASURE
an edge, so the expected-value and gross-edge rules (`time_exit_ev_unsupported`, `negative_ev`,
`target_below_cost_floor`, the standard-account edge rules) cannot judge it; the purpose skips ONLY those, and only
inside its fence: a probe_only account, the demo lock, a terminal that itself reports demo, exactly the minimum lot, an
arm registered in the account's exploration config and a loss at the stop within that arm's per-trade cap. Halts, the
kill switch, rollover, the weekend, the daily and weekly caps and stale data still refuse. The account config
(`exploration:` in config/accounts.yaml) is refused at load unless the account is demo and probe_only, and its budget
share and the cost probe's together stay within the day's allowance."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.ops import accounts as acc_mod
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage

NOW = pd.Timestamp("2026-10-26 10:00", tz="UTC")
ARM = "box_break_1h:explore"


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=1_000, balance_closed_hwm=1_000, day_start_equity=1_000,
                                week_start_equity=1_000, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=20.0)
    return AccountState(**{**base, **kw})


def _limits(**kw: Any) -> RiskLimits:
    """A probe_only account with one registered arm whose per-trade cap is 3.00 (account currency)."""
    base: dict[str, Any] = dict(probe_only=True, exploration_arms={ARM: 3.0})
    return RiskLimits(**{**base, **kw})


def _arm(tmp_path: Path, phase: Any = None, **kw: Any) -> Intent:
    """An exploration entry with a NEGATIVE expected value (p 0.3, 1:1, costs on top): 0.01 lot, a $3 stop, so the
    loss at the stop is 0.01 x 100 x 3 = 3.00, exactly the arm's cap."""
    phase_file = tmp_path / "phase_state.json"
    if phase is not None:
        phase_file.write_text(json.dumps(phase))
    base: dict[str, Any] = dict(agent_id="explore:box_break_1h", side=1, p=0.3, target_atr=1.0, stop_atr=1.0,
                                atr_usd=3.0, cost_atr=0.1, multiplier=1.0, price=2400.0, timeframe="1h",
                                family="box_break", volume_min=0.01, volume_max=0.01, account_mode="demo",
                                phase_file=phase_file, purpose="exploration", terminal_trade_mode="demo", arm_id=ARM)
    return Intent(**{**base, **kw})


def _check(intent: Intent, limits: RiskLimits | None = None, **state: Any):
    return RiskGate(limits or _limits()).check(intent, _state(**state), NOW)


EDGE_RULES = {"negative_ev", "target_below_cost_floor", "time_exit_ev_unsupported", "negative_gross_edge",
              "standard_account_edge", "standard_account_15m"}


# ---------------------------------------------------------------- the fence
def test_exploration_skips_only_the_ev_rule_inside_its_fence(tmp_path):
    d = _check(_arm(tmp_path))
    assert d.allowed, d
    assert d.lots == pytest.approx(0.01)
    # the same entry as a signal is refused for its expected value
    signal = _check(_arm(tmp_path, purpose="signal"))
    assert not signal.allowed and "negative_ev" in signal.reasons
    # a time exit (no reachable target) is not judged on EV inside the fence either
    assert _check(_arm(tmp_path, has_target=False)).allowed
    # outside the fence the EV rules apply again, next to the fence's own reason
    out = _check(_arm(tmp_path), _limits(probe_only=False))
    assert not out.allowed and {"exploration_not_probe_only", "negative_ev"} <= set(out.reasons)


def test_exploration_on_a_standard_account_skips_the_edge_rules_only_inside_its_fence(tmp_path):
    assert _check(_arm(tmp_path, timeframe="15m"), account_class="standard").allowed
    d = _check(_arm(tmp_path, timeframe="15m", terminal_trade_mode="real"), account_class="standard")
    assert not d.allowed and {"standard_account_15m", "standard_account_edge"} <= set(d.reasons)


def test_refused_when_not_probe_only(tmp_path):
    d = _check(_arm(tmp_path), _limits(probe_only=False))
    assert not d.allowed and "exploration_not_probe_only" in d.reasons


@pytest.mark.parametrize("terminal", ["real", "contest", None])
def test_refused_when_terminal_not_demo(tmp_path, terminal):
    d = _check(_arm(tmp_path, terminal_trade_mode=terminal))
    assert not d.allowed and "exploration_terminal_not_demo" in d.reasons


@pytest.mark.parametrize("mode, phase", [("live", None), (None, None), ("demo", {"gates_passed": ["paper_to_tiny_live"]}),
                                         ("demo", "not json")])
def test_refused_on_a_live_registry_entry_or_after_the_live_gate(tmp_path, mode, phase):
    if phase == "not json":
        (tmp_path / "phase_state.json").write_text("{")
        phase = None
    d = _check(_arm(tmp_path, phase, account_mode=mode))
    assert not d.allowed and "exploration_not_demo" in d.reasons


def test_refused_above_the_minimum_lot(tmp_path):
    d = _check(_arm(tmp_path, volume_max=0.02))
    assert not d.allowed and "exploration_not_min_lot" in d.reasons


def test_loss_equal_to_arm_cap_passes_one_penny_above_refused(tmp_path):
    assert _check(_arm(tmp_path), _limits(exploration_arms={ARM: 3.00})).allowed
    d = _check(_arm(tmp_path), _limits(exploration_arms={ARM: 2.99}))
    assert not d.allowed and d.reasons == ["exploration_arm_cap"]
    # the loss is measured on the stop the gate sets: a stops level wider than the asked stop counts
    wide = _check(_arm(tmp_path, stops_level_points=400.0))
    assert not wide.allowed and "exploration_arm_cap" in wide.reasons


def test_unregistered_arm_refused(tmp_path):
    for arm in ("some_other_arm", None, ""):
        d = _check(_arm(tmp_path, arm_id=arm))
        assert not d.allowed and "exploration_arm_unregistered" in d.reasons
    d = _check(_arm(tmp_path), _limits(exploration_arms={}))
    assert not d.allowed and "exploration_arm_unregistered" in d.reasons


@pytest.mark.parametrize("cap", [0.0, -1.0, float("nan"), float("inf")])
def test_an_arm_with_no_usable_cap_is_refused(tmp_path, cap):
    d = _check(_arm(tmp_path), _limits(exploration_arms={ARM: cap}))
    assert not d.allowed and "exploration_arm_unregistered" in d.reasons


@pytest.mark.parametrize("state, reason", [
    (dict(owner_halt=True), "owner_halt"),
    (dict(supervisor_halt=True), "supervisor_halt"),
    (dict(stage=Stage.HALTED), "drawdown_halt"),
    (dict(in_rollover=True), "rollover"),
    (dict(weekend=True), "weekend"),
    (dict(stale_bars=True), "stale_data"),
    (dict(last_tick_age_s=60.0), "stale_data"),
    (dict(equity=975.0, balance_closed_hwm=975.0), "daily_cap"),
    (dict(equity=940.0, balance_closed_hwm=940.0, day_start_equity=940.0), "weekly_cap"),
])
def test_halt_rollover_weekend_still_refuse(tmp_path, state, reason):
    d = _check(_arm(tmp_path), **state)
    assert not d.allowed and reason in d.reasons


def test_the_absolute_daily_loss_limit_still_refuses(tmp_path):
    d = _check(_arm(tmp_path), _limits(daily_loss_limit_abs=3.0))
    assert not d.allowed and d.reasons == ["daily_loss_limit_abs"]


def test_a_signal_or_cost_probe_ignores_the_arm_fields(tmp_path):
    """The fence belongs to the purpose: an arm id on a signal intent opens nothing."""
    d = _check(_arm(tmp_path, purpose="signal"))
    assert not d.allowed and not any(r.startswith("exploration_") for r in d.reasons)


# ---------------------------------------------------------------- the account config
def _account(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="icm-probe", broker="icm", mode="demo", server="S", login=None,
                                terminal_path="t", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=1,
                                enabled=True, daily_loss_limit_abs=220.0,
                                cost_probe=acc_mod.CostProbe(enabled=True, budget_share=0.9, max_open=4, max_loss=45.0),
                                shadow_host=True, probe_only=True,
                                exploration=dict(enabled=True, budget_share=0.1, max_open_total=2,
                                                 arms={ARM: {"max_loss": 10.0, "style": "scalp"}},
                                                 pots={"scalp": {"gbp": 20.0, "max_open": 2}}))   # ADR 0043 pots
    return acc_mod.Account(**{**base, **kw})


def test_exploration_is_off_by_default_and_registers_no_arm():
    acc = _account(exploration=acc_mod.Exploration())
    assert not acc.exploration.enabled and acc.exploration.arms == {}
    assert acc_mod.exploration_arm_caps(acc) == {}


def test_an_enabled_exploration_config_yields_its_arm_caps():
    assert acc_mod.exploration_arm_caps(_account()) == {ARM: 10.0}


def test_exploration_config_refused_on_a_live_account():
    with pytest.raises(ValidationError, match="exploration"):
        _account(mode="live", cost_probe=acc_mod.CostProbe(), shadow_host=False, probe_only=False)


def test_exploration_config_refused_on_an_account_that_is_not_probe_only():
    with pytest.raises(ValidationError, match="probe_only"):
        _account(probe_only=False)


def test_exploration_and_cost_probe_budget_shares_must_fit_in_the_day():
    with pytest.raises(ValidationError, match="budget_share"):
        _account(exploration=dict(enabled=True, budget_share=0.11, max_open_total=2, arms={}))
    assert _account(exploration=dict(enabled=True, budget_share=0.1, max_open_total=2, arms={})).exploration.enabled


def test_an_arm_cap_above_the_exploration_budget_is_refused():
    with pytest.raises(ValidationError, match="max_loss"):
        _account(exploration=dict(enabled=True, budget_share=0.1, max_open_total=2, arms={ARM: {"max_loss": 22.01}}))


@pytest.mark.parametrize("bad", [dict(budget_share=0.0), dict(budget_share=1.1), dict(max_open_total=0),
                                 dict(arms={ARM: {"max_loss": 0.0}}), dict(unknown_key=1)])
def test_malformed_exploration_values_are_refused(bad):
    with pytest.raises(ValidationError):
        acc_mod.Exploration(**bad)


def test_a_disabled_exploration_block_never_opens_an_arm_even_on_a_live_account():
    """Off is inert: the block is not judged on an account that does not use it, and no arm cap reaches the gate."""
    acc = _account(mode="live", cost_probe=acc_mod.CostProbe(), shadow_host=False, probe_only=False,
                   exploration=dict(enabled=False, budget_share=0.1, max_open_total=2, arms={ARM: {"max_loss": 10.0}}))
    assert acc_mod.exploration_arm_caps(acc) == {}


def test_the_shipped_registry_loads_with_exploration_off_on_icm_probe(tmp_path):
    cfg = tmp_path / "accounts.yaml"
    cfg.write_text(acc_mod.ACCOUNTS_FILE.read_text())
    accs = acc_mod.load_accounts(cfg, logins=False)
    probe = accs["icm-probe"]
    assert not probe.exploration.enabled
    assert acc_mod.exploration_arm_caps(probe) == {}
    raw = yaml.safe_load(cfg.read_text())
    assert "exploration" in raw["accounts"]["icm-probe"]

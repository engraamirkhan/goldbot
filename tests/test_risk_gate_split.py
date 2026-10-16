"""ADR 0021: `RiskGate.check_operational` shares one code path with `check` for every account and limit check, and
skips only the model checks. Fence F5: the split of `check` into shared helpers is a pure refactor, shown by a
decision grid captured from `check` BEFORE the split (tests/data/risk_gate_decision_grid.json). F1, F2, S6, S10 below.

The grid golden is written only for an intended change of `check`'s decisions, never to make a refactor pass:
GOLDBOT_WRITE_GATE_GRID=1 pytest tests/test_risk_gate_split.py -k grid, which captures it from 9f04599's gate.py
(`pre_split_gate`), never from HEAD; the trading-safety reviewer signs any change to it.
"""
from __future__ import annotations

import inspect
import json
import math
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

import goldbot.risk.gate as gate_module
from goldbot.config import HeatSettings
from goldbot.risk.gate import OperationalIntent, RiskGate, RiskLimits

GRID = Path(__file__).parent / "data" / "risk_gate_decision_grid.json"
WRITE = os.environ.get("GOLDBOT_WRITE_GATE_GRID") == "1"
NOW = pd.Timestamp("2026-10-26 10:00", tz="UTC")
PRE_SPLIT = "9f04599"                                  # the commit before the split (F5)


def _state(mod: ModuleType = gate_module, **kw: Any) -> Any:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000,
                                week_start_equity=10_000, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return mod.AccountState(**base)


def _intent(mod: ModuleType = gate_module, **kw: Any) -> Any:
    base: dict[str, Any] = dict(agent_id="session_open-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0,
                                cost_atr=0.1, multiplier=1.0, price=2400.0, timeframe="1h", family="session_open")
    base.update(kw)
    return mod.Intent(**base)


LIMITS: dict[str, dict[str, Any]] = {
    "default": {},
    "abs_projection": {"daily_loss_limit_abs": 60.0, "project_open_risk": True},
    "heat": {"heat": HeatSettings(enabled=True, max_open_risk=0.01, max_same_direction=1)},
    "min_lot_cap": {"min_lot_risk_cap": 0.01, "daily_loss_limit_abs": 200.0},
}
STATES: dict[str, dict[str, Any]] = {
    "base": {},
    "day_loss_at_cap": {"equity": 9_800},
    "week_loss": {"equity": 9_600, "day_start_equity": 9_600},
    "week_loss_at_cap": {"equity": 9_500, "day_start_equity": 9_500},           # weekly_cap (review LOW-3)
    "size_down": {"stage": "size_down"},
    "dd_to_size_down": {"balance_closed_hwm": 11_000},
    "dd_to_halt": {"balance_closed_hwm": 11_500},
    "combined_size_down": {"combined_size_down": True},
    "margin_heavy": {"margin_used": 3_200.0},
    "combined_near_cap": {"other_lots": 2.95, "other_equity": 10_000.0},
    "open_risk": {"open_risk": 45.0, "open_long": 1, "open_positions": 1},
    "open_risk_unknown": {"open_risk": None},
    "lost_some": {"equity": 9_960, "open_risk": 10.0},
    "open_risk_high": {"open_risk": 80.0},                                      # heat_cap (review LOW-3)
    "standard": {"account_class": "standard"},
    "unknown_class": {"account_class": "unknown"},
    "halts": {"supervisor_halt": True, "owner_halt": True, "equity_unconfirmed": True},
    "invalid_equity": {"equity": 0.0},
    "nan_spread": {"spread_points": math.nan},
    "wide_spread": {"spread_points": 60.0},
}
INTENTS: dict[str, dict[str, Any]] = {
    "base": {},
    "negative_ev": {"p": 0.3},
    "below_cost_floor": {"target_atr": 0.2},
    "tight_stop": {"stop_atr": 0.05},
    "wide_stop": {"stop_atr": 40.0},
    "wide_stop_ev": {"stop_atr": 40.0, "target_atr": 80.0},                    # min_lot_exceeds_risk (review LOW-3)
    "min_lot_stop": {"stop_atr": 20.0, "target_atr": 40.0},                    # min_lot_exception (review LOW-3)
    "standard_15m": {"timeframe": "15m", "family": "breakout", "agent_id": "breakout-g0-x"},
    "gbp_no_fx": {"account_currency": "GBP"},
    "gbp_fx": {"account_currency": "GBP", "acct_per_usd": 0.78, "swap_usd_per_oz": 2.0},
    "short_costly": {"side": -1, "cost_atr": 0.5, "target_atr": 3.0},
    "nan_price": {"price": math.nan},
}


def _margin(name: str) -> Any:
    if name == "none":
        return None
    if name == "broker_high":
        return lambda side, lots, price: 900.0 * lots
    def boom(side: int, lots: float, price: float) -> float:
        raise RuntimeError("terminal down")
    return boom


def _row(d: Any) -> list[Any]:
    return [d.allowed, d.lots, list(d.reasons), round(d.risk_fraction, 10), round(d.stop_distance, 10),
            round(d.margin_needed, 6), d.margin_note, round(d.risk_amount, 6), round(d.cost_amount, 6)]


def _grid(mod: ModuleType = gate_module) -> dict[str, list[Any]]:
    """`mod` is goldbot.risk.gate, or the pre-split gate.py loaded from 9f04599 to (re)capture the golden: the
    expected values never come from the code under test."""
    out: dict[str, list[Any]] = {}
    margins = ("none", "broker_high", "raises")
    for i, (ln, lkw) in enumerate(LIMITS.items()):
        gate = mod.RiskGate(mod.RiskLimits(**lkw))
        for j, (sn, skw) in enumerate(STATES.items()):
            for k, (iname, ikw) in enumerate(INTENTS.items()):
                m = margins[(i + j + k) % 3]
                st = _state(mod, **skw)
                d = gate.check(_intent(mod, **ikw), st, NOW, margin_required=_margin(m))
                out[f"{ln}|{sn}|{iname}|{m}"] = [*_row(d), st.stage.value]
    return out


def pre_split_gate() -> ModuleType:
    """goldbot/risk/gate.py as it was before the split (9f04599), loaded as its own module."""
    import subprocess
    import sys
    src = subprocess.run(["git", "show", f"{PRE_SPLIT}:goldbot/risk/gate.py"], capture_output=True, text=True,
                         check=True, cwd=Path(__file__).resolve().parents[1]).stdout
    mod = ModuleType("gate_pre_split")
    sys.modules[mod.__name__] = mod                 # pydantic resolves the records' annotations through it
    exec(compile(src, f"{PRE_SPLIT}:goldbot/risk/gate.py", "exec"), mod.__dict__)
    return mod


def test_check_decisions_unchanged_on_a_state_grid():
    got = _grid()
    if WRITE:                                   # captured from the pre-split gate, never from HEAD
        got = _grid(pre_split_gate())                                   # one case per line, so a changed decision reads as a one-line diff
        GRID.parent.mkdir(parents=True, exist_ok=True)
        GRID.write_text("{\n" + ",\n".join(f"{json.dumps(k)}: {json.dumps(got[k], separators=(',', ':'))}"
                                           for k in sorted(got)) + "\n}\n")
        return
    want = json.loads(GRID.read_text())
    assert set(got) == set(want)
    diff = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
    assert not diff, diff
    assert sum(v[0] for v in got.values()) > 50 and sum(not v[0] for v in got.values()) > 50   # both kinds covered
    reached = {r for v in got.values() for r in v[2]}
    assert {"heat_cap", "min_lot_exception", "min_lot_exceeds_risk", "weekly_cap", "daily_cap", "margin_level_floor",
            "combined_exposure_cap", "daily_loss_limit_abs", "same_direction_cap"} <= reached, reached


# ---------------------------------------------------------------------------------------------- check_operational
def _op(tmp_path: Path, phase: Any = None, **kw: Any) -> OperationalIntent:
    phase_file = tmp_path / "phase_state.json"
    if phase is not None:
        phase_file.write_text(phase if isinstance(phase, str) else json.dumps(phase))
    base: dict[str, Any] = dict(side=1, price=2400.0, contract_oz=100.0, volume_min=0.01, volume_step=0.01,
                                volume_max=100.0, stops_level_points=0.0, account_currency="USD", acct_per_usd=1.0,
                                account_mode="demo", phase_file=phase_file)
    base.update(kw)
    return OperationalIntent(**base)


@pytest.mark.parametrize("mode, phase, allowed", [
    ("demo", None, True),                                                         # no phase file: phase 0
    ("demo", {"phase": 2, "gates_passed": ["foundation_to_backtest", "backtest_to_paper"]}, True),
    ("live", None, False),
    ("demo", {"phase": 3, "gates_passed": ["foundation_to_backtest", "backtest_to_paper", "paper_to_tiny_live"]},
     False),                                                                      # after the live gate: never
    ("demo", {"gates_passed": ["tiny_live_to_full_size"]}, False),               # any gate from the live one on
    ("demo", "{not json", False),                                                 # corrupt: refuses
    ("demo", "[1, 2]", False),                                                    # not an object: refuses
    ("demo", {"phase": 2, "gates_passed": "paper_to_tiny_live"}, False),          # malformed list: refuses
])
def test_operational_refuses_operational_not_demo_for_live_or_an_unreadable_phase(tmp_path, mode, phase, allowed):
    d = RiskGate().check_operational(_op(tmp_path, phase, account_mode=mode), _state(), NOW, None)
    assert d.allowed is allowed, d
    assert ("operational_not_demo" in d.reasons) is (not allowed)


def test_operational_live_gates_are_the_roadmap_gates_from_paper_to_tiny_live_on():
    from goldbot.ops.accounts import GATES
    from goldbot.risk.gate import LIVE_GATES
    assert LIVE_GATES == frozenset(GATES[GATES.index("paper_to_tiny_live"):])


def test_operational_has_no_lots_p_target_or_cost_parameter(tmp_path):
    params = set(inspect.signature(RiskGate.check_operational).parameters)
    assert params == {"self", "intent", "st", "now_utc", "margin_required"}
    fields = set(OperationalIntent.model_fields)
    for banned in ("lots", "p", "target_atr", "target", "cost_atr", "cost", "multiplier", "stop_atr", "atr_usd",
                   "swap_usd_per_oz", "agent_id", "has_target"):
        assert banned not in fields, banned
    # F2: exactly volume_min rounded down to volume_step, whatever the account could carry
    d = RiskGate().check_operational(_op(tmp_path, volume_min=0.015, volume_step=0.01), _state(equity=1e6,
                                     balance_closed_hwm=1e6, day_start_equity=1e6, week_start_equity=1e6), NOW, None)
    assert d.allowed and d.lots == 0.01
    # S6: the model checks are skipped, by name: a state check() would refuse only for them allows here
    d = RiskGate().check_operational(_op(tmp_path), _state(account_class="standard"), NOW, None)
    assert d.allowed and not {"time_exit_ev_unsupported", "negative_ev", "target_below_cost_floor",
                              "standard_account_edge", "standard_account_15m"} & set(d.reasons)
    # a minimum lot rounded down to nothing is refused, never sized up
    d = RiskGate().check_operational(_op(tmp_path, volume_min=0.01, volume_step=0.02), _state(), NOW, None)
    assert not d.allowed and "invalid_volume" in d.reasons


def test_operational_keeps_account_class_unknown(tmp_path):
    d = RiskGate().check_operational(_op(tmp_path), _state(account_class="unknown"), NOW, None)
    assert not d.allowed and "account_class_unknown" in d.reasons


@pytest.mark.parametrize("name, kw, limits, reason", [
    ("halts", {"supervisor_halt": True}, {}, "supervisor_halt"),
    ("stale", {"last_tick_age_s": 60.0}, {}, "stale_data"),
    ("margin", {"margin_used": 3_330.0}, {}, "margin_level_floor"),
    ("combined", {"other_lots": 2.995}, {}, "combined_exposure_cap"),
    ("heat", {"open_risk": 99.9}, {"heat": HeatSettings(enabled=True, max_open_risk=0.01)}, "heat_cap"),
    ("same_side", {"open_long": 2}, {"heat": HeatSettings(enabled=True, max_same_direction=2)}, "same_direction_cap"),
    ("risk_cap", {"equity": 50.0, "balance_closed_hwm": 50.0, "day_start_equity": 50.0, "week_start_equity": 50.0},
     {}, "min_lot_exceeds_risk"),
    ("fx", {}, {}, "fx_conversion_unavailable"),
    ("invalid", {"equity": math.nan}, {}, "invalid_state"),
])
def test_operational_applies_every_account_and_sizing_limit(tmp_path, name, kw, limits, reason):
    intent = _op(tmp_path, account_currency="GBP", acct_per_usd=None) if name == "fx" else _op(tmp_path)
    d = RiskGate(RiskLimits(**limits)).check_operational(intent, _state(**kw), NOW, None)
    assert not d.allowed and reason in d.reasons, d


def test_operational_daily_cap_refuses_at_equality(tmp_path):
    gate = RiskGate()
    just_under = gate.check_operational(_op(tmp_path), _state(equity=9_800.01), NOW, None)
    at_cap = gate.check_operational(_op(tmp_path), _state(equity=9_800.0), NOW, None)    # 2.0% down: refused
    assert just_under.allowed and not at_cap.allowed and "daily_cap" in at_cap.reasons


def test_operational_absolute_projection_allows_equality(tmp_path):
    # 0.01 lot, stop distance max(1.00, 0.25) USD/oz, 100 oz: 1.00 at the stop; no cost in the projection (no cost
    # parameter: ADR 0021). Lost 49.00 + 1.00 == 50.00 is allowed; one cent more is refused
    gate = RiskGate(RiskLimits(daily_loss_limit_abs=50.0))
    at = gate.check_operational(_op(tmp_path), _state(equity=9_951.0), NOW, None)
    over = gate.check_operational(_op(tmp_path), _state(equity=9_950.99), NOW, None)
    assert at.allowed and at.risk_amount == pytest.approx(1.0) and at.cost_amount == 0.0
    assert not over.allowed and over.reasons == ["daily_loss_limit_abs"]


def test_operational_stop_is_at_or_beyond_the_stops_level_plus_spread(tmp_path):
    gate = RiskGate()
    wide = gate.check_operational(_op(tmp_path, stops_level_points=150.0), _state(spread_points=30.0), NOW, None)
    assert wide.allowed and wide.stop_distance == pytest.approx(1.8)
    floor = gate.check_operational(_op(tmp_path), _state(spread_points=30.0), NOW, None)
    assert floor.stop_distance == pytest.approx(1.0)                  # OPERATIONAL_MIN_STOP_USD

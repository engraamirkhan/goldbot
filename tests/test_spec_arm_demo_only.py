"""A discovered arm (`spec_rule`, ADR 0058 phase 4) runs on demo accounts only, refused at every layer: the arm loader
for a live-class account, the engine's exploration config (`config_for`), the engine's arm selection
(`exploration.Exploration`), the account record and, unchanged, the RiskGate's exploration fence. Caps are inherited:
the arm's per-trade cap reaches the gate as any arm's does, and the gate holds a spec arm's entry to it.

The spec arm here is a G1 re-expression of box_break_close on 15m; nothing in it is market data."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.engine import exploration as X
from goldbot.engine.exploration import ExplorationConfig, Pot
from goldbot.engine.runner import EngineConfig
from goldbot.ops import accounts as A
from goldbot.research.arms import Arm, arm_hash, load_arms
from goldbot.research.autodisc.gen_combine import family_spec
from goldbot.research.autodisc.spec import ExitTemplate, canonical_json, spec_hash
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits

ROOT = Path(__file__).resolve().parents[1]
SPEC = family_spec("box_break_close", "15m", None, ExitTemplate(stop="atr_1.0", target="2R"))
ARM_ID = "D2610-1"
NOW = pd.Timestamp("2026-10-26 10:00", tz="UTC")


def spec_arm() -> Arm:
    a = Arm(id=ARM_ID, rule="spec_rule", timeframe="15m", max_open=1, forward_only=True,
            params={"spec": canonical_json(SPEC), "spec_hash": spec_hash(SPEC), "prereg": "docs/x.md#d1"})
    return a.model_copy(update={"config_hash": arm_hash(a)})


@pytest.fixture
def spec_file(tmp_path: Path) -> Path:
    a = spec_arm()
    row = {"id": a.id, "rule": a.rule, "timeframe": a.timeframe, "params": a.params, "max_open": 1,
           "forward_only": True, "status": "run", "config_hash": a.config_hash}
    p = tmp_path / "spec_arms.yaml"
    p.write_text("arms:\n  - " + json.dumps(row) + "\n")
    return p


@pytest.fixture
def probe(tmp_path: Path) -> A.Account:
    """icm-probe as config/accounts.yaml has it (no keyring read), with the spec arm registered."""
    shutil.copy(ROOT / "config" / "accounts.yaml", tmp_path / "accounts.yaml")
    acc = A.load_accounts(tmp_path / "accounts.yaml", logins=False)["icm-probe"]
    ex = acc.exploration
    arms = {**ex.arms, ARM_ID: A.ExplorationArm(style=next(iter(ex.pots)), max_loss=5.0)}
    return acc.model_copy(update={"exploration": ex.model_copy(update={"arms": arms})})


# ---------------------------------------------------------------- layer 1: config load
def test_the_arm_loader_refuses_a_spec_arm_for_a_live_class_account_and_loads_it_for_a_demo_one(spec_file):
    assert [a.id for a in load_arms(spec_file)] == [ARM_ID]
    with pytest.raises(ValueError, match="demo accounts only"):
        load_arms(spec_file, live_class=True)


def test_the_engine_config_runs_a_registered_spec_arm_on_a_demo_account_with_its_own_cap(probe, spec_file):
    cfg = X.config_for(probe, X.SPEC_PATH, spec_file)
    assert cfg is not None and ARM_ID in {a.arm_id for a in cfg.arms} and ARM_ID not in cfg.dropped
    assert A.exploration_arm_caps(probe)[ARM_ID] == 5.0          # the RiskGate's per-trade cap for it


def test_the_engine_config_refuses_a_spec_arm_on_an_account_that_is_not_demo(probe, spec_file):
    live = probe.model_copy(update={"mode": "live"})              # past the account validator, as a bug would be
    with pytest.raises(ValueError, match="demo accounts only"):
        X.config_for(live, X.SPEC_PATH, spec_file)


def test_a_live_account_cannot_register_any_exploration_arm_at_load(probe):
    with pytest.raises(ValidationError, match="demo probe_only"):
        A.Account.model_validate({**probe.model_dump(), "mode": "live", "approval_mode": "propose",
                                  "risk_overrides": None, "cost_probe": {}, "probe_only": False,
                                  "max_positions": None})


def test_a_spec_arm_id_that_shadows_an_arm_spec_id_is_refused(probe, tmp_path):
    a = spec_arm().model_copy(update={"id": "I2A"})
    a = a.model_copy(update={"config_hash": arm_hash(a)})
    p = tmp_path / "clash.yaml"
    p.write_text("arms:\n  - " + json.dumps({**a.model_dump(include={"id", "rule", "timeframe", "params",
                                                                       "max_open", "config_hash"})}) + "\n")
    with pytest.raises(ValueError, match="already ARM-SPEC"):
        X.config_for(probe, X.SPEC_PATH, p)


# ---------------------------------------------------------------- layer 2: engine arm selection
def _selection(tmp_path: Path, mode: str) -> tuple[Any, SimpleNamespace]:
    cfg = ExplorationConfig(enabled=True, pots={"intraday": Pot(gbp=40, max_open=2)},
                            arms=(X.Arm(spec=spec_arm(), style="intraday"),))
    eng = SimpleNamespace(cfg=EngineConfig(account_id="icm-probe", broker_name="icm", mode=mode,
                                           state_dir=str(tmp_path)),
                          decisions=[], _closed_through=None, store=None)
    return X.Exploration(eng, cfg), eng                           # type: ignore[arg-type]


def test_the_engine_selects_a_spec_arm_on_a_demo_engine(tmp_path):
    ex, eng = _selection(tmp_path, "demo")
    assert ARM_ID in ex.arms and not [d for d in eng.decisions if d["action"] == "exploration_arm_dropped"]


@pytest.mark.parametrize("mode", ["live", "paper"])
def test_the_engine_drops_and_journals_a_spec_arm_on_an_engine_that_is_not_demo(tmp_path, mode):
    ex, eng = _selection(tmp_path, mode)
    assert ARM_ID not in ex.arms and ex.by_key == {}
    assert [d["arm"] for d in eng.decisions if d["action"] == "exploration_arm_dropped"] == [ARM_ID]


# ---------------------------------------------------------------- the RiskGate fence, unchanged
def _intent(tmp_path: Path, **kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id=X.PREFIX + ARM_ID, side=1, p=0.3, target_atr=2.0, stop_atr=1.0, atr_usd=3.0,
                                cost_atr=0.1, multiplier=1.0, price=2400.0, timeframe="15m", family="spec_rule",
                                volume_min=0.01, volume_max=0.01, account_mode="demo",
                                phase_file=tmp_path / "phase_state.json", purpose="exploration",
                                terminal_trade_mode="demo", arm_id=ARM_ID)
    return Intent(**{**base, **kw})


def _check(intent: Intent, cap: float = 3.0) -> Any:
    state = AccountState(equity=1_000, balance_closed_hwm=1_000, day_start_equity=1_000, week_start_equity=1_000,
                         open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=20.0)
    return RiskGate(RiskLimits(probe_only=True, exploration_arms={ARM_ID: cap})).check(intent, state, NOW)


def test_the_gate_admits_a_spec_arm_entry_only_inside_the_demo_fence_and_its_cap(tmp_path):
    assert _check(_intent(tmp_path)).allowed
    for kw, reason in (({"account_mode": "live"}, "exploration_not_demo"),
                       ({"terminal_trade_mode": "real"}, "exploration_terminal_not_demo")):
        d = _check(_intent(tmp_path, **kw))
        assert not d.allowed and reason in d.reasons, d
    over = _check(_intent(tmp_path), cap=2.0)                      # $3 stop at 0.01 lot = 3.00 > the arm's 2.00
    assert not over.allowed and "exploration_arm_cap" in over.reasons

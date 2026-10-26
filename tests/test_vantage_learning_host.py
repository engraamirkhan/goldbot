"""ADR 0043 broker amendment (PD 2026-10-26; owner: all three accounts learn): vantage-demo is the third probe_only
learning host. It runs Vantage twins of the intraday and swing arms under their own ids ("an arm on a second broker is
a new hypothesis: a distinct arm id ... one registry trial ... its own single read at 300 admissible trades on its own
fills"), with its own scheduled cost probes, inside the demo ceilings and the 18-slot host cap."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.engine import Engine, EngineConfig, exploration
from goldbot.engine.exploration import ExplorationConfig
from goldbot.execution.broker import OrderIntent, OrderResult
from goldbot.ops import accounts as acc_mod
from goldbot.ops import jobs
from goldbot.ops.services.engine import engine_limits
from goldbot.research import arms
from goldbot.research import exploration_data as ex
from goldbot.research.exploration_data import PRIOR_BROKERS
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_exploration_engine import CLOSE, _arm, _at, _sent, _Terminal

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
TWINS = {"VI0": "I0", "VI2A": "I2A", "VI2B": "I2B", "VMB15": "MB15", "VMB30": "MB30", "VMB1H": "MB1H",
         "VMS15": "MSA15", "VW0": "W0", "VW1A": "W1A", "VW1B": "W1B", "VW2": "W2"}
V_INTRADAY = {"VI0", "VI2A", "VI2B", "VMB15", "VMB30", "VMB1H", "VMS15"}
V_SWING = {"VW0", "VW1A", "VW1B", "VW2"}


@pytest.fixture(scope="module")
def vantage() -> acc_mod.Account:
    return acc_mod.load_accounts(logins=False)["vantage-demo"]


# ------------------------------------------------------------------------------------------------ the registry entry
def test_vantage_demo_ships_configured_as_a_demo_probe_only_learning_host_but_switched_off(vantage):
    """Quant review of 10a18031 (PD choice): off in this change; the registry trials are recorded first, then a
    one-line change switches it on."""
    acc = vantage
    assert not acc.enabled and acc.mode == "demo" and acc.broker == "vantage" and acc.login is None
    assert acc.probe_only and not acc.shadow_host and acc.approval_mode == "auto" and acc.account_class == "raw"
    assert acc.symbol == "XAUUSD+" and acc.server_tz == "NY+7" and acc.magic_base == 260200
    assert acc.daily_loss_limit_abs == 200 and acc.max_positions == 18
    assert acc.risk_overrides is not None and acc.risk_overrides.profile == "demo-2026-10-18"


def test_vantage_demo_measures_its_own_costs_with_scheduled_probes_on_a_15_pound_share(vantage):
    cp = vantage.cost_probe
    assert cp.enabled and cp.scheduled.enabled
    assert cp.budget_share * 200 == pytest.approx(15, abs=0.01)
    assert cp.budget_share * cp.scheduled.budget_share * 200 == pytest.approx(12, abs=0.01)   # CFO 2026-10-26
    assert cp.max_loss <= cp.budget_share * 200


def test_vantage_demo_exploration_pots_are_98_intraday_and_82_swing_of_180(vantage):
    ex = vantage.exploration
    assert ex.enabled and set(ex.arms) == V_INTRADAY | V_SWING
    assert ex.budget_share * 200 == pytest.approx(180, abs=0.01) and ex.max_open_total == 17
    assert {k: (p.gbp, p.max_open) for k, p in ex.pots.items()} == {"intraday": (98, 5), "swing": (82, 1)}
    assert {a.max_loss for a in ex.arms.values() if a.style == "intraday"} == {17.0}
    assert {a.max_loss for a in ex.arms.values() if a.style == "swing"} == {40.0}
    assert {k for k, a in ex.arms.items() if a.control_share} == {"VI0", "VW0"}
    assert ex.budget_share + vantage.cost_probe.budget_share <= 1           # one day's allowance


def test_vantage_arms_start_from_the_flat_prior():
    assert "vantage" not in PRIOR_BROKERS


# ------------------------------------------------------------------------------------------------ the frozen spec
def test_every_vantage_arm_is_a_twin_of_an_icm_arm_with_the_same_rule_body_under_its_own_hash():
    spec = {a.id: a for a in arms.load_arms(SPEC)}                 # hash-checked on load
    for vid, twin in TWINS.items():
        v, t = spec[vid], spec[twin]
        assert v.twin_of == twin and v.status == "run"
        assert (v.rule, v.timeframe, v.params, v.max_open) == (t.rule, t.timeframe, t.params, t.max_open)
        assert v.config_hash == arms.arm_hash(v) != t.config_hash
        assert len(vid) <= 5


def test_vantage_arm_ids_and_order_id_keys_never_collide_with_any_other_arm():
    ids = [a.id for a in arms.load_arms(SPEC)]
    icm = [i for i in ids if i not in TWINS]
    assert not set(TWINS) & set(icm)
    keys = [hashlib.sha1(i.encode()).hexdigest()[:5] for i in ids]          # exploration.Arm.key: the order-id part
    assert len(set(keys)) == len(keys)


def test_broker_twins_get_no_history_run_so_vantage_charges_no_second_h60_look():
    runnable = arms.runnable_ids(SPEC)
    assert not set(TWINS) & set(runnable)
    assert {"I0", "W2", "S0"} <= set(runnable)


def test_a_twin_whose_body_differs_from_its_arm_or_names_no_arm_is_refused(tmp_path):
    def load(text: str) -> list[arms.Arm]:
        p = tmp_path / "a.yaml"
        p.write_text(text)
        return arms.load_arms(p, check_hash=False)

    base = "- {id: A, rule: random_bracket, timeframe: 15m, params: {stop_atr: 1.5}}\n"
    assert [a.id for a in load("arms:\n" + base + "- {id: VA, twin_of: A, rule: random_bracket, timeframe: 15m, "
                               "params: {stop_atr: 1.5}}\n")] == ["A", "VA"]
    with pytest.raises(ValueError, match="twin"):
        load("arms:\n" + base + "- {id: VA, twin_of: A, rule: random_bracket, timeframe: 15m, params: {stop_atr: 2}}\n")
    with pytest.raises(ValueError, match="twin"):
        load("arms:\n" + base + "- {id: VB, twin_of: B, rule: random_bracket, timeframe: 15m, params: {}}\n")


# ------------------------------------------------------------------------------------------------ engine and gate
def test_vantage_engine_config_builds_its_eleven_arms_from_the_spec(vantage):
    cfg = exploration.config_for(vantage)
    assert cfg is not None and cfg.dropped == ()
    assert {a.arm_id for a in cfg.arms} == V_INTRADAY | V_SWING and len(cfg.arms) == 11
    by = {a.arm_id: a for a in cfg.arms}
    assert by["VI0"].control_share == 0.30 and by["VW0"].control_share == 0.33
    assert by["VW1B"].tf == "1d" and by["VMB1H"].style == "intraday" and by["VW2"].style == "swing"


def test_vantage_riskgate_limits_carry_its_host_cap_probe_only_fence_and_arm_caps(vantage):
    limits = engine_limits(load_settings(), vantage)
    assert limits.max_positions == 18 and limits.probe_only and limits.daily_loss_limit_abs == 200
    assert limits.exploration_arms == {a: (17.0 if a in V_INTRADAY else 40.0) for a in V_INTRADAY | V_SWING}


class _Recorder(_Terminal):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.intents: list[OrderIntent] = []

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.intents.append(intent)
        return super().place_order(intent)


def test_a_vantage_exploration_order_is_sent_on_its_xauusd_plus_symbol(vantage, tmp_path):
    term = _Recorder(symbol=vantage.symbol, equity=10_000, adverse_slip_points=0.0)
    cfg = ExplorationConfig(enabled=True, budget_share=vantage.exploration.budget_share, max_open_total=17,
                            pots={"intraday": exploration.Pot(gbp=98, max_open=5)}, arms=(_arm("VI0"),))
    limits = engine_limits(load_settings(), vantage).model_copy(update={"exploration_arms": {"VI0": 17.0}})
    eng = Engine(EngineConfig(account_id=vantage.account_id, broker_name=vantage.broker, mode=vantage.mode,
                              approval_mode=vantage.approval_mode, declared_class="raw", state_dir=str(tmp_path),
                              owner_user_id=111, probe_only=True, magic_base=vantage.magic_base,
                              symbol=vantage.symbol, exploration_config=cfg),
                 term, [], {}, ApprovalCenter({111}), limits=limits, live_shares={})
    exploration.on_pass(eng, _at(eng, term, CLOSE))
    assert len(_sent(eng)) == 1
    assert [i.symbol for i in term.intents] == ["XAUUSD+"]
    assert [p.symbol for p in term.positions()] == ["XAUUSD+"]


# ------------------------------------------------------------------------------------------------ scheduled share
def _host(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="a", broker="vantage", mode="demo", server="s", login=None,
                                terminal_path="t", server_tz="UTC", symbol="XAUUSD+", magic_base=1, enabled=True,
                                daily_loss_limit_abs=200.0, probe_only=True,
                                exploration=acc_mod.Exploration(enabled=True, budget_share=0.5, max_open_total=1,
                                                                arms={}, pots={}))
    return acc_mod.Account(**{**base, **kw})


def _probe(share: float) -> acc_mod.CostProbe:
    return acc_mod.CostProbe(enabled=True, budget_share=0.075, max_open=1, max_loss=4.0,
                             scheduled=acc_mod.ScheduledProbe(enabled=True, budget_share=share))


def test_scheduled_probes_may_take_more_than_half_the_probe_budget_only_where_no_mirror_probe_shares_it():
    """Prereg K10: scheduled probes take at most 50% of the probe budget so the mirrored probes keep the rest. A host
    without the shadow book sends no mirror probe, so nothing is taken from them (CFO 2026-10-26: 0.8 on
    vantage-demo); on the shadow host the 50% stands."""
    assert _host(cost_probe=_probe(0.8)).cost_probe.scheduled.budget_share == 0.8
    with pytest.raises(ValidationError, match="K10"):
        _host(cost_probe=_probe(0.8), shadow_host=True)
    assert _host(cost_probe=_probe(0.5), shadow_host=True).cost_probe.scheduled.budget_share == 0.5


# ------------------------------------------------------------------------------------------------ spread (quant 3)
def test_a_vantage_label_pays_vantages_own_spread_once_never_icms(tmp_path):
    """Labels charge the spread once, from the recording account's own bid/ask minutes (entry at the ask, exit at the
    bid for a long); the cost model adds slippage, commission and swap only (it has no spread term)."""
    from tests.test_exploration_broker import T0, _acc, _cand, _ctx, _db, _m1
    assert not {f for f in ex.ReplayCosts.model_fields if "spread" in f}
    db = _db(tmp_path)
    _cand(db, "v1", "vantage")                                       # a long, $2 stop and target, a 10-minute hold
    bars = {"icm-probe": _m1(T0, 120, spread=2.0), "vantage-demo": _m1(T0, 120, spread=0.3)}
    ctx = _ctx(tmp_path, [_acc("icm-probe", "icm"), _acc("vantage-demo", "vantage")], bars)
    slot = T0 + pd.Timedelta(hours=1)
    jobs.exploration_learn(ctx, slot)
    cm = jobs.exploration_costs(ctx, slot, "vantage")
    obs = ex.read_obs(db)
    got = float(obs.loc[obs["candidate_id"] == "v1", "label_sim_r"].iloc[0])
    assert got == pytest.approx((-0.3 - 2 * cm.slip - cm.commission_rt_per_oz) / 2.0)


def test_a_vantage_spread_profile_is_built_from_vantages_own_recorded_ticks_only(tmp_path):
    from tests.test_costs_gbp_and_tick_spread import NOW, _record, _weekdays
    _record(tmp_path, _weekdays(12))                                 # twelve recorded days of icm-demo ticks
    assert jobs.recorded_spread_profile(tmp_path, "icm-demo", NOW, window_days=30, min_days=10)[0] is not None
    prof, note = jobs.recorded_spread_profile(tmp_path, "vantage-demo", NOW, window_days=30, min_days=10)
    assert prof is None and "0 recorded days" in note

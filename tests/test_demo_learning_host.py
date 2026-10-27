"""ADR 0043 amendment (PD 2026-10-26, architect option c): icm-demo is a second probe_only exploration host running the
intraday and swing arms, icm-probe keeps the scalp arms and the cost probes. ADR 0037 amendment 6: a probe_only
account is a probe host when it runs cost probes with the shadow book OR exploration. Nothing a learning host does
reaches the phase gate, the limits evidence or the real accounts' combined limits."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.engine import Engine, EngineConfig, exploration, magic
from goldbot.engine.exploration import ExplorationConfig
from goldbot.ops import accounts as acc_mod
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.ops.limits_job import adaptive_enabled
from goldbot.ops.services.engine import engine_limits
from goldbot.risk.supervisor import Supervisor
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_exploration_engine import CLOSE, _arm, _at, _explore, _sent, _Terminal

SCALP = {"S0", "S0R", "S1A", "S1B", "S2A", "S2B", "S3A", "S3B", "S4", "S5A", "S5B", "F1A", "F1C",   # F1A/F1C: 2026-10-26 prereg
         "KA15", "XC15",                                                                     # wave 2 (8.8, 8.9)
         "Z1P", "Z2P", "Z5P", "Z8P", "Z7P", "Z4P"}                                           # H-81 (2026-10-27)
INTRADAY = {"I0", "I2A", "I2B", "MB15", "MB30", "MB1H", "MSA15", "G1A", "G1C",
            # arms expansion wave 1 (2026-10-26)
            "XBB15", "XTV1", "XEQ15", "XSO15", "XIMNY", "XIMLN", "XAD1H", "XMR15", "XMR1H", "C1H",
            # wave 2 (arms-2026-10-25.md section 8) and H-80's real-order runner
            "F2A", "F2C", "OBR15", "BXR15", "BXR30", "BXR1H"}
SWING = {"W0", "W1A", "W1B", "W2", "WFRI", "PDC4", "C30A", "C30C", "BXR4H"}
# H-81 (prereg-2026-10-27-scalp-volume.md 3.2; ADR 0049 D1a scalp pots): icm-demo's and vantage-demo's scalp strata
DEMO_SCALP = {"Z3D", "Z4D", "Z6D", "Z7D", "Z9D", "Z10D", "ZCD"}
VANTAGE_SCALP = {"Z6V", "Z7V", "ZCV"}
VANTAGE = {"VI0", "VI2A", "VI2B", "VMB15", "VMB30", "VMB1H", "VMS15", "VW0", "VW1A", "VW1B", "VW2",
           "VXRIP1H", "VXRT1H", "VXFK1H", "VXTL1H", "VXTV2", "VXBO1H", "VXBO15", "VXTR1H", "VC1H",   # wave 1
           "KSQ1", "PDC1", "DON4"}                                                                     # wave 2


def _account(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="a", broker="icm", mode="demo", server="s", login=None, terminal_path="t",
                                server_tz="UTC", symbol="XAUUSD", magic_base=1, enabled=True,
                                daily_loss_limit_abs=220.0)
    return acc_mod.Account(**{**base, **kw})


@pytest.fixture(scope="module")
def accs() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


# ------------------------------------------------------------------------------------------------ the validator
def test_a_probe_only_account_running_exploration_alone_is_a_probe_host():
    acc = _account(probe_only=True, exploration=_explore())
    assert acc.probe_only and not acc.shadow_host and not acc.cost_probe.enabled


def test_a_probe_only_account_with_neither_probes_nor_exploration_is_refused():
    with pytest.raises(ValidationError, match="probe_only"):
        _account(probe_only=True)


def test_a_live_account_is_never_a_probe_only_exploration_host():
    with pytest.raises(ValidationError):
        _account(mode="live", login=1, probe_only=True, exploration=_explore())


# ------------------------------------------------------------------------------------------------ the shipped registry
def test_icm_demo_ships_as_the_intraday_swing_and_h81_scalp_learning_host(accs):
    acc = accs["icm-demo"]
    ex = acc.exploration
    assert acc.enabled and acc.mode == "demo" and acc.probe_only and acc.login is None
    assert not acc.shadow_host and not acc.cost_probe.enabled and acc.max_positions == 18
    assert ex.enabled and set(ex.arms) == INTRADAY | SWING | DEMO_SCALP and set(ex.pots) == {"scalp", "intraday", "swing"}
    assert {(ex.arms[a].style, ex.arms[a].max_loss) for a in DEMO_SCALP} == {("scalp", 6.5)}
    assert ex.budget_share * 220 == pytest.approx(198, abs=0.05) and ex.max_open_total == 17
    assert sum(p.gbp for p in ex.pots.values()) == pytest.approx(198)


def test_icm_probe_ships_the_scalp_arms_and_the_cost_probes(accs):
    acc = accs["icm-probe"]
    ex = acc.exploration
    assert acc.shadow_host and acc.cost_probe.enabled and acc.cost_probe.budget_share * 220 == pytest.approx(60, abs=0.05)
    assert ex.enabled and set(ex.arms) == SCALP and set(ex.pots) == {"scalp"}
    assert ex.pots["scalp"].gbp == 138 and ex.max_open_total == 17       # one slot of 18 kept for the scheduled probe
    assert ex.budget_share * 220 == pytest.approx(138, abs=0.05)


def test_no_arm_runs_on_two_enabled_accounts(accs):
    seen: dict[str, str] = {}
    for aid, acc in accs.items():          # every configured host, switched on or not: enabling one never clashes
        if not acc.exploration.enabled:
            continue
        clash = {arm: seen[arm] for arm in acc.exploration.arms if arm in seen}
        assert not clash, f"{aid} shares arms with another host: {clash}"
        seen.update(dict.fromkeys(acc.exploration.arms, aid))
    assert set(seen) == SCALP | INTRADAY | SWING | VANTAGE | DEMO_SCALP | VANTAGE_SCALP


def test_each_hosts_exploration_magic_and_order_ids_are_its_own(accs, tmp_path):
    hosts = [a for a in accs.values() if a.exploration.enabled]           # configured, switched on or not
    assert len({a.magic_base + magic.EXPLORATION_OFFSET for a in hosts}) == len(hosts) == 3
    ids = set()
    for a in hosts:                                                         # the same arm and bar on both: never one id
        eng = _host_engine(tmp_path / a.account_id, a)
        ids.add(exploration.order_id(eng, _arm("I0"), CLOSE))
    assert len(ids) == 3


# ------------------------------------------------------------------------------------------------ the evidence fences
def _host_engine(tmp_path: Path, acc: acc_mod.Account) -> Engine:
    s = load_settings()
    cfg = ExplorationConfig(enabled=True, budget_share=acc.exploration.budget_share, max_open_total=17,
                            pots={k: exploration.Pot(gbp=p.gbp, max_open=p.max_open)
                                  for k, p in acc.exploration.pots.items()},
                            arms=(_arm("I0"),) if "intraday" in acc.exploration.pots else (_arm("S0", style="scalp"),))
    limits = engine_limits(s, acc).model_copy(update={"exploration_arms": {"I0": 17.0, "S0": 6.5}})
    return Engine(EngineConfig(account_id=acc.account_id, broker_name=acc.broker, mode=acc.mode,
                               approval_mode=acc.approval_mode, declared_class="raw", state_dir=str(tmp_path),
                               owner_user_id=111, probe_only=acc.probe_only, magic_base=acc.magic_base,
                               exploration_config=cfg),
                  _Terminal(equity=10_000, adverse_slip_points=0.0), [], {}, ApprovalCenter({111}), limits=limits,
                  live_shares={})


def test_an_icm_demo_exploration_fill_never_reaches_the_phase_gates_closed_trades(accs, tmp_path):
    eng = _host_engine(tmp_path, accs["icm-demo"])
    exploration.on_pass(eng, _at(eng, eng.broker, CLOSE))        # type: ignore[arg-type]
    assert len(_sent(eng)) == 1
    [pid] = list(eng.open)
    eng._try_close(pid, "time_exit", CLOSE)
    eng._flush_closed()
    [rec] = exploration.trades(str(tmp_path))
    assert rec.account_id == "icm-demo"
    assert load_closed_trades(tmp_path)[0] == []                 # gates_phase.evaluate_gates reads only this file


def test_icm_demo_is_out_of_the_limits_evidence_and_the_real_accounts_combined_limits(accs, tmp_path):
    base = load_settings()
    s = base.model_copy(update={"risk": base.risk.model_copy(update={"adaptive_limits": True})})
    acc = accs["icm-demo"]
    assert not adaptive_enabled(s, acc)                          # limits_job: a probe_only account gives no evidence
    assert engine_limits(s, acc).probe_only                      # the engine file then says probe_only: true
    for aid, equity in (("icm-demo", 500.0), ("icm-probe", 500.0)):    # a 50% day on each learning host
        (tmp_path / f"engine_{aid}.json").write_text(json.dumps({
            "account": aid, "mode": "demo", "ts": time.time(), "currency": "GBP", "equity": equity,
            "day_start_equity": 1_000.0, "week_start_equity": 1_000.0, "balance_closed_hwm": 1_000.0,
            "open_lots": 0.02, "open_notional": 4_000.0, "probe_only": True}))
    sup = Supervisor(tmp_path).evaluate()
    assert not sup["halt"] and sup["exposure"] == {} and set(sup["probe_engines"]) == {"icm-demo", "icm-probe"}

"""Owner rule 2026-10-30 (ADR 0061): "every strategy should have been back tested and qualified before it is tested and
learn from live trades that are taken on demo accounts". A strategy arm sends a demo entry only with a passing
`qualification` on its own row (a screen on 2010-2017 AND a confirm on 2018-2025-09, verdict pass, measured costs, every
doc it cites present in the repo); random controls and cost arms are exempt by an explicit `role`. Enforced twice: the
loader stamps the refusal (`arms.load_arms`), the engine's arm selection refuses again from the row itself. A refused
arm stays configured (as a spec retirement), so an open position keeps every exit; a bad qualification refuses its arm
and never stops the engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.engine import cost_probe, exploration
from goldbot.ops import accounts as acc_mod
from goldbot.ops import health
from goldbot.research import arm_rules, arms
from tests.test_exploration_engine import CLOSE, LOSS, QUALIFIED, SHORT, _arm, _at, _engine, _rule

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
RULE = "unqualified (owner rule 2026-10-30)"
CONTROLS = "S0 S0R I0 W0 VI0 VW0 C1H VC1H ZCD ZCV F1C G1C F2C C30C XC15 VS0".split()
COSTS = "F1A G1A".split()
# what each account may still enter on 2026-10-30 (exempt and not retired) and what it now refuses (registered strategy
# arms that A2 had not retired: the A2 retirements are refused anyway)
RUNNING = {"icm-demo": {"C1H", "G1A", "G1C", "I0", "W0", "ZCD"}, "icm-probe": {"F1A", "F1C", "S0", "S0R"},
           "vantage-demo": {"VC1H", "VI0", "VW0", "ZCV"}}
NEWLY_REFUSED = {"icm-demo": {"BXR15", "BXR1H", "BXR30", "BXR4H", "XBB15", "Z10D", "Z3D", "Z4D", "Z6D", "Z7D", "Z9D"},
                 "icm-probe": {"S1A", "S1B", "S5A", "S5B", "Z1P", "Z2P", "Z4P", "Z5P", "Z7P"},
                 "vantage-demo": {"Z6V", "Z7V"}}


def _row(arm_id: str = "Q1", **extra: Any) -> dict[str, Any]:
    a = arms.Arm(id=arm_id, rule="tsmom", timeframe="4h", params={"config": {}})
    return {**a.model_dump(exclude={"entry_refused"}), "config_hash": arms.arm_hash(a), **extra}


def _load(tmp_path: Path, rows: list[dict[str, Any]]) -> dict[str, arms.Arm]:
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump({"arms": rows}))
    return {a.id: a for a in arms.load_arms(path)}


# ------------------------------------------------------------------------------------------------ the load layer
def test_the_loader_keeps_an_unqualified_strategy_arm_configured_but_stamps_it_refused_for_entries(tmp_path) -> None:
    got = _load(tmp_path, [_row("Q1"), _row("Q2", qualification=QUALIFIED)])
    assert got["Q1"].entry_refused.startswith(RULE) and "no qualification" in got["Q1"].entry_refused
    assert got["Q2"].entry_refused == ""


def test_a_qualification_outside_the_hash_leaves_the_frozen_body_and_its_hash_alone() -> None:
    a = arms.Arm.model_validate(_row("Q1"))
    b = arms.Arm.model_validate(_row("Q1", qualification=QUALIFIED, role="strategy"))
    assert arms.arm_hash(a) == arms.arm_hash(b) == a.config_hash


@pytest.mark.parametrize("change, why", [
    ({"confirm": ""}, "invalid"),                                           # a screen alone is no qualification
    ({"screen": "docs/research/no-such-screen.md"}, "does not exist"),
    ({"confirm": "../outside.md"}, "does not exist"),
    ({"confirm": "docs/research/research-plan.md"}, "same reference"),
    ({"verdict": "fail"}, "verdict"),
    ({"verdict": "PASS"}, "verdict"),
    ({"cost_basis": "modelled"}, "cost_basis"),
    ({"confirm_window": "2018-01-01..2025-10-31"}, "holdout"),
    ({"screen_window": "2010-01-01..2018-06-30"}, "before the confirm"),
    ({"screen_window": "2010 to 2017"}, "window"),
])
def test_a_qualification_needs_a_screen_and_a_confirm_both_passing_on_measured_costs_before_the_holdout(
        tmp_path, change: dict[str, str], why: str) -> None:
    got = _load(tmp_path, [_row("Q1", qualification={**QUALIFIED, **change})])["Q1"]
    assert got.entry_refused.startswith(RULE) and why in got.entry_refused, got.entry_refused


def test_a_doc_reference_may_carry_an_anchor_and_a_registry_id_is_accepted_on_its_form(tmp_path) -> None:
    q = {**QUALIFIED, "screen": "docs/research/research-plan.md#table-3", "confirm": "registry:autodisc:abc123"}
    assert _load(tmp_path, [_row("Q1", qualification=q)])["Q1"].entry_refused == ""
    bad = {**QUALIFIED, "confirm": "registry:"}
    assert "does not exist" in _load(tmp_path, [_row("Q1", qualification=bad)])["Q1"].entry_refused


@pytest.mark.parametrize("bad", ["pass", ["pass"], {"verdict": "pass"}, 7])
def test_a_malformed_qualification_refuses_its_arm_and_never_stops_the_load(tmp_path, bad: Any) -> None:
    got = _load(tmp_path, [_row("Q1", qualification=bad), _row("Q2", qualification=QUALIFIED)])
    assert got["Q1"].entry_refused.startswith(RULE) and got["Q2"].entry_refused == ""


def test_a_written_entry_refused_value_is_overwritten_by_the_loader(tmp_path) -> None:
    assert _load(tmp_path, [_row("Q1", entry_refused="")])["Q1"].entry_refused.startswith(RULE)


def test_the_control_and_cost_roles_are_exempt_and_a_control_role_on_a_strategy_body_is_refused(tmp_path) -> None:
    rnd = arms.Arm(id="R1", rule="random_bracket", timeframe="15m",
                   params={"minutes": [0], "stop_atr": 1.5, "target_r": 2.0, "hold_bars": 4})
    rows = [{**rnd.model_dump(exclude={"entry_refused"}), "config_hash": arms.arm_hash(rnd), "role": "control"},
            _row("K1", role="cost"), _row("FAKE", role="control")]
    got = _load(tmp_path, rows)
    assert got["R1"].entry_refused == "" and got["K1"].entry_refused == ""
    assert got["FAKE"].entry_refused.startswith(RULE) and "control" in got["FAKE"].entry_refused


# ------------------------------------------------------------------------------------------------ the live spec
@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}


def test_every_exempt_arm_carries_its_role_and_every_other_arm_is_a_strategy_refused_today(spec) -> None:
    assert {i for i, a in spec.items() if a.role == "control"} == set(CONTROLS)
    assert {i for i, a in spec.items() if a.role == "cost"} == set(COSTS)
    for arm_id, a in spec.items():
        if a.role == "strategy":       # 2026-10-30: no family has qualified (H-16, H-80 R2, H-82..H-84, H-17 FAIL)
            assert a.entry_refused.startswith(RULE), arm_id
        else:
            assert a.entry_refused == "", arm_id


def test_every_account_runs_only_its_exempt_arms_and_keeps_every_refused_arm_configured() -> None:
    for acc in acc_mod.load_accounts(logins=False).values():
        cfg = exploration.config_for(acc)
        if cfg is None:
            continue
        configured = {a.arm_id for a in cfg.arms}
        refused = exploration.entry_refused(cfg)
        assert configured - set(refused) - exploration.spec_retired(cfg) == RUNNING[acc.account_id]
        assert NEWLY_REFUSED[acc.account_id] <= set(refused) <= configured
        assert all(refused[i].startswith(RULE) for i in NEWLY_REFUSED[acc.account_id])


# ------------------------------------------------------------------------------------------------ the engine layer
@pytest.fixture
def _fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)


def _unqualified(arm: exploration.Arm) -> exploration.Arm:
    """The arm as a row without a qualification, and without the loader's stamp: the engine must refuse it itself."""
    return arm.model_copy(update={"spec": arm.spec.model_copy(update={"qualification": None, "entry_refused": ""})})


def _sent(eng: Any) -> list[str]:
    return [d["arm"] for d in eng.decisions if str(d.get("action", "")).startswith("exploration:")]


def test_the_engine_never_enters_an_unqualified_arm_journals_why_and_enters_a_qualified_one(tmp_path, _fixed) -> None:
    eng, pb = _engine(tmp_path, [_arm("ok"), _unqualified(_arm("raw"))], qualify=False)
    ex = eng.exploration
    assert ex is not None and set(ex.arms) == {"ok", "raw"} and ex.retired == {"raw"}
    [j] = [d for d in eng.decisions if d.get("action") == "exploration_arm_unqualified"]
    assert j["arm"] == "raw" and j["rule"].startswith(RULE)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _sent(eng) == ["ok"]


def test_the_engine_refuses_an_arm_the_loader_stamped_even_with_a_qualification_on_its_row(tmp_path, _fixed) -> None:
    arm = _arm("stamped")
    stamped = arm.model_copy(update={"spec": arm.spec.model_copy(update={"entry_refused": RULE + ": test"})})
    eng, pb = _engine(tmp_path, [stamped], qualify=False)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert eng.exploration is not None and eng.exploration.retired == {"stamped"} and _sent(eng) == []


def test_an_exempt_control_without_a_qualification_enters(tmp_path, _fixed) -> None:
    arm = _unqualified(_arm("ctl"))
    ctl = arm.model_copy(update={"spec": arm.spec.model_copy(update={"role": "control",
                                                                     "params": {**arm.spec.params, "control": "t"}})})
    eng, pb = _engine(tmp_path, [ctl], qualify=False)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _sent(eng) == ["ctl"]


def test_a_position_opened_before_its_arm_was_refused_keeps_every_exit_after_a_restart(tmp_path, _fixed) -> None:
    arm = _arm("i-short", side=-1, stop_usd=15.0, target_r=3.0, hold_bars=8)   # stop and target out of the path
    eng, pb = _engine(tmp_path, [arm])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng2, _ = _engine(tmp_path, [_unqualified(arm)], pb=pb, qualify=False)
    eng2.open.clear()
    eng2._reconcile(CLOSE)
    eng2._retry_closes(CLOSE)
    assert pid in eng2.open and len(pb.positions()) == 1      # adopted, not closed as an orphan
    exploration.on_pass(eng2, _at(eng2, pb, CLOSE + pd.Timedelta(minutes=15)))
    assert _sent(eng2) == [] and pid in eng2.open


def test_one_arm_with_a_broken_qualification_never_stops_the_engine_and_the_others_enter(tmp_path, _fixed) -> None:
    arm = _arm("broken")
    broken = arm.model_copy(update={"spec": arm.spec.model_copy(update={"qualification": ["not", "a", "record"],
                                                                        "entry_refused": ""})})
    eng, pb = _engine(tmp_path, [_arm("ok"), broken], qualify=False)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert eng.exploration is not None and eng.exploration.retired == {"broken"} and _sent(eng) == ["ok"]


# ------------------------------------------------------------------------------------------------ health
def test_health_lists_the_exempt_arms_running_and_the_unqualified_arms_refused_per_account(tmp_path) -> None:
    from tests.test_health import make_ctx
    ctx = make_ctx(tmp_path).model_copy(update={"qualification": lambda: {
        "icm-demo": (["C1H", "I0"], ["XBB15", "Z3D"]), "vantage-demo": (["VI0"], [])}})
    c = health.check_qualification(ctx)
    assert c.name == "qualification" and c.status == "ok"
    assert c.reason == ("icm-demo: running C1H, I0; refused 2 (XBB15, Z3D); "
                        "vantage-demo: running VI0; refused 0")


def test_health_warns_when_an_exploring_account_has_no_arm_that_may_enter(tmp_path) -> None:
    from tests.test_health import make_ctx
    ctx = make_ctx(tmp_path).model_copy(update={"qualification": lambda: {"icm-demo": ([], ["XBB15"])}})
    assert health.check_qualification(ctx).status == "warn"


def test_the_runtime_qualification_view_matches_the_engine_per_account() -> None:
    view = exploration.qualification_by_account(acc_mod.enabled_accounts())
    for account_id, (running, refused) in view.items():
        assert set(running) == RUNNING[account_id]
        assert set(refused) == NEWLY_REFUSED[account_id]

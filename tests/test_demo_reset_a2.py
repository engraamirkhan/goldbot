"""Demo reset under A2 (docs/research/prereg-amendment-2026-10-30-kill-rules.md; evaluator ruling R4 in
config/arm_reads.yaml): "Retire now (on sign-off): every unit that made < 2 admissible fills a session day over
2026-10-27..10-30 and is not exempt", and "A retired unit keeps its K, its registry row and its arm id (never
reused)". The retirement is recorded on the arm's frozen spec row (`retired_day`, `retired_rule`, outside the hash), so
the row, the id and the hash stay; the engine refuses every new entry of a retired arm on every account and keeps it
configured, so an open position keeps its stop, target, hold and every exit (never closed as an orphan). The random
controls, the cost arms that feed a confirmation read (F1A/F1C, G1A/G1C) and H-80's runner (BXR*) stay active."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from goldbot.engine import cost_probe, exploration
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules, arms
from tests.test_exploration_engine import CLOSE, LOSS, SHORT, _arm, _at, _engine, _rule

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
RULE = "A2 readability, 2026-10-30"
# R4's list, as the evaluator wrote it (the 51 the plan names, then the ids the plan left implicit); XBB15 is retired
# only on a measured rate below 2 a session day, which this lane could not read (aux.db is on the brain): it stays
A2 = ("S2A S2B S3A S3B MSA15 VMS15 S4 I2A I2B VI2A VI2B W1A W1B VW1A VW1B W2 VW2 MB15 MB30 MB1H VMB15 VMB30 VMB1H WFRI "
      "XIMNY XIMLN XMR15 XMR1H XAD1H XSO15 XEQ15 XTV1 VXBO1H VXBO15 VXTR1H VXTV2 VXRIP1H VXRT1H VXFK1H VXTL1H "
      "Z8P KA15 OBR15 KSQ1 KSQ4 DON4 DOND C30A PDC1 PDC4 F2A "
      "F2C C30C XC15 S6 I1 I3 W3 VS0 VS1A VS1B VS2A VS2B VS3A VS3B VS5A VS5B").split()
EXEMPT = ("S0 S0R I0 W0 VI0 VW0 C1H VC1H ZCD ZCV F1A F1C G1A G1C BXR15 BXR30 BXR1H BXR4H").split()


@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}            # hash-checked: a retirement never edits the frozen body


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_the_a2_list_is_the_evaluators_sixty_seven_ids_without_a_duplicate() -> None:
    assert len(A2) == len(set(A2)) == 67


def test_every_a2_id_is_retired_on_its_own_registry_row_with_the_rule_and_keeps_its_id_and_hash(spec) -> None:
    for arm_id in A2:
        a = spec[arm_id]                                      # the row is still there (K and id kept)
        assert a.retired and a.retired_day == "2026-10-30", arm_id
        assert a.retired_rule.startswith(RULE), arm_id
        assert a.config_hash == arms.arm_hash(a)              # the frozen body is untouched


def test_nothing_but_the_a2_list_is_retired_and_every_control_and_exempt_arm_stays_active(spec) -> None:
    assert {i for i, a in spec.items() if a.retired} == set(A2)
    for arm_id in (*EXEMPT, "XBB15", "S1A", "S1B"):          # S1A/S1B: evaluator R3, read at their own read
        assert not spec[arm_id].retired and spec[arm_id].status == "run", arm_id


def test_a_retirement_needs_its_rule_and_a_day_and_stays_outside_the_hash(spec) -> None:
    base = spec["S2A"].model_dump()
    with pytest.raises(ValueError, match="retired_rule"):
        arms.Arm.model_validate({**base, "retired_rule": ""})
    with pytest.raises(ValueError, match="retired_day"):
        arms.Arm.model_validate({**base, "retired_day": None})
    with pytest.raises(ValueError, match="retired_day"):
        arms.Arm.model_validate({**base, "retired_day": "30 Oct"})
    live = arms.Arm.model_validate({**base, "retired_day": None, "retired_rule": ""})
    assert not live.retired and arms.arm_hash(live) == arms.arm_hash(spec["S2A"])


def test_every_retired_arm_on_every_account_is_refused_by_the_engines_arm_selection_and_kept_configured(
        accounts) -> None:
    for acc in accounts.values():
        cfg = exploration.config_for(acc)
        if cfg is None:
            continue
        registered = set(acc.exploration.arms)
        running = {a.arm_id for a in cfg.arms}
        refused = exploration.spec_retired(cfg)
        for arm_id in registered & set(A2):
            # blocked rows were never selected; every other retired arm is selected only to keep managing its exits
            assert arm_id in refused or arm_id in cfg.dropped, (acc.account_id, arm_id)
        assert not refused & set(EXEMPT)
        assert refused <= running


def test_retired_count_by_account(accounts) -> None:
    """The ids each account stops entering (registered there and on the A2 list)."""
    got = {k: len(set(a.exploration.arms) & set(A2)) for k, a in accounts.items() if a.exploration.enabled}
    assert got == {"icm-demo": 24, "icm-probe": 8, "vantage-demo": 20}


def test_every_pot_keeps_its_caps_and_sums_inside_the_budget_and_keeps_an_active_arm(accounts, spec) -> None:
    """Nothing is re-split: the pots and per-trade caps are the CFO's (tests/test_arms_wave2_registry.py), the controls'
    fixed shares unchanged; each pot still has an arm that may enter (a control at least)."""
    for acc in accounts.values():
        ex = acc.exploration
        if not ex.enabled:
            continue
        assert acc.daily_loss_limit_abs is not None
        assert sum(p.gbp for p in ex.pots.values()) <= ex.budget_share * acc.daily_loss_limit_abs + 1e-9
        assert all(a.max_loss <= {"scalp": 6.5, "intraday": 30.0, "swing": 40.0}[str(a.style)]
                   for a in ex.arms.values())
        for style in ex.pots:
            mine = [i for i, a in ex.arms.items() if a.style == style]
            assert sum(ex.arms[i].control_share or 0.0 for i in mine) <= 1
            assert any(not spec[i].retired and spec[i].status == "run" for i in mine), (acc.account_id, style)


def _retired(arm: exploration.Arm) -> exploration.Arm:
    return arm.model_copy(update={"spec": arm.spec.model_copy(update={"retired_day": "2026-10-30",
                                                                      "retired_rule": RULE})})


@pytest.fixture
def _fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)


def test_the_engine_sends_no_entry_of_a_spec_retired_arm_journals_it_and_runs_the_rest(tmp_path, _fixed) -> None:
    eng, pb = _engine(tmp_path, [_arm("ok"), _retired(_arm("gone"))])
    ex = eng.exploration
    assert ex is not None and set(ex.arms) == {"ok", "gone"} and ex.retired == {"gone"}
    assert [(d["arm"], d["rule"]) for d in eng.decisions if d.get("action") == "exploration_arm_retired"] \
        == [("gone", RULE)]
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert [d["arm"] for d in eng.decisions if str(d.get("action", "")).startswith("exploration:")] == ["ok"]


def test_a_position_opened_before_the_retirement_keeps_every_exit_after_a_restart(tmp_path, _fixed) -> None:
    arm = _arm("i-short", side=-1, stop_usd=15.0, target_r=3.0, hold_bars=8)   # stop and target out of the path
    eng, pb = _engine(tmp_path, [arm])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng2, _ = _engine(tmp_path, [_retired(arm)], pb=pb)
    eng2.open.clear()
    eng2._reconcile(CLOSE)
    eng2._retry_closes(CLOSE)
    assert pid in eng2.open and len(pb.positions()) == 1      # adopted, not closed as an orphan
    later = CLOSE + pd.Timedelta(minutes=15)
    exploration.on_pass(eng2, _at(eng2, pb, later))
    assert not [d for d in eng2.decisions if str(d.get("action", "")).startswith("exploration:")]

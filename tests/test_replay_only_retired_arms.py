"""Retired no-evidence arms record again as replay-only (owner 2026-11-24, "more zero-risk learning data";
docs/research/prereg-amendment-2026-11-24-replay-only-retired-arms.md).

* A retired gold arm whose row says `control_mode: replay_only` keeps its retirement (it sends nothing, on every
  account) but its rule runs again: every candidate is recorded in exploration_obs with taken 0 and blocked
  `replay_only`, and labelled by the existing r2 replay. Nothing it signals reaches the queue, the RiskGate, an order
  intent or the order core.
* The red-box rule records again under new ids (RBR15/RBR1H/RBR4H and their random-ladder twins RBRC*): the old rows'
  hashed `end_utc` stops them at 2026-11-22 22:00 UTC. The new rows are born retired and replay-only, with no learning
  block and no qualification, so they add no K row.
* Arms left retired keep recording nothing: GS1/GS1C (their pending read is truncated at 2026-11-22), the old red-box
  rows and every non-gold row (the r2 replay labels gold only).
* Bounded: at most REPLAY_RETIRED_DAILY_CAP records a UTC day per engine from retired arms.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.engine import cost_probe, exploration, exploration_deregister
from goldbot.engine.exploration import LEDGER, Arm
from goldbot.research import arm_rules
from goldbot.research import arms as arms_mod
from goldbot.research import exploration_data as ed
from goldbot.research.arms import Arm as SpecArm
from tests.test_exploration_daily_budget import START, _engine
from tests.test_exploration_engine import (
    _actions,
    _arm,
    _at,
    _rules,  # noqa: F401 - the always-long rule and the fixed loss (autouse)
    _sent,
)

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.usefixtures("_rules")

# the gold arms retired 2026-11-13..11-23 for lack of evidence (kill reads, cost budgets, controls with no live arm)
REVIVED = ("S1A", "S1B", "F1A", "F1C", "G1A", "G1C", "Z1P", "Z2P", "Z5P", "LCZ1P", "LCZ2P", "LCZ5P", "Z3D", "Z9D",
           "Z10D", "LCZ3D", "LCZ9D", "LCZ10D", "ZCD", "IEX15")
REDBOX_REPLAY = {"RBR15": "RB15", "RBR1H": "RB1H", "RBR4H": "RB4H",
                 "RBRC15": "RBC15", "RBRC1H": "RBC1H", "RBRC4H": "RBC4H"}
REPLAY_BORN_RETIRED = frozenset(REDBOX_REPLAY)      # the new rows, retired from birth (other tests' pinned sets)
LEFT_RETIRED = ("GS1", "GS1C", "RB15", "RB1H", "RB4H", "S0-XAGUSD", "LB-US500")
# replay-only since the red-box week, so they would record again, but their hashed end_utc decides no candidate after
# the week's end: RBRC* records the random ladder from now on
ENDED = ("RBC15", "RBC1H", "RBC4H")


def _rows() -> dict[str, SpecArm]:
    return {a.id: a for a in arms_mod.load_arms(ROOT / "config" / "exploration_arms.yaml")}


def _registered() -> dict[str, list[str]]:
    accounts = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    out: dict[str, list[str]] = {}
    for acc_id, acc in accounts.items():
        for arm_id in ((acc.get("exploration") or {}).get("arms") or {}):
            out.setdefault(arm_id, []).append(acc_id)
    return out


def _retired_replay(arm: Arm, role: str = "strategy") -> Arm:
    spec = arm.spec.model_copy(update={"role": role, "control_mode": "replay_only", "retired_day": "2026-11-13",
                                       "retired_rule": "test: retired for lack of evidence"})
    return arm.model_copy(update={"spec": spec})


def _retired(arm: Arm) -> Arm:
    spec = arm.spec.model_copy(update={"retired_day": "2026-11-13", "retired_rule": "test: retired"})
    return arm.model_copy(update={"spec": spec})


# ------------------------------------------------------------------------------------------------ the spec rows
def test_every_gold_arm_retired_for_lack_of_evidence_since_2026_11_13_records_replay_only_and_stays_retired():
    rows = _rows()
    for a in REVIVED:
        assert rows[a].control_mode == "replay_only" and rows[a].retired, a


def test_the_arms_left_retired_keep_recording_nothing():
    rows = _rows()
    for a in LEFT_RETIRED:
        assert rows[a].retired and rows[a].control_mode == "broker", a
    for a in ENDED:
        assert rows[a].retired and pd.Timestamp(rows[a].params["end_utc"]) <= pd.Timestamp("2026-11-22T22:00Z"), a


def test_the_red_box_rule_records_again_under_new_replay_only_ids_with_the_old_body_until_the_quarter_end():
    rows = _rows()
    for new, old in REDBOX_REPLAY.items():
        a, b = rows[new], rows[old]
        assert (a.rule, a.timeframe, a.max_open) == (b.rule, b.timeframe, b.max_open), new
        assert {k: v for k, v in a.params.items() if k not in ("end_utc", "control", "prereg")} == \
            {k: v for k, v in b.params.items() if k not in ("end_utc", "control", "prereg")}, new
        assert a.params["end_utc"] == "2026-12-31T22:00:00Z", new
        assert a.retired and a.control_mode == "replay_only", new


def test_replay_only_recording_adds_no_k_row():
    """A trial is a read; the new rows carry no learning block (no K id) and no qualification (never read for edge)."""
    rows = _rows()
    for new in REDBOX_REPLAY:
        assert rows[new].learning is None and rows[new].qualification is None, new
        assert rows[new].role in ("strategy", "control"), new


def test_every_recording_arm_is_registered_on_a_demo_account_and_its_rule_runs():
    rows, reg = _rows(), _registered()
    accounts = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    for a in (*REVIVED, *REDBOX_REPLAY):
        assert rows[a].rule in arm_rules.RULE_FUNCS, a
        assert reg.get(a) and all(accounts[acc]["mode"] == "demo" for acc in reg[a]), a


def test_replay_only_is_allowed_on_any_role_of_a_gold_arm():
    for role in ("strategy", "learning", "cost", "owner_experiment"):
        a = SpecArm(id="X", rule="random_bracket", timeframe="15m", role=role, control_mode="replay_only",
                    params={"minutes": [0], "window_utc": ["07:00", "19:45"], "stop_usd": 3.0, "target_r": 1.0,
                            "hold_bars": 4})
        assert a.control_mode == "replay_only"


# ------------------------------------------------------------------------------------------------ the engine
def test_a_retired_replay_only_arm_records_every_candidate_and_never_reaches_the_queue_gate_or_broker(
        tmp_path, monkeypatch):
    calls: list[str] = []
    intent, place, send = exploration._intent, cost_probe._place, exploration._send

    def spy_intent(*a: Any, **k: Any) -> Any:
        calls.append("intent:" + a[1].arm_id)
        return intent(*a, **k)

    def spy_place(*a: Any, **k: Any) -> Any:
        calls.append("place:" + k["journal"]["arm"])
        return place(*a, **k)

    def spy_send(eng: Any, ex: Any, ps: Any, arm: Arm, *a: Any, **k: Any) -> Any:
        calls.append("send:" + arm.arm_id)
        return send(eng, ex, ps, arm, *a, **k)

    monkeypatch.setattr(exploration, "_intent", spy_intent)
    monkeypatch.setattr(cost_probe, "_place", spy_place)
    monkeypatch.setattr(exploration, "_send", spy_send)
    eng, pb = _engine(tmp_path, [_retired_replay(_arm("R", max_open=10)), _arm("e", max_open=10)], None)
    check = eng.gate.check

    def spy_check(i: Any, *a: Any, **k: Any) -> Any:
        calls.append("gate:" + i.arm_id)
        return check(i, *a, **k)

    eng.gate.check = spy_check                                 # type: ignore[method-assign,assignment]
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, START + pd.Timedelta(minutes=15 * k)))
        assert not [s for s in eng.exploration.queue if s.arm_id == "R"]   # type: ignore[union-attr]
    assert not [c for c in calls if c.endswith(":R")], calls
    assert {d["arm"] for d in _sent(eng)} == {"e"}                       # the qualified arm still trades
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    r = obs[obs["arm_id"] == "R"]
    assert len(r) == 3 and not r["taken"].any()
    assert set(r["blocked_reason"]) == {"replay_only"} and set(r["mu"]) == {0.0}
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        assert not LEDGER.select(conn, "arm_id = ?", ("R",))
    assert [d["arm"] for d in _actions(eng, "exploration_arm_replay_only")] == ["R"]


def test_an_unqualified_learning_arm_in_replay_only_records_though_its_entries_are_refused(tmp_path):
    arm = _retired_replay(_arm("L", max_open=10), role="learning")
    eng, pb = _engine(tmp_path, [arm], None)
    exploration.on_pass(eng, _at(eng, pb, START))
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    assert list(obs[obs["arm_id"] == "L"]["blocked_reason"]) == ["replay_only"]
    assert not _sent(eng)


def test_a_retired_arm_without_replay_only_still_records_nothing(tmp_path):
    eng, pb = _engine(tmp_path, [_retired(_arm("Q", max_open=10))], None)
    exploration.on_pass(eng, _at(eng, pb, START))
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    assert obs[obs["arm_id"] == "Q"].empty and not _sent(eng)


def test_a_hash_refused_replay_only_arm_records_nothing(tmp_path):
    eng, pb = _engine(tmp_path, [_retired_replay(_arm("H", max_open=10))], None)
    ex = eng.exploration
    assert ex is not None
    ex.recording = ex.recording - {"H"}                    # as `Exploration` drops a hash-refused or invalid row
    exploration.on_pass(eng, _at(eng, pb, START))
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    assert obs[obs["arm_id"] == "H"].empty


def test_a_retired_replay_only_arm_is_never_deregistered_or_dormant():
    cfg = exploration.ExplorationConfig(
        enabled=True, pots={"intraday": exploration.Pot(gbp=48, max_open=5)},
        arms=(_retired_replay(_arm("R")), _retired(_arm("Q"))))
    assert exploration_deregister.candidates(cfg) == ["Q"]


def test_a_retired_replay_only_1m_arm_keeps_its_minute_work(tmp_path):
    eng, pb = _engine(tmp_path, [_retired_replay(_arm("M", tf="1m", style="scalp", max_open=10))], None)
    ex = eng.exploration
    assert ex is not None
    exploration.refresh_dormant(eng, ex, START)
    assert "M" not in ex.dormant


def test_replay_records_of_retired_arms_stop_at_the_daily_cap_and_say_so_once(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "REPLAY_RETIRED_DAILY_CAP", 2)
    eng, pb = _engine(tmp_path, [_retired_replay(_arm("R", max_open=10))], None)
    for k in range(4):
        exploration.on_pass(eng, _at(eng, pb, START + pd.Timedelta(minutes=15 * k)))
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    assert len(obs[obs["arm_id"] == "R"]) == 2
    assert len(_actions(eng, "exploration_replay_cap")) == 1

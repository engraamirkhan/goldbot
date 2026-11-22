"""Owner priority 2026-11-22: only arms with evidence place orders.

Weekly 15-20 Nov lost £412.63, 85 % of it from random controls that sent live orders, and no arm beat its random twin.
From the red-box week's end (2026-11-22 22:00 UTC) the frozen spec lets exactly the GD-1 learning arms (GD1D/GD1P, a
recorded history pass, prereg-amendment-2026-11-20-gd1-asia-learning.md) reach the broker. Every other arm is retired,
replay-only, or refused at load (an unqualified strategy). The pass check is the same rule read on the brain after the
deploy: zero orders from any other arm.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from goldbot.research import arms as A

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "config" / "exploration_arms.yaml"
EVIDENCE = frozenset({"GD1D", "GD1P"})
NO_EVIDENCE_1122 = frozenset({"GS1", "GS1C", "RB15", "RBC15", "RB1H", "RBC1H", "RB4H", "RBC4H"})


def _rows() -> list[dict]:
    return yaml.safe_load(SPEC.read_text())["arms"]


def _load(tmp_path: Path, at: str) -> dict[str, A.Arm]:
    rows = _rows()
    reg = tmp_path / "registry.jsonl"
    k_ids = [r["learning"]["k_id"] for r in rows if isinstance(r.get("learning"), dict)]
    reg.write_text("\n".join(json.dumps({"config_hash": k, "family": A.LEARNING_FAMILY}) for k in k_ids) + "\n")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(A, "_now", lambda: pd.Timestamp(at, tz="UTC"))
        return {a.id: a for a in A.load_arms(SPEC, registry=reg)}


def _can_send(a: A.Arm) -> bool:
    return a.status == "run" and not a.retired and a.control_mode == "broker" and not a.entry_refused


@pytest.mark.parametrize("at", ["2026-11-23 00:00", "2026-11-25 12:00", "2026-12-15 08:00"])
def test_only_the_evidence_backed_learning_arms_can_reach_the_broker(tmp_path, at) -> None:
    loaded = _load(tmp_path, at)
    assert {k for k, a in loaded.items() if _can_send(a)} == EVIDENCE


def test_a_sunday_deploy_loads_gd1_unrefused_so_the_monday_open_is_taken(tmp_path) -> None:
    """The engine never undoes a start refusal: GD-1 loaded 'not yet' on Sunday would miss its whole run."""
    loaded = _load(tmp_path, "2026-11-22 14:00")
    assert all(loaded[a].entry_refused == "" for a in EVIDENCE), {a: loaded[a].entry_refused for a in EVIDENCE}


def test_every_no_evidence_arm_that_traded_the_red_box_week_is_retired_on_2026_11_22() -> None:
    spec = {r["id"]: A.Arm.model_validate(r) for r in _rows()}
    for arm_id in NO_EVIDENCE_1122:
        assert spec[arm_id].retired_day == "2026-11-22", arm_id
        assert not A.A2_RULE.search(spec[arm_id].retired_rule), arm_id


def test_the_weekend_random_controls_are_replay_only() -> None:
    spec = {r["id"]: A.Arm.model_validate(r) for r in _rows()}
    assert spec["WKS0"].control_mode == "replay_only" and spec["WKI0"].control_mode == "replay_only"


def test_no_random_control_on_the_spec_sends_to_the_broker() -> None:
    spec = [A.Arm.model_validate(r) for r in _rows()]
    live_controls = [a.id for a in spec if a.role == "control" and a.status == "run" and not a.retired
                     and a.control_mode == "broker"]
    assert live_controls == []

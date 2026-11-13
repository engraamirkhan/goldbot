"""Backlog 117: a bad exploration arm row must not stop the engine (exits keep running).

On 2026-10-26 G1A/G1C rows used a `window_local` form the `Arm` validator refuses; `load_arms` raised inside
`exploration.config_for` at engine start and all three demo engines would have crash-looped, with time barriers, the
Friday cut and reconciliation stopped. Rule: an invalid ARM-SPEC row fails closed for that arm only (dropped, logged,
journalled `exploration_arm_dropped` with its reason: an engine incident of the daily review); the engine starts, the
other arms run and every position is still managed. The research loader stays strict (`load_arms` without
`rejected`), so CI still refuses the bad file."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import exploration
from goldbot.execution.broker import Tick
from goldbot.ops import accounts as acc_mod
from goldbot.research.arms import load_arms
from tests.test_exploration_engine import (
    CLOSE,
    TICKS,
    _arm,
    _at,
    _engine,
    _explore,
    _probe_account,
    _sent,
)
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

SPEC = Path(__file__).parents[1] / "config" / "exploration_arms.yaml"


def _account() -> acc_mod.Account:
    return _probe_account(exploration=_explore(
        arms={"S0": acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.25),
              "S0R": acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.25)},
        pots={"scalp": acc_mod.ExplorationPot(gbp=10, max_open=8)}))


def _broken(tmp_path: Path, how: str) -> Path:
    raw = yaml.safe_load(SPEC.read_text())
    row = next(r for r in raw["arms"] if r["id"] == "S0R")
    if how == "window_local":            # the 2026-10-26 form: a window_local without its `to` end
        row["params"] = {**row["params"], "window_local": {"from": ["Europe/London", "08:00"]}}
    elif how == "not_a_mapping":
        raw["arms"][raw["arms"].index(row)] = "S0R"
    elif how == "duplicate":
        raw["arms"].append(dict(row))
    elif how == "twin":
        row["twin_of"] = "NO-SUCH-ARM"
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


@pytest.mark.parametrize("how", ["window_local", "not_a_mapping", "duplicate", "twin"])
def test_the_research_loader_still_refuses_a_file_with_a_bad_row_so_ci_catches_it(tmp_path, how):
    with pytest.raises(ValueError):
        load_arms(_broken(tmp_path, how))


@pytest.mark.parametrize("how", ["window_local", "not_a_mapping", "duplicate", "twin"])
def test_a_bad_arm_spec_row_drops_that_arm_only_with_its_reason_and_the_other_arms_still_load(tmp_path, how):
    cfg = exploration.config_for(_account(), spec_path=_broken(tmp_path, how), spec_arms_path=tmp_path / "none.yaml")
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["S0"]
    assert "S0R" in cfg.dropped and cfg.invalid                          # named with why, journalled at the start
    assert cfg.invalid.get("S0R") or how == "not_a_mapping"             # a row with no id is named by its position


def test_the_rejected_list_names_each_bad_row_and_keeps_every_good_row(tmp_path):
    rejected: dict[str, str] = {}
    arms = load_arms(_broken(tmp_path, "window_local"), check_hash=False, rejected=rejected)
    assert set(rejected) == {"S0R"} and "window_local" in rejected["S0R"]
    assert len(arms) == len(load_arms(SPEC)) - 1 and "S0" in {a.id for a in arms}


def test_an_unreadable_arm_spec_file_drops_every_arm_and_the_engine_config_still_loads(tmp_path):
    path = tmp_path / "arms.yaml"
    path.write_text("arms: [ {id: \n")
    cfg = exploration.config_for(_account(), spec_path=path, spec_arms_path=tmp_path / "none.yaml")
    assert cfg is not None and cfg.arms == () and set(cfg.dropped) == {"S0", "S0R"}
    assert "unreadable" in cfg.invalid["S0"]


def test_the_engine_with_a_bad_arm_row_turns_reconciles_closes_that_arms_position_and_runs_the_other_arm(tmp_path):
    bad, good = _arm("s", tf="1m", style="scalp", hold_bars=5), _arm("g", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [bad])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)
    # the restart after a deploy whose spec file breaks arm s's row: config_for drops it with its reason
    eng2, _ = _engine(tmp_path, [good], pb=pb)
    assert eng2.exploration is not None
    cfg: Any = eng2.exploration.cfg.model_copy(update={"dropped": ("s",), "invalid": {"s": "window_local: bad"}})
    eng2.exploration = exploration.Exploration(eng2, cfg)
    rows = [d for d in eng2.decisions if d.get("action") == "exploration_arm_dropped"]
    assert [(d["arm"], d["why"]) for d in rows] == [("s", "window_local: bad")]
    eng2.open.clear()
    eng2._reconcile(tick.ts_utc)                                          # the loop turns: reconciliation runs
    assert pid in eng2.open and "s" not in eng2.exploration.arms          # adopted from its order row, arm gone
    nxt = minute + pd.Timedelta(minutes=1)
    eng2.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < nxt])
    eng2._last_tick = Tick(ts_utc=nxt + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask)
    exploration.on_pass(eng2, eng2._last_tick)
    assert {d["agent"] for d in _sent(eng2)} <= {"explore:g"}           # no candidate of the bad arm
    eng2._retry_closes(nxt)                                               # its position is never left unmanaged
    assert pid not in eng2.open
    assert pid not in {p.position_id for p in pb.positions()}            # closed at the broker

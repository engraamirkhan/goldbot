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
from goldbot.engine import arm_snapshot, exploration
from goldbot.execution.broker import Tick
from goldbot.ops import accounts as acc_mod
from goldbot.research.arms import arm_hash, load_arms
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
def test_without_a_snapshot_a_bad_row_drops_that_arm_only_with_its_reason_and_the_other_arms_still_load(tmp_path, how):
    """No last good row: the arm has never been in a good spec this engine knows, so it is dropped (named, with why)."""
    cfg = exploration.config_for(_account(), spec_path=_broken(tmp_path, how), spec_arms_path=tmp_path / "none.yaml")
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["S0"]
    assert "S0R" in cfg.dropped and cfg.invalid                          # named with why, journalled at the start
    assert cfg.invalid.get("S0R") or how == "not_a_mapping"             # a row with no id is named by its position


def test_the_rejected_list_names_each_bad_row_and_keeps_every_good_row(tmp_path):
    rejected: dict[str, str] = {}
    arms = load_arms(_broken(tmp_path, "window_local"), check_hash=False, rejected=rejected)
    assert set(rejected) == {"S0R"} and "window_local" in rejected["S0R"]
    assert len(arms) == len(load_arms(SPEC)) - 1 and "S0" in {a.id for a in arms}


def _clean_load(tmp_path: Path) -> Path:
    snap = arm_snapshot.path_for(tmp_path / "state")
    assert exploration.config_for(_account(), spec_path=SPEC, spec_arms_path=tmp_path / "none.yaml", snapshot=snap)
    return snap


def test_a_clean_load_remembers_every_row_and_a_later_load_never_forgets_one(tmp_path):
    snap = _clean_load(tmp_path)
    assert set(arm_snapshot.recall(snap) or {}) == {a.id for a in load_arms(SPEC)}
    exploration.config_for(_account(), spec_path=_broken(tmp_path, "not_a_mapping"),
                           spec_arms_path=tmp_path / "none.yaml", snapshot=snap)
    assert "S0R" in (arm_snapshot.recall(snap) or {})                   # merged by id, never removed


@pytest.mark.parametrize("how", ["window_local", "not_a_mapping", "duplicate", "twin"])
def test_with_a_snapshot_a_bad_row_keeps_its_arm_from_the_last_good_spec_with_entries_refused(tmp_path, how):
    snap = _clean_load(tmp_path)
    cfg = exploration.config_for(_account(), spec_path=_broken(tmp_path, how), spec_arms_path=tmp_path / "none.yaml",
                                 snapshot=snap)
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["S0", "S0R"] and "S0R" not in cfg.dropped
    refused = exploration.entry_refused(cfg)
    assert refused["S0R"].startswith(arm_snapshot.INVALID_RULE) and "S0" not in refused
    assert cfg.invalid["S0R"]                                            # journalled at the start with why


def test_an_unreadable_arm_spec_with_a_snapshot_keeps_every_arm_with_all_entries_refused(tmp_path):
    snap = _clean_load(tmp_path)
    path = tmp_path / "arms.yaml"
    path.write_text("arms: [ {id: \n")
    cfg = exploration.config_for(_account(), spec_path=path, spec_arms_path=tmp_path / "none.yaml", snapshot=snap)
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["S0", "S0R"] and cfg.dropped == ()
    assert set(exploration.entry_refused(cfg)) == {"S0", "S0R"} and "unreadable" in cfg.invalid["S0"]


@pytest.mark.parametrize("snapshot", [False, True])
def test_an_unreadable_arm_spec_without_a_snapshot_refuses_the_start_and_never_closes_everything(tmp_path, snapshot):
    path = tmp_path / "arms.yaml"
    path.write_text("arms: [ {id: \n")
    snap = arm_snapshot.path_for(tmp_path / "state") if snapshot else None    # a snapshot path with no file yet
    with pytest.raises(ValueError, match="refusing to start"):
        exploration.config_for(_account(), spec_path=path, spec_arms_path=tmp_path / "none.yaml", snapshot=snap)


def test_a_snapshot_row_a_newer_release_wrote_still_reads(tmp_path):
    snap = _clean_load(tmp_path)
    raw = yaml.safe_load(snap.read_text())
    raw["arms"] = [{**r, "field_from_the_future": 1} for r in raw["arms"]]
    snap.write_text(yaml.safe_dump(raw, sort_keys=False))
    assert set(arm_snapshot.recall(snap) or {}) == {a.id for a in load_arms(SPEC)}


# ------------------------------------------------------------------------------------------------ the engine
def _row(arm: exploration.Arm) -> dict[str, Any]:
    spec = arm.spec.model_copy(update={"config_hash": arm_hash(arm.spec)})
    return spec.model_dump(mode="json", exclude={"entry_refused"}, exclude_defaults=True)


def _write(path: Path, rows: list[Any]) -> Path:
    path.write_text(yaml.safe_dump({"arms": rows}, sort_keys=False))
    return path


@pytest.mark.parametrize("how", ["window_local", "duplicate", "unreadable"])
def test_after_a_bad_spec_restart_the_arms_open_position_is_adopted_kept_and_closed_only_by_its_own_hold(tmp_path, how):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    acc = _probe_account(exploration=_explore(arms={"s": acc_mod.ExplorationArm(max_loss=6.5, style="scalp")},
                                              pots={"scalp": acc_mod.ExplorationPot(gbp=10, max_open=8)}))
    snap, spec, none = arm_snapshot.path_for(tmp_path / "state"), tmp_path / "arms.yaml", tmp_path / "none.yaml"
    assert exploration.config_for(acc, spec_path=_write(spec, [_row(arm)]), spec_arms_path=none, snapshot=snap)
    eng, pb = _engine(tmp_path, [arm])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)
    # the deploy breaks the spec, then the engine restarts
    if how == "window_local":
        _write(spec, [{**_row(arm), "params": {**_row(arm)["params"], "window_local": {"from": ["Europe/London",
                                                                                                "08:00"]}}}])
    elif how == "duplicate":
        _write(spec, [_row(arm), _row(arm)])
    else:
        spec.write_text("arms: [ {id: \n")
    cfg = exploration.config_for(acc, spec_path=spec, spec_arms_path=none, snapshot=snap)
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["s"] and cfg.dropped == ()
    eng2, _ = _engine(tmp_path, list(cfg.arms), pb=pb, qualify=False)
    assert eng2.exploration is not None
    eng2.exploration = exploration.Exploration(eng2, eng2.exploration.cfg.model_copy(update={"invalid": cfg.invalid}))
    rows = [d for d in eng2.decisions if d.get("action") == "exploration_arm_invalid"]
    assert [d["arm"] for d in rows] == ["s"] and rows[0]["rule"].startswith(arm_snapshot.INVALID_RULE)
    assert not [d for d in eng2.decisions if d.get("action") == "exploration_arm_dropped"]
    eng2.open.clear()
    eng2._reconcile(tick.ts_utc)                                          # the loop turns: reconciliation runs
    assert eng2.open[pid].agent_id == "explore:s"                         # adopted under its arm, never an orphan
    nxt = minute + pd.Timedelta(minutes=1)
    eng2.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < nxt])
    eng2._last_tick = Tick(ts_utc=nxt + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask)
    exploration.on_pass(eng2, eng2._last_tick)
    assert not _sent(eng2)                                                # entries refused: no new order
    eng2._retry_closes(nxt)
    assert pid in eng2.open and pid in {p.position_id for p in pb.positions()}   # a config defect closes nothing
    opened = eng2.open[pid].opened_utc
    assert opened is not None
    eng2._retry_closes(opened + pd.Timedelta(seconds=300))                # its own hold still closes it
    assert pid not in eng2.open and not pb.positions()

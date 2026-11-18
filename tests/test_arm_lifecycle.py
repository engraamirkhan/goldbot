"""Build 2 (owner directive 2026-11-18): the automatic forward read and decision per demo arm,
goldbot/research/arm_lifecycle.py, with the rule of docs/research/arm-lifecycle-2026-11-18.md:
"RETIRE when the evidence says the arm is worse than its twin, or the existing kill rules fire; SIZE_UP one step ...
when the evidence says it is better; otherwise HOLD", by the replay-parity monitor's e-process on the forward excess
over the arm's matched replay twin, every decision journalled, a RETIRE applied through the engine's retirement.
Synthetic labels only; nothing here is market data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.research import arm_lifecycle as L
from goldbot.research.arms import Arm, load_arms

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-11-17"
NOW = pd.Timestamp("2026-11-18 06:25", tz="UTC")
SCOPE = "icm:15m-30m"
PASS = {SCOPE: L.ParityGap(scope=SCOPE, status="PASS", mean_gap_r=0.03)}


def _arm(arm_id: str = "A1", role: str = "strategy", **params: Any) -> Arm:
    return Arm(id=arm_id, rule="random_bracket", timeframe="15m", role=role,  # type: ignore[arg-type]
               params={"stop_usd": 3.0, "target_r": 1.0, "hold_bars": 4, **params})


def _obs(arm_r: float, ctrl_r: float = 0.0, *, days: int = 30, per_day: int = 3, seed: int = 1,
         gap: float = 0.0, arm_id: str = "A1", control: str = "C1", hours: tuple[int, ...] = (9, 14)) -> pd.DataFrame:
    """`days` session days ending on DAY: `per_day` arm fills a day (net R ~ N(arm_r, 1)) and twice as many control
    candidates (replay R ~ N(ctrl_r, 1)), in `hours` UTC cells."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range(end=DAY, periods=days, freq="D"):
        for i in range(per_day):
            h = hours[i % len(hours)]
            fr = float(rng.normal(arm_r, 1.0))
            rows.append({"arm_id": arm_id, "taken": True, "label_fill_r": fr, "label_sim_r": fr - gap,
                         "decision_utc": d.tz_localize("UTC") + pd.Timedelta(hours=h), "session_day": f"{d:%Y-%m-%d}"})
        for i in range(2 * per_day):
            h = hours[i % len(hours)]
            rows.append({"arm_id": control, "taken": False, "label_fill_r": np.nan,
                         "label_sim_r": float(rng.normal(ctrl_r, 1.0)),
                         "decision_utc": d.tz_localize("UTC") + pd.Timedelta(hours=h), "session_day": f"{d:%Y-%m-%d}"})
    return pd.DataFrame(rows)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database.open(str(tmp_path), "aux")


def _read(db: Database, obs: pd.DataFrame, arm: Arm | None = None, parity: dict[str, L.ParityGap] | None = None,
          control: str | None = "C1") -> L.ArmRead:
    return L.read_arm(db, obs, arm or _arm(), broker="icm", control=control, accounts={"icm-probe"},
                      parity=PASS if parity is None else parity, cap=5.0, day=DAY)


# ---------------------------------------------------------------- the twin and the figures
def test_the_twin_is_the_controls_replay_mean_in_the_same_hour_cell_up_to_the_fills_session_day():
    fills = pd.DataFrame({"decision_utc": pd.to_datetime(["2026-11-02 09:15", "2026-11-03 09:15", "2026-11-03 14:00"],
                                                         utc=True), "session_day": ["2026-11-02", "2026-11-03",
                                                                                    "2026-11-03"]})
    ctrl = pd.DataFrame({"decision_utc": pd.to_datetime(["2026-11-02 09:40", "2026-11-03 09:05", "2026-11-04 09:05"],
                                                        utc=True), "session_day": ["2026-11-02", "2026-11-03",
                                                                                   "2026-11-04"],
                         "label_sim_r": [1.0, 3.0, 100.0]})
    got = L.twin_means(fills, ctrl)
    assert got[0] == 1.0 and got[1] == 2.0           # point in time: the 11-04 label is never read
    assert np.isnan(got[2])                           # no control label at 14:00: unmatched


def test_the_read_reports_fills_mean_r_the_fill_replay_gap_and_raises_the_twin_by_the_parity_gap(db):
    r = _read(db, _obs(0.2, 0.0, gap=0.05))
    assert r.n_fills == 90 and r.n_days == 30 and r.validated
    assert r.gap_r == pytest.approx(0.05) and r.control_raise_r == pytest.approx(0.03)
    assert r.mean_r is not None and r.excess_r is not None
    assert r.excess_r == pytest.approx(r.mean_r - 0.03 - float(_obs(0.2).query("arm_id == 'C1'")["label_sim_r"]
                                                               .mean()), abs=0.15)


@pytest.mark.parametrize("parity,why", [
    ({}, "not read"),
    ({SCOPE: L.ParityGap(scope=SCOPE, status="INSUFFICIENT")}, "INSUFFICIENT"),
    ({SCOPE: L.ParityGap(scope=SCOPE, status="FIRE", mean_gap_r=0.1, revoked=True)}, "revoked"),
])
def test_an_unvalidated_twin_is_never_evidence_and_the_arm_holds(db, parity, why):
    r = L.decide(_read(db, _obs(-1.0), parity=parity), m=1)
    assert not r.validated and why in r.reason and r.decision == "HOLD"
    assert r.e_worse > L.E_RETIRE                     # the evidence is there, but the twin is not validated


def test_too_few_fills_or_days_hold(db):
    r = L.decide(_read(db, _obs(-1.0, days=8)), m=1)
    assert r.decision == "HOLD" and "insufficient" in r.reason


def test_a_read_with_most_fills_in_cells_without_a_control_label_is_not_readable(db):
    obs = _obs(-1.0)
    obs.loc[obs["arm_id"] == "A1", "decision_utc"] += pd.Timedelta(hours=3)      # arm at 12h/17h, control at 9h/14h
    r = L.decide(_read(db, obs), m=1)
    assert r.decision == "HOLD" and "not readable" in r.reason and r.unmatched_share == 1.0


# ---------------------------------------------------------------- the decisions
def test_an_arm_clearly_worse_than_its_twin_is_retired(db):
    r = L.decide(_read(db, _obs(-0.8, 0.0)), m=1)
    assert r.decision == "RETIRE" and r.e_worse >= L.E_RETIRE and "worse than its replay twin" in r.reason


def test_an_arm_no_different_from_its_twin_holds(db):
    r = L.decide(_read(db, _obs(0.0, 0.0, seed=3)), m=1)
    assert r.decision == "HOLD" and r.e_worse < L.E_RETIRE


def test_a_strategy_arm_clearly_better_than_its_twin_is_a_size_up_proposal_with_a_bonferroni_bar(db):
    read = _read(db, _obs(1.0, 0.0)).model_copy(update={"net_gbp": 12.0})
    up = L.decide(read, m=1)
    assert up.decision == "SIZE_UP" and not up.applied and "proposal only" in up.reason
    assert up.e_size_up == L.E_PER_STREAM * 2
    many = L.decide(read, m=10 ** 6)                   # the bar rises with the arms read in the run
    assert many.decision == "HOLD" and many.e_size_up == L.E_PER_STREAM * 2 * 10 ** 6


def test_a_learning_arm_is_never_sized_up_and_a_money_loser_never_is(db):
    learn = _read(db, _obs(1.0), arm=_arm(role="strategy")).model_copy(update={"role": "learning", "net_gbp": 9.0})
    assert L.decide(learn, m=1).decision == "HOLD"
    loser = _read(db, _obs(1.0)).model_copy(update={"net_gbp": -1.0})
    assert L.decide(loser, m=1).decision == "HOLD"


def test_a_kill_rule_retires_whatever_the_evidence(db):
    r = L.decide(_read(db, _obs(1.0)).model_copy(update={"kill": "learning_loss_cap 25 reached"}), m=1)
    assert r.decision == "RETIRE" and "kill rule" in r.reason


def test_no_matched_control_holds(db):
    r = L.decide(_read(db, _obs(-1.0), control=None), m=1)
    assert r.decision == "HOLD" and "no matched control" in r.reason


# ---------------------------------------------------------------- which arms, and what is applied
def test_controls_cost_arms_owner_experiments_and_the_red_box_week_arms_are_never_decided():
    arms = {a.id: a for a in load_arms(ROOT / "config" / "exploration_arms.yaml", check_hash=False)}
    for i in ("RB15", "RBC15", "RB1H", "RBC1H", "RB4H", "RBC4H"):
        assert not L.decided(arms[i]), i
    assert not any(L.decided(a) for a in arms.values() if a.role in ("control", "cost", "owner_experiment"))
    assert L.decided(_arm()) and not L.decided(_arm(end_utc="2026-11-22T22:00:00Z"))


def test_run_journals_every_decision_and_applies_a_retire_through_the_engines_retirement(db, monkeypatch):
    obs = pd.concat([_obs(-0.8, arm_id="BAD"), _obs(0.0, seed=4, arm_id="OK", control="C2")], ignore_index=True)
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: obs)
    rb = next(a for a in load_arms(ROOT / "config" / "exploration_arms.yaml", check_hash=False) if a.id == "RB15")
    reads = L.run(db, hosts={"icm-probe": "icm"}, arms=[_arm("BAD"), _arm("OK"), rb],
                  controls={"BAD": "C1", "OK": "C2"}, caps={}, day=DAY, now=NOW, parity=PASS)
    assert {r.arm_id: r.decision for r in reads} == {"BAD": "RETIRE", "OK": "HOLD"}
    assert {r.arm_id: (r.decision, r.applied) for r in L.reads_of(db, DAY)} == {"BAD": ("RETIRE", True),
                                                                                 "OK": ("HOLD", False)}
    with db.connection() as conn:
        rows = conn.execute("SELECT broker, arm_id, status, rule FROM exploration_arm_status").fetchall()
    assert [(b, a, s) for b, a, s, _ in rows] == [("icm", "BAD", "retired")] and "arm lifecycle" in rows[0][3]
    assert L.summary(reads)["retired"] == ["icm:BAD"]


def test_a_retired_decision_is_read_by_the_engine_which_takes_no_new_entry_of_the_arm(tmp_path, monkeypatch):
    from goldbot.engine import exploration
    from tests.test_exploration_engine import CLOSE, _at, _engine
    from tests.test_exploration_engine import _arm as engine_arm
    eng, pb = _engine(tmp_path, [engine_arm("BAD"), engine_arm("OK")])
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: _obs(-0.8, arm_id="BAD"))
    L.run(Database.open(str(tmp_path), "aux"), hosts={"icm-probe": "icm"}, arms=[_arm("BAD")],
          controls={"BAD": "C1"}, caps={}, day=DAY, now=NOW, parity=PASS)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert eng.exploration is not None and "BAD" in eng.exploration.retired and "OK" not in eng.exploration.retired
    assert not [t for t in eng.open.values() if t.agent_id == exploration.PREFIX + "BAD"]
    assert [t for t in eng.open.values() if t.agent_id == exploration.PREFIX + "OK"]


def test_the_parity_seam_reads_unvalidated_until_the_monitor_is_deployed(db, monkeypatch):
    import importlib
    real = importlib.import_module

    def missing(name: str, *a: Any) -> Any:
        if name == "goldbot.research.replay_parity":
            raise ModuleNotFoundError(name)
        return real(name, *a)
    monkeypatch.setattr(importlib, "import_module", missing)
    assert L.parity_gaps(db, ["icm"], before=NOW) == {}


def test_the_job_is_scheduled_after_the_daily_review():
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings()
    assert "arm_lifecycle" in JOBS and s.research.arm_lifecycle.enabled
    assert s.scheduler.arm_lifecycle.at > s.scheduler.daily_review.at


def test_a_read_a_newer_release_wrote_with_an_extra_field_still_reads(db):
    """ADR 0010: the journal is read tolerantly (a rollback reads a newer release's rows)."""
    body = L.ArmRead(day=DAY, broker="icm", arm_id="A1", role="strategy", tf="15m").model_dump()
    with db.write() as conn:
        conn.execute("INSERT INTO arm_lifecycle_reads (day, broker, arm_id, decision, recorded_ns, body) "
                     "VALUES (?, 'icm', 'A1', 'HOLD', 0, ?)", (DAY, json.dumps({**body, "new": 1})))
    [r] = L.reads_of(db, DAY)
    assert r.arm_id == "A1" and r.decision == "HOLD"

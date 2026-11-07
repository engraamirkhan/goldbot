"""N-3 HAR low-range abstention and the N-1a ICM routing guard (docs/research/prereg-2026-11-07-n3-har.md,
goldbot/engine/refusals_novel.py): learning arms and the controls read only against them are refused on a predicted
low-range day and, once the nightly job has written a row, on any day without one (fail closed); nothing is refused
before the job's first row or after its `off` row; gold learning entries run on ICM only; exits are untouched."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import entry_refusals, exploration, refusals_novel
from goldbot.engine.exploration import Arm, Pending
from goldbot.research import har_abstention as har
from goldbot.research.arms import Arm as SpecArm
from tests.test_entry_refusals import _obs, _spy_gate
from tests.test_exploration_engine import LONG, _actions, _arm, _at, _engine, _sent
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

ROOT = Path(__file__).parents[1]
TUE = pd.Timestamp("2025-03-04", tz="UTC")
MON_CLOSE = pd.Timestamp("2025-03-03 22:00", tz="UTC")      # the close that opens Tuesday's opportunity day


def _spec(arm_id: str, *, role: str = "learning", instrument: str | None = None,
          control: str | None = None, learning_control: str | None = None) -> Arm:
    params: dict[str, Any] = {"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4}
    if instrument is not None:
        params["instrument"] = instrument
    if control is not None:
        params["control"] = control
    learning = {"control": learning_control} if learning_control else None
    spec = SpecArm(id=arm_id, rule=LONG, timeframe="15m", max_open=5, params=params, role=role,  # type: ignore[arg-type]
                   learning=learning)
    return Arm(spec=spec, style="intraday", control_share=0.05 if role == "control" else None)


def _eng(tmp_path: Path, arms: list[Arm], broker: str = "icm") -> Any:
    ex = SimpleNamespace(db=Database.open(str(tmp_path), "aux"), arms={a.arm_id: a for a in arms})
    return SimpleNamespace(cfg=SimpleNamespace(broker_name=broker, weekend_window=None), exploration=ex)


def _sig(arm: Arm, ts: pd.Timestamp) -> Pending:
    return Pending(arm_id=arm.arm_id, side=1, bar_ts=ts, stop_usd=5.0, target_usd=10.0, hold_bars=4)


def _row(day: str, *, low: bool = False, status: str = "ok", available: pd.Timestamp | None = None) -> har.HarRow:
    d = pd.Timestamp(day)
    avail = available if available is not None else d.tz_localize("UTC") - pd.Timedelta(hours=1, minutes=40)
    return har.HarRow(for_day=d.date(), status=status, available_utc=avail,  # type: ignore[arg-type]
                      inputs_close_utc=d.tz_localize("UTC") - pd.Timedelta(hours=2),
                      hat=0.8 if low else 1.2, q20=1.0, low=low, n_trailing=250, fit_rows=600,
                      fit_to=pd.Timestamp("2025-10-01").date(), last_input_day=(d - pd.Timedelta(days=1)).date())


def _write(eng: Any, *rows: har.HarRow) -> None:
    for r in rows:
        har.write_row(eng.exploration.db, r)


LEARN = "Z3D"
ARMS = [_spec(LEARN, learning_control="LCZ3D"), _spec("LCZ3D", role="control", control="learning:Z3D"),
        _spec("ZCD", role="control"), _spec("Z9D"), _spec("S0", role="control"), _spec("S1A", role="strategy"),
        _spec("LCNEW", role="control", control="learning:Z9D")]


# ------------------------------------------------------------------------------------------------ scope
@pytest.mark.parametrize("arm_id, in_scope", [("Z3D", True), ("LCZ3D", True), ("ZCD", True), ("Z9D", True),
                                              ("S0", False), ("S1A", False), ("LCNEW", False)])
def test_scope_is_gold_learning_arms_and_the_preregistered_learning_controls(tmp_path, arm_id, in_scope):
    """A control is in scope only when the prereg names it (LC* twins, ZCD): a new learning twin needs an amendment."""
    eng = _eng(tmp_path, ARMS)
    assert refusals_novel.in_scope(eng, eng.exploration.arms[arm_id]) is in_scope


def test_retiring_every_strategy_arm_does_not_widen_the_scope_to_a_shared_control(tmp_path):
    """LOW-4: a shared control read against a strategy and a learning arm stays out of scope once the strategy arm is
    retired and only the learning arm is left in its pairs."""
    shared = _spec("SHC", role="control", control="learning:Z9D,S1A")
    eng = _eng(tmp_path, [*ARMS, shared])
    assert not refusals_novel.in_scope(eng, shared)
    retired = _eng(tmp_path, [*(a for a in ARMS if a.arm_id != "S1A"), shared])
    assert not refusals_novel.in_scope(retired, shared)


def test_a_raising_pairs_never_refuses_a_strategy_control(tmp_path, monkeypatch):
    from goldbot.engine import exploration_budget

    def broken(arms: Any) -> Any:
        raise RuntimeError("pairs broke")
    monkeypatch.setattr(exploration_budget, "pairs", broken)
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-04", low=True))
    arm = eng.exploration.arms["S0"]
    assert entry_refusals.first_refusal(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10))) is None
    assert refusals_novel.in_scope(eng, eng.exploration.arms["LCZ3D"])


def test_the_scope_controls_are_every_learning_twin_and_zcd_in_the_arm_config():
    """The pinned set matches the config: every gold LC* twin and every gold learning arm's control is named, none is
    missing from the config (an added twin must be named by a prereg amendment, else its arm is refused alone)."""
    import yaml
    every = yaml.safe_load((ROOT / "config" / "exploration_arms.yaml").read_text())["arms"]
    ids = {r["id"] for r in every}
    rows = [r for r in every if (r.get("params") or {}).get("instrument", "XAUUSD") == "XAUUSD"]     # gold only
    twins = {r["id"] for r in rows if str((r.get("params") or {}).get("control", "")).startswith("learning:")}
    learning_controls = {r["learning"]["control"] for r in rows if r.get("role") == "learning"
                         and isinstance(r.get("learning"), dict) and r["learning"].get("control")}
    assert twins | learning_controls | {"ZCD"} == set(refusals_novel.SCOPE_CONTROLS)
    assert set(refusals_novel.SCOPE_CONTROLS) <= ids


def test_a_learning_arm_on_another_instrument_is_out_of_scope(tmp_path):
    arm = _spec("LMI", instrument="EURUSD")
    assert not refusals_novel.in_scope(_eng(tmp_path, [arm]), arm)


# ------------------------------------------------------------------------------------------------ har rule
def test_nothing_is_refused_before_the_nightly_job_has_written_any_row(tmp_path):
    eng = _eng(tmp_path, ARMS)
    arm = eng.exploration.arms[LEARN]
    s = _sig(arm, TUE + pd.Timedelta(hours=10))
    assert not refusals_novel.har_unavailable(eng, arm, s) and not refusals_novel.har_low_range(eng, arm, s)


def test_a_low_day_refuses_a_learning_signal_and_its_control_as_har_low_range(tmp_path):
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-04", low=True))
    for arm_id in (LEARN, "LCZ3D"):
        arm = eng.exploration.arms[arm_id]
        s = _sig(arm, TUE + pd.Timedelta(hours=10))
        assert not refusals_novel.har_unavailable(eng, arm, s)
        assert refusals_novel.har_low_range(eng, arm, s)


def test_a_normal_day_refuses_nothing(tmp_path):
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-04", low=False))
    arm = eng.exploration.arms[LEARN]
    s = _sig(arm, TUE + pd.Timedelta(hours=10))
    assert not refusals_novel.har_unavailable(eng, arm, s) and not refusals_novel.har_low_range(eng, arm, s)


def test_a_low_day_never_touches_a_strategy_arm_or_a_shared_control(tmp_path):
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-04", low=True))
    for arm_id in ("S0", "S1A"):
        arm = eng.exploration.arms[arm_id]
        assert not refusals_novel.har_low_range(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10)))


def test_after_the_first_row_a_day_without_a_row_fails_closed_as_har_unavailable(tmp_path):
    """Trading-safety objection 1: a missing or stale HAR row refuses (the job missed a night)."""
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-03", low=False))
    arm = eng.exploration.arms[LEARN]
    assert refusals_novel.har_unavailable(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10)))


def test_in_the_first_30_minutes_after_the_cut_off_a_missing_row_refuses_without_a_warning(tmp_path, monkeypatch,
                                                                                            caplog):
    """LOW-3: the job runs at 22:05 and needs a few minutes; before 22:30 a missing row is expected, still refused."""
    monkeypatch.setattr(refusals_novel, "_warned", set())
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-04"))
    arm = eng.exploration.arms[LEARN]
    close = TUE + pd.Timedelta(hours=22)
    with caplog.at_level("WARNING", logger=refusals_novel.__name__):
        assert refusals_novel.har_unavailable(eng, arm, _sig(arm, close + pd.Timedelta(minutes=29)))
    assert not caplog.records
    with caplog.at_level("WARNING", logger=refusals_novel.__name__):
        assert refusals_novel.har_unavailable(eng, arm, _sig(arm, close + pd.Timedelta(minutes=30)))
    assert [r.levelname for r in caplog.records] == ["WARNING"]


def test_the_decision_time_is_the_rules_decision_close_when_the_signal_was_released_later(tmp_path):
    """Code review: a shifted XC15 keeps its decision close in `decision_utc` (entry_refusals._decision_utc): a
    Monday 21:45 decision released at 22:15 belongs to Monday's opportunity day, not Tuesday's."""
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-03", low=True, available=MON_CLOSE - pd.Timedelta(days=1)),
           _row("2025-03-04", available=MON_CLOSE - pd.Timedelta(days=1)))
    arm = eng.exploration.arms[LEARN]
    s = Pending(arm_id=arm.arm_id, side=1, bar_ts=MON_CLOSE + pd.Timedelta(minutes=15), stop_usd=5.0,
                target_usd=10.0, hold_bars=4, decision_utc=MON_CLOSE - pd.Timedelta(minutes=15))
    assert refusals_novel.har_low_range(eng, arm, s)


def test_warned_days_older_than_the_signal_day_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(refusals_novel, "_warned", {("", pd.Timestamp("2025-02-01").date()),
                                                    ("", pd.Timestamp("2025-03-03").date())})
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-03"))
    arm = eng.exploration.arms[LEARN]
    assert refusals_novel.har_unavailable(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10)))
    assert refusals_novel._warned == {("", TUE.date())}


def test_a_row_written_after_the_decision_time_is_not_read_point_in_time(tmp_path):
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-03"), _row("2025-03-04", low=True, available=TUE + pd.Timedelta(hours=11)))
    arm = eng.exploration.arms[LEARN]
    early = _sig(arm, TUE + pd.Timedelta(hours=10))
    assert refusals_novel.har_unavailable(eng, arm, early)
    late = _sig(arm, TUE + pd.Timedelta(hours=12))
    assert not refusals_novel.har_unavailable(eng, arm, late) and refusals_novel.har_low_range(eng, arm, late)


def test_the_signal_day_is_the_opportunity_day_rolling_at_2200_utc(tmp_path):
    """A 22:30 UTC Monday signal belongs to Tuesday's opportunity day."""
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-04", low=True, available=MON_CLOSE + pd.Timedelta(minutes=5)))
    arm = eng.exploration.arms[LEARN]
    assert refusals_novel.har_low_range(eng, arm, _sig(arm, MON_CLOSE + pd.Timedelta(minutes=30)))


def test_an_off_row_switches_the_rule_off_from_its_availability(tmp_path):
    eng = _eng(tmp_path, ARMS)
    _write(eng, _row("2025-03-03", low=True), _row("2025-03-04", status="off"))
    arm = eng.exploration.arms[LEARN]
    s = _sig(arm, TUE + pd.Timedelta(hours=10))
    assert not refusals_novel.har_unavailable(eng, arm, s) and not refusals_novel.har_low_range(eng, arm, s)


def test_an_unreadable_har_table_raises_so_the_hook_refuses_as_refusal_error(tmp_path):
    eng = _eng(tmp_path, ARMS)
    eng.exploration.db = None
    arm = eng.exploration.arms[LEARN]
    got = entry_refusals.first_refusal(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10)))
    assert got is not None and got.reason.startswith(entry_refusals.ERROR_PREFIX)


# ------------------------------------------------------------------------------------------------ N-1a routing
@pytest.mark.parametrize("broker, refused", [("icm", False), ("vantage", True), ("vantage247", True)])
def test_gold_learning_entries_run_on_icm_only(tmp_path, broker, refused):
    eng = _eng(tmp_path, ARMS, broker=broker)
    for arm_id in (LEARN, "LCZ3D"):
        arm = eng.exploration.arms[arm_id]
        assert refusals_novel.learning_icm_only(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10))) is refused


def test_the_icm_routing_guard_leaves_vantage_strategy_arms_and_controls_alone(tmp_path):
    eng = _eng(tmp_path, ARMS, broker="vantage")
    for arm_id in ("S0", "S1A"):
        arm = eng.exploration.arms[arm_id]
        assert not refusals_novel.learning_icm_only(eng, arm, _sig(arm, TUE + pd.Timedelta(hours=10)))


def test_no_active_gold_learning_arm_or_learning_control_is_registered_on_a_non_icm_account():
    """N-1a: the config already routes them to ICM (Z6V/Z7V/LCZ6V/LCZ7V/ZCV retired under A5 on 2026-11-06)."""
    import yaml
    rows = {r["id"]: r for r in yaml.safe_load((ROOT / "config" / "exploration_arms.yaml").read_text())["arms"]}
    accounts = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    bad = []
    for name, acc in accounts.items():
        if (acc or {}).get("broker") == "icm":
            continue
        for reg in ((acc or {}).get("exploration") or {}).get("arms") or []:
            r = rows.get(reg["id"] if isinstance(reg, dict) else reg)
            if r is None or r.get("retired_day") or r.get("status") != "run":
                continue
            gold = (r.get("params") or {}).get("instrument", "XAUUSD") == "XAUUSD"
            learning_ctl = str((r.get("params") or {}).get("control", "")).startswith("learning:")
            if gold and (r.get("role") == "learning" or learning_ctl or r["id"] in ("ZCD", "ZCV")):
                bad.append((name, r["id"]))
    assert bad == []


# ------------------------------------------------------------------------------------------------ registration
def test_the_shipped_refusals_add_the_icm_guard_then_har_unavailable_then_har_low_range():
    assert [r.name for r in entry_refusals.REFUSALS] == [
        entry_refusals.WINDOW_0207, entry_refusals.F1_PIT, refusals_novel.LEARNING_ICM_ONLY,
        refusals_novel.HAR_UNAVAILABLE, refusals_novel.HAR_LOW_RANGE]


def test_the_novel_refusals_module_never_reaches_an_order_or_a_close():
    tree = ast.parse((ROOT / "goldbot" / "engine" / "refusals_novel.py").read_text())
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"_execute", "_place", "order_send", "_try_close", "check", "send", "close"}


# ------------------------------------------------------------------------------------------------ in the engine
def test_in_the_engine_a_low_day_refuses_the_signal_shadows_it_and_the_riskgate_never_sees_it(tmp_path, monkeypatch):
    monkeypatch.setattr(refusals_novel, "in_scope", lambda eng, arm: arm.arm_id == "a")
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b")])
    _write(SimpleNamespace(exploration=SimpleNamespace(db=Database.open(str(tmp_path), "aux"))),
           _row("2025-03-04", low=True))
    seen = _spy_gate(eng)
    exploration.on_pass(eng, _at(eng, pb, TUE + pd.Timedelta(hours=12)))
    assert {d["agent"] for d in _sent(eng)} == {"explore:b"}, eng.decisions
    assert [d["agent"] for d in _actions(eng, "exploration_refused:har_low_range")] == ["explore:a"]
    assert seen and all(a[0].agent_id != "explore:a" for a in seen)
    rows = [r for r in _obs(tmp_path) if r["arm_id"] == "a"]
    assert rows and all(r["taken"] == 0 and r["blocked_reason"] == refusals_novel.HAR_LOW_RANGE for r in rows)

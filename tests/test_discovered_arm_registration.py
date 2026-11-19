"""Discovered arms on demo, the learning-class route (docs/research/prereg-amendment-2026-11-18-discovered-arms.md).

`autodisc-register --to-db` registers an explore survivor as a hot-loaded learning arm with its replay-only control:
"Under the registry lock, the arm's K row, the control's `NOT_TRIAL` row and the aux.db `arm_registry` row are all
written, or none of them" (section 2.2); refused over `research.trial_budget_quarter` (`used + reserved + 1`), for a
6th concurrent discovered arm (2.3) and when the kill rule is unreadable by `until` (2.4 (f)). The engine loads the
pair only when the arm's K row resolves, the control matches, and the control is the one frozen at registration
("Any change to the control's parameters after registration voids the arm").

Every file is a temp copy: config/accounts.yaml (no keyring read), an empty spec arms file, a real-mode ledger and a
trial registry built here. No real K row is written: every registry path is under tmp_path."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import arm_registry as AR
from goldbot.engine import exploration
from goldbot.ops import autodisc_register as R
from goldbot.research import arm_rules_spec as S
from goldbot.research import arms as ARMS
from goldbot.research import registry as RG
from goldbot.research.autodisc import handoff
from goldbot.research.autodisc.gen_combine import family_spec
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.spec import ExitTemplate, Leaf, canonical_json, spec_hash
from tests.test_exploration_engine import CLOSE, _arm, _at, _engine
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-11-23 12:00", tz="UTC")           # after the amendment's effective time (2026-11-22 22:00)
T2R = ExitTemplate(stop="atr_1.0", target="2R")
FILTERS = [None, *(Leaf(kind="time", name=d) for d in ("mon", "tue", "wed", "thu", "fri"))]
# an explore survivor that passes section 2.4: gross >= 2 x 0.90 $/oz, net > 0, ~2 signals a session day
GOOD = {"n_events": 4000, "mean_r": 0.08, "fitness": 2.4, "excess_r": 0.05, "stress_mean_r": 0.03, "floors": True,
        "n_bars": 46000, "n_days": 2000, "n_signal_days": 1500, "gross_usd_mean": 2.10, "net_usd_mean": 0.60}


def spec(i: int = 0) -> Any:
    return family_spec("box_break_close", "1h", FILTERS[i], T2R)


class Research(SimpleNamespace):
    """The two settings the budget check reads (`research.budget_for`, `research.reserved_for`)."""

    def budget_for(self, quarter: str) -> int:
        return int(self.budget)

    def reserved_for(self, quarter: str) -> int:
        return int(self.reserved)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    shutil.copy(ROOT / "config" / "accounts.yaml", tmp_path / "accounts.yaml")
    shutil.copy(ROOT / "config" / "spec_arms.yaml", tmp_path / "spec_arms.yaml")
    subprocess.run(["git", "init", "-q", "-b", "feature/autodisc-arm", str(tmp_path)], check=True)
    ledger = SearchLedger(tmp_path / "discovery.db", mode="real")
    for i in range(len(FILTERS)):
        ledger.log_spec(spec(i), "G1", "1")
        ledger.record_result(spec_hash(spec(i)), GOOD)
        ledger.log_spec(spec(i), "G1", "1", null=True)
        ledger.record_result(spec_hash(spec(i)), {"null_fitness": [0.5, 1.1, None, 0.9]}, null=True)
    registry = tmp_path / "research_registry.jsonl"
    monkeypatch.setattr(ARMS, "REGISTRY_PATH", registry)        # the engine's and learning_refusal's registry
    monkeypatch.setattr(ARMS, "_now", lambda: NOW)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(R, "LIFECYCLE_READY", True)             # the interlock, lifted for the route's own tests
    return {"ledger_path": tmp_path / "discovery.db", "accounts_path": tmp_path / "accounts.yaml",
            "spec_arms_path": tmp_path / "spec_arms.yaml", "root": ROOT, "now": NOW, "state_dir": state,
            "registry_path": registry, "research": Research(budget=100, reserved=0),
            "max_loss": 5.0}                    # inside icm-demo's caps and the engine harness's (ceiling 14.4)


def register(env: dict[str, Any], i: int = 0, account: str = "icm-demo", **kw: Any) -> R.LearningPlan:
    return R.register_learning_to_db(spec_hash(spec(i)), account, **{**env, **kw})


def registry_rows(env: dict[str, Any]) -> list[dict[str, Any]]:
    p = env["registry_path"]
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def arm_rows(env: dict[str, Any]) -> list[AR.RegistryRow]:
    return AR.read_rows(Database.open(str(env["state_dir"]), "aux"))


# ---------------------------------------------------------------- one transaction
def test_a_registration_writes_the_k_row_the_not_trial_control_row_and_both_arm_rows_together(env):
    p = register(env)
    rows = registry_rows(env)
    k = [r for r in rows if r["config_hash"] == p.k_id]
    nt = [r for r in rows if r["status"] == RG.NOT_TRIAL]
    assert len(k) == 1 and RG.is_trial(k[0]) and k[0]["family"] == ARMS.LEARNING_FAMILY
    assert len(nt) == 1 and not RG.is_trial(nt[0]) and nt[0]["trial"] is None
    assert nt[0]["config"]["arm"] == p.reg.arm_id and nt[0]["config"]["k_id"] == p.k_id
    assert nt[0]["config"]["control_hash"] == p.control_hash
    assert RG.n_trials_effective(rows) == 1                     # +1 K a registration: the control adds none
    got = arm_rows(env)
    assert [r.arm_id for r in got] == [p.reg.arm_id, p.reg.arm_id + "C"]
    arm, ctl = (json.loads(r.row) for r in got)
    block = arm["learning"]
    assert arm["role"] == "learning" and ctl["role"] == "control" and ctl["rule"] == S.SPEC_CONTROL
    assert block == {"prereg": S.DISCOVERED_PREREG, "k_id": p.k_id, "control": p.reg.arm_id + "C",
                     "loss_cap_gbp": 60.0, "since": "2026-11-23", "until": "2027-01-04"}
    assert arm["params"]["per_trade_bracket"] == ctl["params"]["per_trade_bracket"] == "spec_exit:" + p.reg.spec_hash[:12]
    assert arm["params"]["end_utc"] == "2027-01-04T22:00:00Z"
    assert ctl["params"]["control"] == "learning:" + p.reg.arm_id
    assert R.journal(env["ledger_path"])[0].arm_id == p.reg.arm_id


def test_until_is_capped_at_the_sunset():
    since = pd.Timestamp("2027-03-01", tz="UTC")
    assert R.learning_until(since) == "2027-03-31"
    assert R.learning_until(pd.Timestamp("2026-11-23", tz="UTC")) == "2027-01-04"


def test_registration_without_a_k_row_is_refused(env, monkeypatch):
    """T1: the K write failing writes no arm_registry row; a k_id that does not resolve is refused by
    `learning_refusal` and the engine gives the arm no entry."""
    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(RG.TrialRegistry, "record", boom)
    with pytest.raises((R.RegistrationRefused, OSError)):
        register(env)
    assert arm_rows(env) == [] and registry_rows(env) == [] and R.journal(env["ledger_path"]) == []


def test_an_arm_whose_k_id_does_not_resolve_is_refused_by_learning_refusal(env):
    p = R.plan_learning(spec_hash(spec(0)), "icm-demo", **{k: v for k, v in env.items() if k != "state_dir"},
                        state_dir=env["state_dir"])
    arm = ARMS.Arm.model_validate(p.arm_row)
    why = ARMS.learning_refusal(arm, ROOT, env["registry_path"], NOW)
    assert why is not None and "does not resolve" in why


def test_the_arm_row_failing_leaves_a_void_marker_and_the_k_row_counted(env, monkeypatch):
    def boom(*a: Any, **k: Any) -> None:
        raise ValueError("aux.db locked")
    monkeypatch.setattr(AR, "append_many", boom)
    with pytest.raises(R.RegistrationRefused):
        register(env)
    rows = registry_rows(env)
    assert arm_rows(env) == []
    assert [r["status"] for r in rows] == ["learning", RG.NOT_TRIAL, RG.LEARNING_VOID]
    assert RG.n_trials_effective(rows) == 1                     # the K stays counted, never deleted
    with pytest.raises(R.RegistrationRefused):                  # and the spec is never registered again
        register(env)


# ---------------------------------------------------------------- refusals
def test_registration_over_the_quarter_budget_is_refused(env):
    """T2: used + reserved + 1 > the quarter's budget: nothing written, the refusal carries the quarter's numbers."""
    reg = RG.TrialRegistry(env["registry_path"])
    for i in range(3):
        reg.record(agent_id="t", family="f", config={"i": i}, feature_version="v", rationale="r", results={})
    with pytest.raises(R.RegistrationRefused) as exc:
        register(env, research=Research(budget=5, reserved=2))
    msg = str(exc.value)
    assert "2026Q4" in msg and "3 used" in msg and "2 reserved" in msg and "budget 5" in msg
    assert arm_rows(env) == [] and len(registry_rows(env)) == 3 and R.journal(env["ledger_path"]) == []
    register(env, research=Research(budget=6, reserved=2))       # exactly at the budget: written


def test_a_sixth_concurrent_discovered_arm_is_refused(env):
    for i in range(5):
        register(env, i, now=NOW + pd.Timedelta(days=7 * i))      # the weekly tier: one a week here
    with pytest.raises(R.RegistrationRefused, match="5 discovered arms"):
        register(env, 5, now=NOW + pd.Timedelta(days=35))
    assert len(arm_rows(env)) == 10


def test_an_arm_whose_kill_rule_is_unreadable_by_until_is_refused(env, tmp_path):
    """Section 2.4 (f): explore rate x the run's session days < 50 candidates, or < 10 signal days: refused."""
    for name, over in (("rare", {"n_events": 400}), ("clustered", {"n_signal_days": 200})):
        p = tmp_path / f"{name}.db"
        led = SearchLedger(p, mode="real")
        led.log_spec(spec(0), "G1", "1")
        led.record_result(spec_hash(spec(0)), {**GOOD, **over})
        led.log_spec(spec(0), "G1", "1", null=True)
        led.record_result(spec_hash(spec(0)), {"null_fitness": [0.5, 1.1, None, 0.9]}, null=True)
        with pytest.raises(R.RegistrationRefused, match="kill rule"):
            register(env, ledger_path=p)
    assert arm_rows(env) == [] and registry_rows(env) == []


@pytest.mark.parametrize("over,why", [({"gross_usd_mean": 1.79}, "gross"), ({"net_usd_mean": 0.0}, "net"),
                                      ({"gross_usd_mean": None}, "explore record")])
def test_a_survivor_below_the_cost_aware_bar_is_refused(env, tmp_path, over, why):
    p = tmp_path / "bar.db"
    led = SearchLedger(p, mode="real")
    led.log_spec(spec(0), "G1", "1")
    led.record_result(spec_hash(spec(0)), {k: v for k, v in {**GOOD, **over}.items() if v is not None})
    led.log_spec(spec(0), "G1", "1", null=True)
    led.record_result(spec_hash(spec(0)), {"null_fitness": [0.5, 1.1, None, 0.9]}, null=True)
    with pytest.raises(R.RegistrationRefused, match=why):
        register(env, ledger_path=p)


def test_the_command_registers_a_learning_arm_only_with_to_db_and_exits_2_on_a_refusal(env, capsys):
    with pytest.raises(SystemExit):
        R.main([spec_hash(spec(0)), "--account", "icm-demo", "--learning"])            # no --to-db
    rc = R.main(["f" * 64, "--account", "icm-demo", "--learning", "--to-db", str(env["state_dir"]),
                 "--ledger", str(env["ledger_path"]), "--registry", str(env["registry_path"])])
    assert rc == 2 and "refused" in capsys.readouterr().err       # not in the ledger (or before the effective time)
    assert arm_rows(env) == [] and registry_rows(env) == []


@pytest.mark.parametrize("when", ["2026-11-22 21:59", "2027-04-01 00:00"])
def test_no_registration_before_the_effective_time_or_after_the_sunset(env, when):
    with pytest.raises(R.RegistrationRefused):
        register(env, now=pd.Timestamp(when, tz="UTC"))
    assert arm_rows(env) == []


# ---------------------------------------------------------------- the engine
def _ex(eng: Any) -> exploration.Exploration:
    assert eng.exploration is not None
    return eng.exploration


def _engine_with(env: dict[str, Any], tmp_path: Path) -> tuple[Any, Any, R.LearningPlan]:
    """A registration on icm-demo copied to the engine harness's account (icm-probe) in its state dir."""
    p = register(env)
    eng, pb = _engine(tmp_path / "eng", [_arm("peer", tf="1h")])
    rows = [(json.loads(r.row), r.style, r.max_loss) for r in arm_rows(env)]
    AR.append_many(str(tmp_path / "eng"), account_id="icm-probe", entries=rows, source="test")
    return eng, pb, p


def test_the_engine_loads_the_learning_arm_and_its_replay_only_control_which_never_sends(env, tmp_path):
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    ex = _ex(eng)
    ctl = p.reg.arm_id + "C"
    assert p.reg.arm_id in ex.arms and ctl in ex.arms, [d.get("why") for d in eng.decisions
                                                         if "registry" in d["action"]]
    assert ex.arms[ctl].control_share is not None
    assert p.reg.arm_id in ex.learning_group and ctl in ex.learning_group
    assert AR.entry_refusal(eng, ex, ctl) == S.REPLAY_ONLY
    assert p.reg.arm_id not in ex.retired, ex.unqualified.get(p.reg.arm_id)


def test_the_engine_gives_no_entry_to_an_arm_whose_k_row_does_not_resolve(env, tmp_path):
    eng, pb, p = _engine_with(env, tmp_path)
    kept = [r for r in registry_rows(env) if r["config_hash"] != p.k_id]      # the K row gone from this host's copy
    assert len(kept) == 1 and kept[0]["status"] == RG.NOT_TRIAL
    env["registry_path"].write_text("".join(json.dumps(r) + "\n" for r in kept))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    ex = _ex(eng)
    assert p.reg.arm_id in ex.retired and "does not resolve" in ex.unqualified[p.reg.arm_id]


def test_the_engine_refuses_a_learning_row_whose_since_is_before_the_amendments_effective_day(env, tmp_path):
    """Section 1: "Until then no ... enters". Registration refuses before EFFECTIVE_UTC, but a learning row
    appended straight to aux.db never went through it: the hot loader refuses a `since` before EFFECTIVE_UTC's UTC
    day (the earliest a registration can write), so with `check_learning`'s "not yet" no such row enters before it.
    The constant is the one registration reads (one source)."""
    assert R.EFFECTIVE_UTC is S.EFFECTIVE_UTC
    p = register(env)
    eng, pb = _engine(tmp_path / "eng", [_arm("peer", tf="1h")])
    early = f"{S.EFFECTIVE_UTC - pd.Timedelta(days=1):%Y-%m-%d}"
    until = S.learning_until(pd.Timestamp(early, tz="UTC"))
    entries = []
    for r in arm_rows(env):
        row = json.loads(r.row)
        if row["role"] == "learning":
            row["learning"] = {**row["learning"], "since": early, "until": until}
            row["params"] = {**row["params"], "end_utc": f"{until}T22:00:00Z"}
        entries.append((row, r.style, r.max_loss))
    AR.append_many(str(tmp_path / "eng"), account_id="icm-probe", entries=entries, source="test")
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    why = [str(d.get("why")) for d in eng.decisions if d.get("arm") == p.reg.arm_id]
    assert p.reg.arm_id not in _ex(eng).arms or p.reg.arm_id in _ex(eng).retired
    assert any("effective" in w for w in why), why


def test_a_bracket_that_does_not_match_its_recompute_at_the_entry_is_void_and_holds_the_arm(env, tmp_path):
    """T4 at the order path: the signal's stop, target and hold (what its candidate INSERT records) are recomputed
    from the bars closed at the decision; off by more than 1e-9, or sent more than 60 s after the decision: void, not
    sent, the arm's entries held, journalled."""
    eng, pb, p = _engine_with(env, tmp_path)
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    ex = _ex(eng)
    arm = ex.arms[p.reg.arm_id]
    got = exploration.arm_bars(eng.bars_1m[eng.bars_1m["visible_at"] <= CLOSE], arm.spec, CLOSE)
    assert got is not None
    b = S.bracket(S.spec_of(arm.spec), got[0], CLOSE)
    assert b is not None
    hold = b.max_hold_s // 3600
    s = exploration.Pending(arm_id=arm.arm_id, side=1, bar_ts=CLOSE, stop_usd=b.stop_dist, target_usd=b.target_dist,
                            hold_bars=hold)
    now = CLOSE + pd.Timedelta(seconds=2)
    assert AR.bracket_refusal(eng, arm, s, now) is None
    assert "bracket_late" in str(AR.bracket_refusal(eng, arm, s, CLOSE + pd.Timedelta(seconds=61)))
    off = s.model_copy(update={"stop_usd": b.stop_dist + 1e-6})
    assert "bracket_mismatch" in str(AR.bracket_refusal(eng, arm, off, now))
    assert AR.entry_refusal(eng, ex, arm.arm_id, off, now) == "bracket_void"
    assert arm.arm_id in ex.retired
    assert [d["arm"] for d in eng.decisions if d.get("action") == AR.BRACKET_VOID] == [arm.arm_id]


def test_a_change_to_the_control_after_registration_voids_the_arm(env, tmp_path):
    """T9: the control is recorded NOT_TRIAL, linked to the arm, and adds no K. A control whose frozen parameters
    differ from the ones its NOT_TRIAL row recorded refuses the arm's entries and retires it; its K row stays."""
    p = register(env)
    nt = [r for r in registry_rows(env) if r["status"] == RG.NOT_TRIAL]
    assert len(nt) == 1 and nt[0]["config"]["arm"] == p.reg.arm_id and nt[0]["config"]["k_id"] == p.k_id
    assert RG.n_trials_effective(registry_rows(env)) == 1
    arm = ARMS.Arm.model_validate(p.arm_row)
    ctl = ARMS.Arm.model_validate(p.control_row)
    assert ARMS.control_freeze_refusal(arm, ctl) is None
    changed = ctl.model_copy(update={"params": {**ctl.params, "signal_rate": ctl.params["signal_rate"] * 2}})
    changed = changed.model_copy(update={"config_hash": ARMS.arm_hash(changed)})
    why = ARMS.control_freeze_refusal(arm, changed)
    assert why is not None and "changed after registration" in why

    # the engine: the same arm row with a changed control row (a fresh aux.db) is held, journalled, K kept
    eng, pb = _engine(tmp_path / "eng", [_arm("peer", tf="1h")])
    [arm_row, ctl_row] = [json.loads(r.row) for r in arm_rows(env)]
    style, cap = arm_rows(env)[0].style, arm_rows(env)[0].max_loss
    ctl_row = {**ctl_row, "params": changed.params, "config_hash": changed.config_hash}
    AR.append_many(str(tmp_path / "eng"), account_id="icm-probe", entries=[(arm_row, style, cap),
                                                                         (ctl_row, style, cap)], source="test")
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    ex = _ex(eng)
    assert p.reg.arm_id not in ex.arms or p.reg.arm_id in ex.retired
    void = [d for d in eng.decisions if d.get("action") == AR.CONTROL_CHANGED]
    assert void and void[0]["arm"] == p.reg.arm_id
    assert any(r["config_hash"] == p.k_id for r in registry_rows(env))       # the K row stays counted


def test_the_engine_refuses_a_sixth_concurrent_discovered_arm(env, tmp_path):
    """Section 2.3 again at the load: the sixth discovered learning arm (by registry order) is refused."""
    for i in range(5):
        register(env, i, now=NOW + pd.Timedelta(days=7 * i))
    other = tmp_path / "other"
    other.mkdir()
    sixth = register(env, 5, now=NOW + pd.Timedelta(days=35), state_dir=other)     # another host's aux.db
    eng, pb = _engine(tmp_path / "eng", [_arm("peer", tf="1h")])
    rows = [(json.loads(r.row), r.style, r.max_loss)
            for r in [*arm_rows(env), *AR.read_rows(Database.open(str(other), "aux"))]]
    AR.append_many(str(tmp_path / "eng"), account_id="icm-probe", entries=rows, source="test")
    monkey = pd.Timestamp(NOW + pd.Timedelta(days=36))
    ARMS._now = lambda: monkey                                                        # restored by the env fixture
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    ex = _ex(eng)
    learning = [a for a, arm in ex.arms.items() if arm.spec.role == "learning" and a not in ex.retired]
    assert len(learning) == AR.MAX_DISCOVERED and sixth.reg.arm_id not in learning
    assert any(d.get("arm") == sixth.reg.arm_id and "concurrent" in str(d.get("why")) for d in eng.decisions)


# ---------------------------------------------------------------- the interlock (quant review of 751cf7ab)
def test_lifecycle_ready_is_false_on_this_branch():
    """Flipped only in the PR that delivers every piece: the lifecycle voids, forward power and T5 (section 6)."""
    assert R.LIFECYCLE_READY is False


def test_every_registration_is_refused_until_the_lifecycle_is_ready(env, monkeypatch):
    monkeypatch.setattr(R, "LIFECYCLE_READY", False)
    with pytest.raises(R.RegistrationRefused, match="amendment not effective: lifecycle voids, forward power, T5"):
        register(env)
    assert arm_rows(env) == [] and registry_rows(env) == []


def offer(env: dict[str, Any], i: int, power_forward: dict[str, Any]) -> None:
    """The discovery lane's hand-off row for spec(i), as `discovery_nightly` writes it (ADR 0090)."""
    s = spec(i)
    handoff.write_candidates(env["ledger_path"], [handoff.CandidateArm(
        spec_hash=spec_hash(s), night="2026-11-22", timeframe=s.timeframe, style="intraday",
        spec=canonical_json(s), dsr=None, meets_confirm_dsr=False, k_search=1, power={},
        power_forward=power_forward)])


def test_the_plan_reports_the_offered_candidates_forward_power_and_none_when_never_offered(env):
    """2.4 (f): the forward power against the twin is the hand-off row's `power_forward`, "reported, not evidence"."""
    verdict = {"admit": False, "n_eff": 12.5, "floor": 30}
    offer(env, 0, verdict)
    args = {k: v for k, v in env.items() if k != "state_dir"}
    assert R.plan_learning(spec_hash(spec(0)), "icm-demo", state_dir=env["state_dir"], **args).power_forward == verdict
    assert R.plan_learning(spec_hash(spec(1)), "icm-demo", state_dir=env["state_dir"], **args).power_forward is None


def test_the_forward_power_never_refuses_or_admits_a_registration(env):
    """Reported, not evidence: an underpowered forward verdict still registers; the plan is the same apart from it."""
    offer(env, 0, {"admit": False, "n_eff": 0.0})
    offer(env, 1, {"admit": True, "n_eff": 99.0})
    a, b = register(env, 0), register(env, 1)
    assert (a.power_forward or {}).get("admit") is False and (b.power_forward or {}).get("admit") is True
    assert [r.arm_id for r in arm_rows(env)] == [a.reg.arm_id, a.reg.arm_id + "C", b.reg.arm_id, b.reg.arm_id + "C"]


# The amendment's section 6 tests T1-T9, under their pre-registered names unless mapped:
# T1 test_registration_without_a_k_row_is_refused: here.
# T2 test_registration_over_the_quarter_budget_is_refused: here (with test_a_sixth_concurrent_discovered_arm_is_refused
#    and test_an_arm_whose_kill_rule_is_unreadable_by_until_is_refused).
# T3 test_booked_k_equals_the_screened_configurations, MAPPED to tests/research/autodisc/test_registry_link.py
#    test_sync_tops_the_registry_up_to_the_ledger_k_search_once (summed k_contrib = the ledger's distinct K_search,
#    re-synced once) and test_the_nightly_job_records_its_batch_k_contrib_only_for_a_real_search (n_trials_discovery
#    rises by exactly k_contrib); n_refused_power: test_power_refusals_are_counted_and_reported_never_booked_as_k in
#    tests/research/autodisc/test_discovery_nightly.py.
# T4 test_a_bracket_written_after_decision_utc_is_refused: tests/test_spec_bracket.py; at the order path
#    test_a_bracket_that_does_not_match_its_recompute_at_the_entry_is_void_and_holds_the_arm here; at night
#    tests/test_arm_lifecycle_inputs.py (the recompute from the engine's stored minutes).
# T5 test_power_admission_is_outcome_blind: tests/research/autodisc/test_discovery_nightly.py.
# T6 test_bracket_is_unchanged_by_bars_after_decision: tests/test_spec_bracket.py.
# T7 test_phase_gate_reads_discovery_k_for_discovered_specs: tests/test_phase_gate_pin.py.
# T8 test_prior_search_seed_is_outside_quarter_k_but_inside_n_trials_discovery:
#    tests/research/autodisc/test_discovery_nightly.py.
# T9 test_a_change_to_the_control_after_registration_voids_the_arm: here.


# ---------------------------------------------------------------- trading-safety notes on 751cf7ab
def test_the_staleness_limit_of_a_learning_timeframe_is_the_brackets_latency_limit():
    """Raising one without the other fails: a signal the engine may still send must be one whose bracket stands."""
    for tf in S.LEARNING_TFS:
        assert exploration.stale_s(tf) == S.LATENCY_S, tf


def _void_at_the_entry(eng: Any, pb: Any, arm_id: str) -> None:
    ex = _ex(eng)
    arm = ex.arms[arm_id]
    got = exploration.arm_bars(eng.bars_1m[eng.bars_1m["visible_at"] <= CLOSE], arm.spec, CLOSE)
    assert got is not None
    b = S.bracket(S.spec_of(arm.spec), got[0], CLOSE)
    assert b is not None
    s = exploration.Pending(arm_id=arm_id, side=1, bar_ts=CLOSE, stop_usd=b.stop_dist + 1e-6,
                            target_usd=b.target_dist, hold_bars=b.max_hold_s // 3600)
    assert AR.entry_refusal(eng, ex, arm_id, s, CLOSE + pd.Timedelta(seconds=2)) == "bracket_void"


def _status(state: Path) -> list[tuple[str, str, str]]:
    with Database.open(str(state), "aux").connection() as conn:
        return [(str(b), str(a), str(r)) for b, a, r in
                conn.execute("SELECT broker, arm_id, rule FROM exploration_arm_status").fetchall()]


def test_the_hold_after_a_void_bracket_survives_a_restart_and_the_arm_keeps_its_exits(env, tmp_path):
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    _void_at_the_entry(eng, pb, p.reg.arm_id)
    [(broker, arm_id, rule)] = _status(tmp_path / "eng")
    assert (broker, arm_id) == ("icm", p.reg.arm_id) and "bracket void" in rule
    again, pb2 = _engine(tmp_path / "eng", [_arm("peer", tf="1h")])           # the restart: a fresh process
    exploration.on_pass(again, _at(again, pb2, CLOSE + pd.Timedelta(hours=1)))
    ex = _ex(again)
    assert p.reg.arm_id in ex.arms and p.reg.arm_id in ex.retired             # configured, entries refused
    assert exploration.agent_for(again, exploration.PREFIX + p.reg.arm_id) is not None   # its exits keep its barrier


def test_the_hold_after_a_control_change_is_written_for_the_broker(env, tmp_path):
    p = register(env)
    ctl = ARMS.Arm.model_validate(p.control_row)
    changed = ctl.model_copy(update={"params": {**ctl.params, "signal_rate": ctl.params["signal_rate"] * 2}})
    changed = changed.model_copy(update={"config_hash": ARMS.arm_hash(changed)})
    eng, pb = _engine(tmp_path / "eng", [_arm("peer", tf="1h")])
    [arm_row, ctl_row] = [json.loads(r.row) for r in arm_rows(env)]
    style, cap = arm_rows(env)[0].style, arm_rows(env)[0].max_loss
    ctl_row = {**ctl_row, "params": changed.params, "config_hash": changed.config_hash}
    AR.append_many(str(tmp_path / "eng"), account_id="icm-probe", entries=[(arm_row, style, cap),
                                                                         (ctl_row, style, cap)], source="test")
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    got = [(b, a) for b, a, r in _status(tmp_path / "eng") if "control" in r]
    assert got == [("icm", p.reg.arm_id)]


def test_the_lifecycle_recompute_is_the_engines_bracket_for_the_same_candidate(env, tmp_path):
    """Wilder ATR depends on where its window starts: the nightly recompute reads the engine's exact window
    (`arm_bars`: the arm's lookback), so a bracket the engine accepted is never voided at night."""
    from goldbot.research import arm_integrity as I
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    arm = _ex(eng).arms[p.reg.arm_id]
    got = exploration.arm_bars(eng.bars_1m[eng.bars_1m["visible_at"] <= CLOSE], arm.spec, CLOSE)
    assert got is not None
    engine = S.bracket(S.spec_of(arm.spec), got[0], CLOSE)
    night = I.engine_recompute({"icm-probe": eng.bars_1m})(arm.spec, {"account_id": "icm-probe",
                                                                       "decision_utc": CLOSE})
    assert engine is not None and night == engine
    s = exploration.Pending(arm_id=arm.arm_id, side=1, bar_ts=CLOSE, stop_usd=night.stop_dist,
                            target_usd=night.target_dist, hold_bars=night.max_hold_s // 3600)
    assert AR.bracket_refusal(eng, arm, s, CLOSE + pd.Timedelta(seconds=2)) is None
    assert I.engine_recompute({})(arm.spec, {"account_id": "icm-probe", "decision_utc": CLOSE}) == I.NO_MINUTES

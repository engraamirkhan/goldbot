"""The demo learning cohort (ADR 0095; docs/research/prereg-amendment-2026-11-24-demo-learning-cohort.md).

Owner 2026-11-24, "it is quite concerning that it is not trading at all"; PD + research director + CFO: the ten retired
arms the self-retrain judges send real demo orders again, on icm-probe only, at 0.01 lot, as a capped learning cohort,
while their controls stay replay_only. They stay retired for promotion and learning weights.

* Config: the cohort is exactly the self-retrain's PRENAMED arms, on icm-probe only (no other account registers them),
  minimum lot, no control; caps GBP 15 per arm, GBP 50 a week for the ten, one open per arm, until 2027-01-15; their
  frozen rows keep their retirement; every control stays replay_only; GD-1 and every live account untouched.
* Engine: a member sends a minimum-lot order through the RiskGate and is stamped `learning_cohort`; a retired arm
  outside the cohort, a control or a replay-only row named in it never sends; one open per member; its loss cap stops
  it alone; the weekly cap holds all ten until Monday 00:00 UTC; nothing goes out before the first ledger read; the
  evidence stops it was retired by (daily review, cost read, cost suspension, learning block) do not apply to it.
* Exits are never gated: a cap never closes an open position.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.db import Database
from goldbot.engine import (
    Engine,
    EngineConfig,
    cost_probe,
    exploration,
    exploration_cohort,
    exploration_deregister,
)
from goldbot.engine.exploration import Arm, ExplorationConfig, ExplorationRecord, Pot
from goldbot.ops import accounts as acc_mod
from goldbot.ops.accounts import LearningCohort
from goldbot.research import arm_rules
from goldbot.research import arms as arms_mod
from goldbot.research.arms import Arm as SpecArm
from goldbot.research.self_retrain import PRENAMED
from goldbot.risk import RiskLimits
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_exploration_engine import CLOSE, LONG, LOSS, QUALIFIED, _actions, _at, _rule, _sent, _Terminal

ROOT = Path(__file__).parents[1]
DAY = pd.Timestamp("2025-03-04", tz="UTC")           # the engine's clock in these tests (CLOSE is Tue 2025-03-04)
CONTROLS = ("LCZ1P", "LCZ2P", "LCZ5P", "LCZ3D", "LCZ9D", "LCZ10D", "F1C", "RBRC15", "S0", "GD1CD", "GD1CP")


def _accounts() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]


def _spec_rows() -> dict[str, SpecArm]:
    return {a.id: a for a in arms_mod.load_arms(ROOT / "config" / "exploration_arms.yaml")}


# ------------------------------------------------------------------------------------------------ the config
def test_the_cohort_is_exactly_the_ten_arms_the_self_retrain_judges_and_lives_on_icm_probe_only() -> None:
    accounts = _accounts()
    with_cohort = {k: a["exploration"]["cohort"] for k, a in accounts.items() if (a.get("exploration") or {})
                   .get("cohort")}
    assert list(with_cohort) == ["icm-probe"]
    assert set(with_cohort["icm-probe"]["arms"]) == set(PRENAMED) and len(PRENAMED) == 10
    for arm_id in PRENAMED:
        on = [k for k, a in accounts.items() if arm_id in ((a.get("exploration") or {}).get("arms") or {})]
        assert on == ["icm-probe"], (arm_id, on)


def test_the_cohort_caps_are_15_per_arm_50_a_week_one_open_each_until_2027_01_15() -> None:
    co = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)["icm-probe"].exploration.cohort
    assert co is not None
    assert (co.arm_loss_cap_gbp, co.week_loss_cap_gbp, co.max_open_per_arm) == (15.0, 50.0, 1)
    assert (co.since, co.until) == ("2026-11-24", "2027-01-15")
    assert (ROOT / co.prereg).is_file()


def test_every_cohort_arm_trades_the_minimum_lot_and_one_trade_never_loses_more_than_its_cap() -> None:
    acc = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)["icm-probe"]
    for arm_id in PRENAMED:
        reg = acc.exploration.arms[arm_id]
        assert reg.sizing == "min_lot" and reg.adds == 0 and reg.unit_lots is None, arm_id
        assert reg.control_share is None and reg.max_loss <= 15.0, arm_id


def test_the_cohort_arms_keep_their_retirement_and_send_through_the_broker_mode() -> None:
    rows = _spec_rows()
    for arm_id in PRENAMED:
        assert rows[arm_id].retired and rows[arm_id].control_mode == "broker", arm_id


def test_every_control_of_the_cohort_and_of_gd1_stays_replay_only() -> None:
    rows = _spec_rows()
    for arm_id in CONTROLS:
        assert rows[arm_id].control_mode == "replay_only", arm_id


def test_gd1_is_not_in_the_cohort_and_keeps_its_learning_block_and_registrations() -> None:
    rows, accounts = _spec_rows(), _accounts()
    for arm_id, acc in (("GD1D", "icm-demo"), ("GD1P", "icm-probe")):
        assert arm_id not in PRENAMED and rows[arm_id].role == "learning" and not rows[arm_id].retired
        assert accounts[acc]["exploration"]["arms"][arm_id] == {"style": "swing", "max_loss": 40.0}


def test_no_live_account_names_a_cohort_and_a_cohort_off_a_demo_probe_only_account_is_refused() -> None:
    accounts = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)
    assert all(a.exploration.cohort is None for a in accounts.values() if a.mode != "demo")
    probe = accounts["icm-probe"]
    with pytest.raises(ValueError):
        probe.model_validate({**probe.model_dump(), "mode": "live", "approval_mode": "propose"})
    with pytest.raises(ValueError):                         # every orders path of a cohort is demo probe_only
        probe.model_validate({**probe.model_dump(), "probe_only": False})


def _cohort(**kw: Any) -> dict[str, Any]:
    return {"prereg": "p.md", "arms": ["Z1P"], "since": "2026-11-24", "until": "2027-01-15", "arm_loss_cap_gbp": 15.0,
            "week_loss_cap_gbp": 50.0, **kw}


@pytest.mark.parametrize("change, why", [
    ({"exploration_arm": {"style": "scalp", "max_loss": 6.5, "control_share": 0.05}}, "control"),
    ({"exploration_arm": {"style": "scalp", "max_loss": 20.0}}, "max_loss"),
    ({"cohort": {"arms": ["NOPE"]}}, "not registered"),
])
def test_a_cohort_arm_that_is_a_control_unregistered_or_above_the_arm_cap_is_refused_at_load(change, why) -> None:
    probe = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)["icm-probe"]
    ex = probe.exploration.model_dump()
    ex["arms"]["Z1P"] = change.get("exploration_arm", ex["arms"]["Z1P"])
    ex["cohort"] = _cohort(**change.get("cohort", {}))
    with pytest.raises(ValueError, match=why):
        probe.model_validate({**probe.model_dump(), "exploration": ex})


def test_a_cohort_runs_at_most_60_days_and_its_arm_cap_is_within_its_weekly_cap() -> None:
    with pytest.raises(ValueError, match="60 days"):
        LearningCohort(**_cohort(until="2027-02-28"))
    with pytest.raises(ValueError, match="weekly"):
        LearningCohort(**_cohort(arm_loss_cap_gbp=60.0))


def test_a_cohort_arm_registered_on_a_second_account_is_refused_so_its_orders_never_leave_icm_probe() -> None:
    accounts = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)
    assert acc_mod.cohort_clashes(accounts.values()) is None
    demo = accounts["icm-demo"]
    ex = demo.exploration.model_dump()
    ex["arms"]["Z1P"] = {"style": "scalp", "max_loss": 6.5}
    moved = demo.model_validate({**demo.model_dump(), "exploration": ex})
    clash = acc_mod.cohort_clashes([accounts["icm-probe"], moved])
    assert clash is not None and "Z1P" in clash and "icm-demo" in clash


# ------------------------------------------------------------------------------------------------ the engine
@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The always-long test rule, a fixed GBP 1 at every stop and every round trip, and the engine's start day."""
    monkeypatch.setitem(arm_rules.RULE_FUNCS, LONG, _rule(1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)
    monkeypatch.setattr(exploration_cohort, "_today", lambda eng: DAY)


def _arm(arm_id: str, *, retired: bool = True, role: str = "strategy", control_mode: str = "broker",
         control_share: float | None = None, max_open: int = 5) -> Arm:
    spec = SpecArm(id=arm_id, rule=LONG, timeframe="15m", max_open=max_open,
                   params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4},
                   qualification=QUALIFIED if role == "strategy" and not retired else None)
    upd: dict[str, Any] = {"role": role, "control_mode": control_mode}
    if retired:
        upd |= {"retired_day": "2025-02-20", "retired_rule": "test: retired for lack of evidence"}
    return Arm(spec=spec.model_copy(update=upd), style="intraday", control_share=control_share)


def _cohort_cfg(arms: tuple[str, ...], **kw: Any) -> LearningCohort:
    return LearningCohort(**{"prereg": "p.md", "arms": arms, "since": "2025-03-01", "until": "2025-04-15",
                             "arm_loss_cap_gbp": 15.0, "week_loss_cap_gbp": 50.0, "max_open_per_arm": 1, **kw})


def _engine(tmp_path: Path, arm_list: list[Arm], cohort: LearningCohort | None) -> tuple[Engine, Any]:
    cfg = ExplorationConfig(enabled=True, budget_share=0.9, max_open_total=18,
                            pots={"intraday": Pot(max_open=10)}, pot_budgets=False, arms=tuple(arm_list),
                            learning_max_open=6, cohort=cohort)
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=50, probe_only=True,
                        exploration_arms={a.arm_id: 15.0 for a in arm_list}, daily_loss_limit_abs=220.0)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg),
                 pb, [], {}, ApprovalCenter({111}), limits=limits)
    return eng, pb


def _fill(state: Path, cid: str, arm_id: str, pnl: float | None, sent: pd.Timestamp) -> None:
    rec = ExplorationRecord(
        client_order_id=cid, account_id="icm-probe", arm_id=arm_id, family="x", tf="15m", side=1, lots=0.01,
        signal_utc=sent, sent_utc=sent, atr_usd=5.0, stop_usd=5.0, target_usd=10.0, hold_s=3600, fill_price=2400.0,
        stop_loss=2.0, cost=0.5, exit_utc=sent + pd.Timedelta(minutes=30), exit_price=2399.0, exit_reason="stop",
        pnl=pnl, censored=False)
    with Database.open(state, "aux").write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _arms_sent(eng: Engine) -> list[str]:
    return [d["arm"] for d in _sent(eng)]


def _refusals(eng: Engine, arm_id: str) -> list[str]:
    return [d["action"].removeprefix("exploration_refused:") for d in _actions(eng, "exploration_refused:")
            if d.get("arm") == arm_id]


def test_a_retired_cohort_arm_sends_a_minimum_lot_order_through_the_riskgate_stamped_learning_cohort(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("Z1P"), _arm("Q")], _cohort_cfg(("Z1P",)))
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy                                     # type: ignore[method-assign]
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == ["Z1P"], eng.decisions                       # Q: retired, not in the cohort
    [(intent, gd)] = seen
    assert intent.purpose == "exploration" and intent.arm_id == "Z1P" and gd.allowed
    assert intent.volume_max == intent.volume_min == 0.01 and gd.lots == pytest.approx(0.01)
    [rec] = exploration.trades(str(tmp_path), "icm-probe")
    assert rec.evidence_class == exploration_cohort.EVIDENCE_CLASS and rec.lots == pytest.approx(0.01)
    assert [d["arm"] for d in _actions(eng, exploration_cohort.ADMITTED)] == ["Z1P"]


def test_a_retired_arm_outside_the_cohort_and_an_engine_without_a_cohort_send_nothing(tmp_path):
    eng, pb = _engine(tmp_path / "a", [_arm("Z1P")], None)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == []


@pytest.mark.parametrize("arm, why", [
    (_arm("C", role="control"), "control"),
    (_arm("C", control_share=0.05), "control"),
    (_arm("C", control_mode="replay_only"), "replay_only"),
    (_arm("C", retired=False), "not retired"),
])
def test_a_control_a_replay_only_row_or_a_live_arm_named_in_the_cohort_never_joins_it(tmp_path, arm, why):
    eng, pb = _engine(tmp_path, [arm], _cohort_cfg(("C",)))
    ex = eng.exploration
    assert ex is not None and ex.cohort == frozenset()
    [j] = _actions(eng, exploration_cohort.REFUSED)
    assert j["arm"] == "C" and why in j["why"]
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    if arm.spec.retired:
        assert _arms_sent(eng) == []


def test_a_cohort_member_opens_at_most_one_position_though_its_frozen_row_allows_five(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("Z1P", max_open=5)], _cohort_cfg(("Z1P",)))
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert _arms_sent(eng) == ["Z1P"]
    assert set(_refusals(eng, "Z1P")) == {"cohort_max_open"}


def test_the_arm_loss_cap_stops_that_arm_for_good_and_the_others_keep_trading(tmp_path):
    _fill(tmp_path, "a", "Z1P", -9.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))
    _fill(tmp_path, "b", "Z1P", -6.0, pd.Timestamp("2025-03-03 11:00", tz="UTC"))
    _fill(tmp_path, "old", "Z2P", -40.0, pd.Timestamp("2025-02-27 11:00", tz="UTC"))    # before `since`: not counted
    eng, pb = _engine(tmp_path, [_arm("Z1P"), _arm("Z2P")], _cohort_cfg(("Z1P", "Z2P")))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == ["Z2P"]
    [cap] = _actions(eng, exploration_cohort.LOSS_CAP)
    assert cap["arm"] == "Z1P" and cap["loss"] == 15.0
    assert eng.exploration is not None and "Z1P" in eng.exploration.retired


def test_a_member_stopped_at_its_cap_records_replay_only_again_so_the_self_retrain_keeps_its_rows(tmp_path):
    from goldbot.research import exploration_data as ed
    _fill(tmp_path, "a", "Z1P", -15.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))
    eng, pb = _engine(tmp_path, [_arm("Z1P")], _cohort_cfg(("Z1P",)))
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert _arms_sent(eng) == []
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    z = obs[obs["arm_id"] == "Z1P"]
    assert len(z) == 3 and not z["taken"].any()
    assert set(z["blocked_reason"]) == {"replay_only"}


def test_an_entry_whose_loss_with_open_risk_would_reach_the_arm_cap_is_refused(tmp_path):
    _fill(tmp_path, "a", "Z1P", -13.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))     # 13 + 0 open + 1 + 1 = 15
    eng, pb = _engine(tmp_path, [_arm("Z1P")], _cohort_cfg(("Z1P",)))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == [] and _refusals(eng, "Z1P") == ["cohort_loss_cap"]


def test_the_weekly_cap_holds_every_member_and_the_week_read_starts_again_on_monday(tmp_path):
    for i, a in enumerate(("Z1P", "Z2P", "Z5P", "Z1P")):          # GBP 49 lost this week, no arm at its own cap
        _fill(tmp_path, f"w{i}", a, -12.25, pd.Timestamp("2025-03-03 08:00", tz="UTC") + pd.Timedelta(hours=i))
    eng, pb = _engine(tmp_path, [_arm("Z1P"), _arm("Z2P"), _arm("Z5P"), _arm("S1A")],
                      _cohort_cfg(("Z1P", "Z2P", "Z5P", "S1A"), arm_loss_cap_gbp=30.0))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))                  # 49 + 0 open + 2 >= 50: every member refused
    assert _arms_sent(eng) == []
    assert all(_refusals(eng, a) == ["cohort_week_cap"] for a in ("Z1P", "Z2P", "Z5P", "S1A"))
    _fill(tmp_path, "w9", "S1A", -1.0, pd.Timestamp("2025-03-04 09:00", tz="UTC"))     # GBP 50: held, said once
    ex = eng.exploration
    assert ex is not None
    exploration_cohort.refresh(eng, ex, CLOSE + pd.Timedelta(minutes=1))
    exploration_cohort.refresh(eng, ex, CLOSE + pd.Timedelta(minutes=2))
    [held] = _actions(eng, exploration_cohort.WEEK_CAP)
    assert held["week_loss"] == 50.0 and held["until"] == "2025-03-10T00:00:00+00:00"
    monday = pd.Timestamp("2025-03-10 09:00", tz="UTC")
    with Database.open(tmp_path, "aux").connection() as conn:
        got = exploration_cohort.read(conn, ex.cfg.cohort, ex.cohort, monday, {})   # type: ignore[arg-type]
    assert got.week_pnl == 0.0 and got.loss["Z1P"] == pytest.approx(24.5)
    assert exploration_cohort.week_start(CLOSE) == pd.Timestamp("2025-03-03", tz="UTC")


def test_no_cohort_entry_goes_out_until_the_ledger_has_been_read(tmp_path, monkeypatch):
    def fail(*a: Any, **k: Any) -> Any:
        raise OSError("disk")

    monkeypatch.setattr(exploration_cohort, "read", fail)
    eng, pb = _engine(tmp_path, [_arm("Z1P")], _cohort_cfg(("Z1P",)))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == [] and _refusals(eng, "Z1P") == ["cohort_unread"]
    assert len(_actions(eng, exploration_cohort.UNREAD)) == 1


def test_past_its_until_every_member_stops(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("Z1P"), _arm("Z2P")], _cohort_cfg(("Z1P", "Z2P"), until="2025-03-03"))
    # the start day (2025-03-04) is past `until`: nobody joins
    assert eng.exploration is not None and eng.exploration.cohort == frozenset()
    eng, pb = _engine(tmp_path / "b", [_arm("Z1P"), _arm("Z2P")], _cohort_cfg(("Z1P", "Z2P"), until="2025-03-04"))
    ex = eng.exploration
    assert ex is not None and ex.cohort == {"Z1P", "Z2P"}
    exploration_cohort.refresh(eng, ex, pd.Timestamp("2025-03-05 08:00", tz="UTC"))
    assert sorted(d["arm"] for d in _actions(eng, exploration_cohort.EXPIRED)) == ["Z1P", "Z2P"]
    assert {"Z1P", "Z2P"} <= ex.retired


def test_the_evidence_stops_it_was_retired_by_do_not_stop_a_cohort_member(tmp_path):
    """Daily review retirement (aux.db), a completed cost read, an H-81/edge-cap suspension and the learning block's
    control check: the cohort's caps replace them for a member; a retired arm outside the cohort keeps all of them."""
    learning = _arm("Z3D", role="learning").spec.model_copy(update={"learning": {
        "prereg": "docs/research/learning-2026-11-02.md", "k_id": "0" * 12, "control": "LCZ3D", "loss_cap_gbp": 1.0,
        "until": "2025-03-20", "since": "2025-03-01"}})
    arms = [Arm(spec=learning, style="intraday"), _arm("F1A", role="cost")]
    eng, pb = _engine(tmp_path, arms, _cohort_cfg(("Z3D", "F1A")))
    ex = eng.exploration
    assert ex is not None and ex.cohort == {"Z3D", "F1A"} and not ({"Z3D", "F1A"} & ex.retired)
    assert not ({"Z3D", "F1A"} & ex.learning_group)
    with Database.open(tmp_path, "aux").write() as conn:
        conn.execute("INSERT INTO exploration_arm_status (broker, arm_id, status, day, rule, recorded_ns) VALUES "
                     "('icm', 'Z3D', 'retired', '2025-03-04', 'test', 0)")
    for i in range(arms_mod.COST_ARMS["F1A"][1]):
        _fill(tmp_path, f"c{i}", "F1A", 0.0, pd.Timestamp("2025-03-03 08:00", tz="UTC"))
    ex.cost_scale = {"Z3D": 0.0, "F1A": 0.0}
    exploration.refresh_retired(eng, ex, CLOSE)
    exploration.refresh_cost_reads(eng, ex, CLOSE)
    assert not ({"Z3D", "F1A"} & ex.retired)
    assert exploration_deregister.candidates(ex.cfg) == []


def test_a_cap_never_closes_an_open_position(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("Z1P")], _cohort_cfg(("Z1P",)))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == ["Z1P"] and len(eng.open) == 1
    _fill(tmp_path, "big", "Z1P", -60.0, pd.Timestamp("2025-03-04 09:00", tz="UTC"))   # arm and week caps both hit
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=1)))
    assert len(eng.open) == 1 and len(pb.positions()) == 1                             # still open, every exit kept
    assert _actions(eng, exploration_cohort.LOSS_CAP) and _actions(eng, exploration_cohort.WEEK_CAP)


def test_a_stop_the_riskgate_floors_wider_is_judged_again_against_the_cohort_caps(tmp_path) -> None:
    """The order core re-checks every room on the loss at the gate's stop; the cohort's caps are two of them."""
    _fill(tmp_path, "a", "Z1P", -10.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))
    eng, pb = _engine(tmp_path, [_arm("Z1P"), _arm("Z2P")], _cohort_cfg(("Z1P", "Z2P")))
    ex = eng.exploration
    assert ex is not None
    exploration_cohort.refresh(eng, ex, CLOSE)
    got = exploration_cohort.rooms(ex, "Z1P", 0.5, {"Z1P": 1.0, "Z2P": 2.0})
    assert got == {"cohort_loss_cap": pytest.approx(15.0 - 10.0 - 1.0 - 0.5),
                   "cohort_week_cap": pytest.approx(50.0 - 10.0 - 3.0 - 0.5)}
    assert exploration_cohort.rooms(ex, "Q", 0.5, {}) == {}


# ------------------------------------------------------------------------------------------------ health
def test_entry_silence_counts_the_cohort_arms_as_supply_though_they_are_retired() -> None:
    from goldbot.ops import entry_silence as es
    probe = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)["icm-probe"]
    s = es.supply_for(probe, ["1m", "5m", "15m", "30m", "1h", "4h"])
    assert s is not None and s.cohort is not None
    assert {a: s.arms[a].blocked for a in PRENAMED} == {a: None for a in PRENAMED}
    assert s.arms["RBR1H"].blocked == "spec retired"                     # a retired arm outside the cohort


def test_the_health_detail_shows_the_cohort_week_to_date_pnl_and_the_weekly_hold(tmp_path) -> None:
    from goldbot.ops import entry_silence as es
    co = _cohort_cfg(("Z1P", "Z2P"))
    s = es.AccountSupply(broker="icm", cohort=co)
    _fill(tmp_path, "a", "Z1P", -20.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))
    _fill(tmp_path, "b", "Z2P", -31.0, pd.Timestamp("2025-03-04 10:00", tz="UTC"))
    line = es.cohort_line(s, tmp_path / "aux.db", CLOSE)
    assert line is not None and "week to date -51.00 of the -50 cap over 2 closes" in line
    assert "held to Mon 2025-03-10 00:00 UTC" in line and "cap: Z1P, Z2P" in line
    assert es.cohort_line(es.AccountSupply(broker="icm"), tmp_path / "aux.db", CLOSE) is None

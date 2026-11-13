"""Demo learning limits in the engine (owner 2026-11-02: "fix the limits of our system to trade ... for the system to be
not bound to test, learn and improve signals"; ADR 0061 amendment 2026-11-02, the demo-limits ADR).

* A learning arm (`role: learning`) enters inside its own bounds: at most `learning_max_open` learning positions
  (`learning_slots`), and `exploration_learning.refresh` (once a minute, from this host's ledger) retires it at
  LEARNING_READ_FILLS (300) admissible fills (`exploration_arm_learning_read_due`), at its loss cap counting a fill with
  no P&L as a loss at its stop (`exploration_arm_learning_loss_cap`), or past its `until`; its dedicated control retires
  with it. An unreadable ledger refuses learning entries (fail closed); the stop holds across a restart.
* Every record carries its evidence class (the arm's role at send) and whether a news blackout was in force.
* Without pot budgets (`pot_budgets: false`) the pot and arm-day rooms are dropped; the per-trade cap and the
  exploration budget still bind.
* With `demo_weekend_flat` an entry is refused only inside the last WEEKEND_ENTRY_BUFFER_S before the Friday cut (the
  cut closes every position, censored); a rule that follows the cut keeps its own rule."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import Engine, EngineConfig, cost_probe, exploration, exploration_learning
from goldbot.engine.exploration import Arm, ExplorationConfig, ExplorationRecord, Pot
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules, arms
from goldbot.research.arms import Arm as SpecArm
from goldbot.risk import RiskLimits
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_exploration_engine import CLOSE, LONG, LOSS, SHORT, _actions, _at, _rule, _sent, _Terminal

PREREG = "docs/research/learning-2026-11-02.md"
UNTIL = "2025-03-20"                        # the engine's clock in these tests is March 2025
LOAD_DAY = pd.Timestamp("2025-03-01", tz="UTC")
SINCE = "2025-03-01"                       # the learning arms' registration day in these tests


@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test rules (always long, always short) and a fixed £1 at every stop and every round trip."""
    monkeypatch.setitem(arm_rules.RULE_FUNCS, LONG, _rule(1))
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)


@pytest.fixture(autouse=True)
def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The learning arms' K rows in a registry of the test's own, and the loader's clock in the same March."""
    monkeypatch.setattr(arms, "_now", lambda: LOAD_DAY)
    reg = tmp_path / "research_registry.jsonl"
    reg.write_text("".join(json.dumps({"config_hash": arms.learning_k_id(i, arms.arm_hash(_spec(i)), PREREG),
                                       "family": arms.LEARNING_FAMILY}) + "\n" for i in ("L1", "L2")))
    monkeypatch.setattr(arms, "REGISTRY_PATH", reg)


def _spec(arm_id: str, *, side: int = 1, control: str = "C1", until: str = UNTIL, loss_cap: float = 100.0,
          role: str = "learning", serves: str | None = None, max_open: int = 5, since: str = SINCE) -> SpecArm:
    params: dict[str, Any] = {"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4}
    if role == "control":                   # a random control names what it controls (a dedicated one: its arms)
        params["control"] = arms.LEARNING_CONTROL_PREFIX + serves if serves is not None else "null"
    base = SpecArm(id=arm_id, rule=LONG if side > 0 else SHORT, timeframe="15m", params=params, max_open=max_open)
    if role == "control":
        return base.model_copy(update={"role": "control"})
    block = {"prereg": PREREG, "k_id": arms.learning_k_id(arm_id, arms.arm_hash(base), PREREG), "control": control,
             "loss_cap_gbp": loss_cap, "until": until, "since": since}
    return base.model_copy(update={"role": "learning", "learning": block})


def _learning(arm_id: str = "L1", **kw: Any) -> Arm:
    return Arm(spec=_spec(arm_id, **kw), style="intraday")


def _control(arm_id: str = "C1", serves: str = "L1") -> Arm:
    return Arm(spec=_spec(arm_id, role="control", serves=serves, side=-1), style="intraday", control_share=0.2)


def _engine(tmp_path: Path, arm_list: list[Arm], *, learning_max_open: int = 6, pot_budgets: bool = True,
            pots: dict[str, Pot] | None = None, budget_share: float = 0.9, weekend_flat: bool = False,
            caps: dict[str, float] | None = None, blackout_entries: bool = True) -> tuple[Engine, Any]:
    pots = pots if pots is not None else {"intraday": Pot(gbp=48, max_open=8)}
    cfg = ExplorationConfig(enabled=True, budget_share=budget_share, max_open_total=18, pots=pots,
                            arms=tuple(arm_list), pot_budgets=pot_budgets, learning_max_open=learning_max_open)
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=50, probe_only=True,
                        exploration_arms=caps if caps is not None else {a.arm_id: 40.0 for a in arm_list},
                        daily_loss_limit_abs=220.0, blackout_entries=blackout_entries)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg, demo_weekend_flat=weekend_flat),
                 pb, [], {}, ApprovalCenter({111}), limits=limits)
    return eng, pb


def _fill(state: Path, cid: str, arm_id: str = "L1", *, pnl: float | None = -1.0, censored: bool = False,
          stop_loss: float = 2.0, cost: float = 0.5, sent: pd.Timestamp = pd.Timestamp("2025-03-03 10:00", tz="UTC")
          ) -> None:
    rec = ExplorationRecord(
        client_order_id=cid, account_id="icm-probe", arm_id=arm_id, family="x", tf="15m", side=1, lots=0.01,
        signal_utc=sent, sent_utc=sent, atr_usd=5.0, stop_usd=5.0, target_usd=10.0, hold_s=3600, fill_price=2400.0,
        stop_loss=stop_loss, cost=cost, exit_utc=sent + pd.Timedelta(minutes=30), exit_price=2399.0,
        exit_reason="stop", pnl=pnl, censored=censored)
    with Database.open(state, "aux").write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _arms_sent(eng: Engine) -> list[str]:
    return [d["arm"] for d in _sent(eng)]


# ------------------------------------------------------------------------------------------------ entering
def test_a_learning_arm_enters_beside_its_control_and_each_record_carries_its_evidence_class(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert sorted(_arms_sent(eng)) == ["C1", "L1"], eng.decisions
    recs = {r.arm_id: r for r in exploration.trades(str(tmp_path), "icm-probe")}
    assert recs["L1"].evidence_class == "learning" and recs["C1"].evidence_class == "control"
    assert recs["L1"].in_blackout is False


def test_learning_entries_stop_at_learning_max_open(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(), _control()], learning_max_open=1)
    for k in range(2):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert _arms_sent(eng).count("L1") == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:") if d["arm"] == "L1"] == \
        ["exploration_refused:learning_slots"]


def test_with_no_learning_slot_registered_no_learning_arm_enters(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(), _control()], learning_max_open=0)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == ["C1"]


def test_a_learning_arm_whose_control_is_not_configured_on_this_account_never_enters(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(control="C9")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == []
    [j] = _actions(eng, "exploration_arm_unqualified")
    assert j["arm"] == "L1" and "C9" in j["rule"]


# ------------------------------------------------------------------------------------------------ retiring
def test_at_300_admissible_fills_the_learning_arm_and_its_dedicated_control_retire_with_a_read_due(tmp_path) -> None:
    for i in range(arms.LEARNING_READ_FILLS - 1):
        _fill(tmp_path, f"f{i}", pnl=0.0)
    _fill(tmp_path, "censored", pnl=0.0, censored=True)          # not admissible: the arm is not due at 299
    eng, pb = _engine(tmp_path, [_learning(), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert sorted(_arms_sent(eng)) == ["C1", "L1"]
    _fill(tmp_path, "f-last", pnl=0.0)
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))
    assert sorted(_arms_sent(eng)) == ["C1", "L1"]                                     # nothing more
    [due] = _actions(eng, "exploration_arm_learning_read_due")
    assert due["arm"] == "L1" and due["fills"] == arms.LEARNING_READ_FILLS
    assert due["censored"] == 1                     # quant (5): censored fills reported apart, never in the 300
    assert eng.exploration is not None and {"L1", "C1"} <= eng.exploration.retired


def test_the_300_fill_stop_holds_across_a_restart(tmp_path) -> None:
    for i in range(arms.LEARNING_READ_FILLS):
        _fill(tmp_path, f"f{i}", pnl=0.0)
    eng, pb = _engine(tmp_path, [_learning(), _control()])            # a fresh process: nothing known in memory
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == [] and _actions(eng, "exploration_arm_learning_read_due")


def test_the_loss_cap_retires_the_arm_counting_a_fill_with_no_pnl_as_a_loss_at_its_stop(tmp_path) -> None:
    _fill(tmp_path, "a", pnl=-6.0)
    _fill(tmp_path, "b", pnl=None, stop_loss=3.5, cost=0.5)            # unknown P&L: 4.0 lost, never 0
    eng, pb = _engine(tmp_path, [_learning(loss_cap=10.0), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert sorted(_arms_sent(eng)) == []
    [cap] = _actions(eng, "exploration_arm_learning_loss_cap")
    assert cap["arm"] == "L1" and cap["loss"] == pytest.approx(10.0)


def test_below_its_loss_cap_the_arm_keeps_entering(tmp_path) -> None:
    _fill(tmp_path, "a", pnl=-6.0)
    _fill(tmp_path, "b", pnl=None, stop_loss=1.4, cost=0.5)   # 7.9 lost: + £2 for the new trade is 9.9
    eng, pb = _engine(tmp_path, [_learning(loss_cap=10.0), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert sorted(_arms_sent(eng)) == ["C1", "L1"]


def test_past_its_until_the_learning_arm_retires(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(until="2025-03-03"), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))                  # the engine's day is 2025-03-04
    assert _arms_sent(eng) == []
    [end] = _actions(eng, "exploration_arm_learning_expired")
    assert end["arm"] == "L1"


def test_a_dedicated_control_serving_two_arms_runs_until_both_are_retired(tmp_path) -> None:
    for i in range(arms.LEARNING_READ_FILLS):
        _fill(tmp_path, f"f{i}", pnl=0.0)
    eng, pb = _engine(tmp_path, [_learning("L1"), _learning("L2"), _control(serves="L1,L2")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert sorted(_arms_sent(eng)) == ["C1", "L2"]


def test_an_unreadable_ledger_refuses_learning_entries_and_no_other_arm(tmp_path, monkeypatch) -> None:
    def broken(*a: Any, **k: Any) -> Any:
        raise OSError("disk")
    monkeypatch.setattr(exploration_learning, "read", broken)
    other = Arm(spec=_spec("O1", role="control"), style="intraday", control_share=0.2)
    eng, pb = _engine(tmp_path, [_learning(), _control(), other])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _arms_sent(eng) == ["O1"]
    reasons = {d["arm"]: d["action"] for d in _actions(eng, "exploration_refused:")}
    assert reasons == {"L1": "exploration_refused:learning_unread", "C1": "exploration_refused:learning_unread"}
    assert _actions(eng, "exploration_learning_unread")


# ------------------------------------------------------------------------------------------------ pot budgets
def test_without_pot_budgets_the_pot_and_arm_day_rooms_no_longer_refuse(tmp_path) -> None:
    """The arm-budget case of test_exploration_engine (a 0.5 share of a £4 pot admits two £1 trades): with
    `pot_budgets: false` the third goes too; the pot's slots still count."""
    ctl = Arm(spec=_spec("C1", role="control"), style="intraday", control_share=0.5)
    eng, pb = _engine(tmp_path, [ctl], pot_budgets=False, pots={"intraday": Pot(max_open=5)})
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_arms_sent(eng)) == 3
    assert not [d for d in _actions(eng, "exploration_refused:") if d["action"].endswith(("pot", "arm_budget"))]


def test_without_pot_budgets_the_exploration_budget_and_the_arm_cap_still_bind(tmp_path) -> None:
    ctl = Arm(spec=_spec("C1", role="control"), style="intraday", control_share=0.5)
    eng, pb = _engine(tmp_path, [ctl], pot_budgets=False, pots={"intraday": Pot(max_open=5)},
                      budget_share=0.0125)          # 0.0125 x the day's £200 (2% of £10,000) = £2.50: two £1 trades
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_arms_sent(eng)) == 2
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:budget"]
    eng2, pb2 = _engine(tmp_path / "cap", [ctl], pot_budgets=False, pots={"intraday": Pot(max_open=5)},
                        caps={"C1": 1.2})                           # £1 at the stop + £1 round trip > £1.20
    exploration.on_pass(eng2, _at(eng2, pb2, CLOSE))
    assert [d["action"] for d in _actions(eng2, "exploration_refused:")] == ["exploration_refused:arm_cap"]


def test_a_pot_without_its_gbp_is_refused_while_pot_budgets_are_on() -> None:
    with pytest.raises(ValueError, match="gbp"):
        ExplorationConfig(enabled=True, pots={"intraday": Pot(max_open=1)}, arms=())


# ------------------------------------------------------------------------------------------------ the weekend
FRI_CUT = pd.Timestamp("2026-11-20 19:30", tz="UTC")       # 21:30 Europe/Athens (UTC+2), an ordinary Friday


@pytest.mark.parametrize("before_cut_s, refused", [(3 * 3600, False), (901, False), (899, True), (0, True)])
def test_with_demo_weekend_flat_only_the_last_fifteen_minutes_before_the_cut_refuse_an_entry(
        tmp_path, before_cut_s: int, refused: bool) -> None:
    eng, _ = _engine(tmp_path, [], weekend_flat=True)
    now = FRI_CUT - pd.Timedelta(seconds=before_cut_s)
    assert exploration.WEEKEND_ENTRY_BUFFER_S == 900
    assert exploration.crosses_friday(eng, now, 4 * 3600) is refused


def test_without_demo_weekend_flat_a_hold_that_reaches_the_cut_is_refused_as_before(tmp_path) -> None:
    eng, _ = _engine(tmp_path, [])
    assert exploration.crosses_friday(eng, FRI_CUT - pd.Timedelta(hours=3), 4 * 3600)
    assert not exploration.crosses_friday(eng, FRI_CUT - pd.Timedelta(hours=5), 4 * 3600)


def test_a_rule_that_follows_the_cut_keeps_its_own_rule_with_demo_weekend_flat(tmp_path) -> None:
    eng, _ = _engine(tmp_path, [], weekend_flat=True)
    after_cut = FRI_CUT + pd.Timedelta(minutes=5)
    assert exploration.crosses_friday(eng, after_cut, 60, follows_cut=True)
    assert not exploration.crosses_friday(eng, FRI_CUT - pd.Timedelta(hours=3), 2 * 3600, follows_cut=True)


# ------------------------------------------------------------------------------------------------ review round (PR 295)
def test_m1_a_pot_that_keeps_its_gbp_still_binds_without_pot_budgets(tmp_path) -> None:
    """Trading-safety M1: a pot with a £ figure keeps its sleeve and its arms' days although pot_budgets is false
    (the GS1/GS1C growth pot, the ADR 0061 amendment of 2026-11-01); the 0.5-of-£4 case admits two £1 trades."""
    ctl = Arm(spec=_spec("C1", role="control"), style="intraday", control_share=0.5)
    eng, pb = _engine(tmp_path, [ctl], pot_budgets=False, pots={"intraday": Pot(gbp=4.0, max_open=5)})
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_arms_sent(eng)) == 2
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_budget"]


def test_m1_the_growth_pair_never_holds_more_than_its_pot_at_the_stops(tmp_path) -> None:
    """GS1/GS1C keep their £80 day on icm-demo; here a £3 growth pot holds at most three £1 trades of the pair."""
    pair = [Arm(spec=_spec(i, role="control", side=s), style="growth", control_share=0.5) for i, s in
            (("G1", 1), ("G2", -1))]
    eng, pb = _engine(tmp_path, pair, pot_budgets=False, pots={"growth": Pot(gbp=3.0, max_open=5)})
    for k in range(4):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_arms_sent(eng)) <= 3 and len(eng.open) <= 3
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} <= {"exploration_refused:pot",
                                                                           "exploration_refused:arm_budget"}
    growth = acc_mod.load_accounts(logins=False)["icm-demo"].exploration.pots["growth"]
    assert (growth.gbp, growth.max_open) == (80, 2)


@pytest.mark.parametrize("kind, entered", [("tier1", True), ("news_shock", False), (None, False)])
def test_m2_a_lifted_blackout_enters_through_tier1_only_and_stamps_its_kind(tmp_path, kind: str | None,
                                                                            entered: bool) -> None:
    eng, pb = _engine(tmp_path, [_learning(), _control()], blackout_entries=False)
    tick = _at(eng, pb, CLOSE)
    eng.state.in_blackout, eng.state.blackout_kind = True, kind
    exploration.on_pass(eng, tick)
    assert bool(_arms_sent(eng)) is entered
    if entered:
        rec = next(r for r in exploration.trades(str(tmp_path), "icm-probe") if r.arm_id == "L1")
        assert rec.in_blackout is True and rec.blackout_kind == "tier1"


def test_m2_a_news_shock_inside_a_lifted_tier1_window_is_the_event(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from goldbot.engine import runner
    eng, _ = _engine(tmp_path, [], blackout_entries=False)
    eng.store = SimpleNamespace(  # type: ignore[assignment]
        read=lambda *a, **k: pd.DataFrame({"ts_utc": [CLOSE], "tier": [1], "title": ["NFP"]}))
    monkeypatch.setattr(runner, "blackout_window", lambda *a, **k: {"title": "NFP", "ts_utc": CLOSE.isoformat()})
    monkeypatch.setattr(runner, "engine_shock", lambda cache, *a, **k: (cache, {"title": "x", "received_utc": "y", "kind": "news_shock"}))
    assert eng._blackout(CLOSE) == {"title": "x", "received_utc": "y", "kind": "news_shock"}
    eng.gate.limits = eng.gate.limits.model_copy(update={"blackout_entries": True})     # every other account
    eng._news = (None, pd.DataFrame())
    assert eng._blackout(CLOSE) == {"title": "NFP", "ts_utc": CLOSE.isoformat()}


EARLY = pd.Timestamp("2026-11-27 17:45", tz="UTC")      # day after Thanksgiving: 12:00 CT less 15 minutes


@pytest.mark.parametrize("now, hold_s, refused", [
    (pd.Timestamp("2026-11-27 16:00", tz="UTC"), 3600, False),       # ends 17:00, before the effective cut
    (pd.Timestamp("2026-11-27 16:00", tz="UTC"), 2 * 3600, True),    # reaches it
    (pd.Timestamp("2026-11-27 17:50", tz="UTC"), 60, True),          # past it, before the reopen
    (pd.Timestamp("2026-12-31 17:00", tz="UTC"), 3600, True),        # 31 December
    (pd.Timestamp("2026-12-24 17:00", tz="UTC"), 3600, True),        # 24 December
    (pd.Timestamp("2026-12-31 15:00", tz="UTC"), 3600, False),
    (pd.Timestamp("2026-11-19 17:00", tz="UTC"), 2 * 3600, False),   # an ordinary Thursday
])
def test_m3_with_demo_weekend_flat_an_early_close_day_cuts_entries_15_minutes_before_its_close(
        tmp_path, now: pd.Timestamp, hold_s: int, refused: bool) -> None:
    eng, _ = _engine(tmp_path, [], weekend_flat=True)
    assert exploration.crosses_friday(eng, now, hold_s) is refused


def test_m3_an_unusable_holiday_list_falls_back_to_the_old_hold_rule(tmp_path, monkeypatch) -> None:
    eng, _ = _engine(tmp_path, [], weekend_flat=True)
    thursday = pd.Timestamp("2026-11-19 17:00", tz="UTC")
    assert not exploration.crosses_friday(eng, thursday, 2 * 86400)          # the list read: flat at the cut
    monkeypatch.setattr(eng, "_holiday_list", lambda: None)
    assert exploration.crosses_friday(eng, thursday, 2 * 86400)              # unknown early closes: the old rule


def test_l1_the_loss_cap_refuses_an_entry_whose_loss_with_open_risk_would_reach_it(tmp_path) -> None:
    _fill(tmp_path, "a", pnl=-7.0)
    eng, pb = _engine(tmp_path, [_learning(loss_cap=10.0), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))                     # 7 + 0 open + 2 (stop and round trip) = 9
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))     # 7 + 1 open + 2 = 10: refused
    assert _arms_sent(eng).count("L1") == 1
    assert "exploration_refused:learning_loss_cap" in {d["action"] for d in _actions(eng, "exploration_refused:")
                                                       if d["arm"] == "L1"}


def test_l2_a_dedicated_control_stops_when_its_only_arm_is_cost_suspended(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(), _control()])
    ex = eng.exploration
    assert ex is not None
    exploration_learning.refresh(eng, ex, CLOSE)
    assert "C1" not in ex.retired
    ex.cost_scale["L1"] = 0.0
    exploration_learning.refresh(eng, ex, CLOSE + pd.Timedelta(minutes=1))
    assert "C1" in ex.retired and "L1" not in ex.retired


def test_l3_the_loss_cap_verdict_holds_across_a_restart(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(loss_cap=10.0), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert "L1" in _arms_sent(eng)
    _fill(tmp_path, "big", pnl=-10.0)
    eng2, pb2 = _engine(tmp_path, [_learning(loss_cap=10.0), _control()])
    exploration.on_pass(eng2, _at(eng2, pb2, CLOSE + pd.Timedelta(minutes=15)))
    assert _arms_sent(eng2) == [] and _actions(eng2, "exploration_arm_learning_loss_cap")


def test_l3_the_expiry_verdict_holds_across_a_restart(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_learning(until="2025-03-04"), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))                    # 2025-03-04: its last day
    assert "L1" in _arms_sent(eng)
    eng2, pb2 = _engine(tmp_path, [_learning(until="2025-03-04"), _control()])
    exploration.on_pass(eng2, _at(eng2, pb2, CLOSE + pd.Timedelta(days=1)))
    assert _arms_sent(eng2) == [] and _actions(eng2, "exploration_arm_learning_expired")


def test_quant1_fills_before_the_registration_day_never_count(tmp_path) -> None:
    for i in range(arms.LEARNING_READ_FILLS):
        _fill(tmp_path, f"old{i}", pnl=-1.0, sent=pd.Timestamp("2025-02-27 10:00", tz="UTC"))
    eng, pb = _engine(tmp_path, [_learning(loss_cap=10.0), _control()])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert sorted(_arms_sent(eng)) == ["C1", "L1"]


def test_quant4_learning_shares_and_strategy_shares_sum_to_at_most_one_within_a_style(tmp_path) -> None:
    from tests.test_exploration_engine import _arm as strategy
    arm_list = [_learning("L1"), _learning("L2"), _control(serves="L1,L2"),
                *(strategy(f"q{i}") for i in range(3))]
    eng, _ = _engine(tmp_path, arm_list)
    ex = eng.exploration
    assert ex is not None
    got = exploration.shares(ex, CLOSE)
    assert sum(got.values()) <= 1 + 1e-9 and got["C1"] == pytest.approx(0.2)
    assert got["L1"] == pytest.approx(got["L2"]) and got["L1"] > 0


def test_engine_config_exploration_block_of_every_demo_account_loads() -> None:
    for acc in acc_mod.load_accounts(logins=False).values():
        cfg = exploration.config_for(acc)
        if cfg is not None and acc.account_id != "vantage-247":
            # icm-probe 6 -> 14 for the multi-instrument learning pairs (learning-mi-2026-11-02.md section 5)
            assert cfg.pot_budgets is False, acc.account_id
            assert cfg.learning_max_open == {"icm-probe": 14, "vantage-demo": 12}.get(acc.account_id, 6), acc.account_id

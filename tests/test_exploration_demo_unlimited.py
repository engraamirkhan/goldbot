"""Demo accounts without limits in the exploration engine (owner decision 2026-11-25, "allow absolutely anything";
ADR 0097). With `RiskLimits.demo_unlimited` the account-configured caps no longer refuse a demo entry: the daily,
hourly and session entry budgets, the global and host position caps, pot slots, the learning slots, the net-lot cap,
the exploration budget, the pot and arm-day rooms, the per-trade arm cap, the 8 % drawdown pause, and the demo
learning cohort's caps (tests/test_demo_learning_cohort_unlimited.py). Kept: what the arm's frozen spec says (its
max_open; a learning arm's loss cap, tests/test_exploration_learning_unlimited.py), replay_only never sends, a retired
or unqualified arm stays retired (a research verdict, not a cap), an unknown drawdown still pauses, one arm's day
stays within ARM_RUNAWAY_FRAC of the mark (trading-safety M5), every order is the minimum lot through the RiskGate
with its own stop, every record keeps its evidence class and is tagged `limits_epoch=unlimited`, and exits go first
(engine/order_send.py `entry_yield`)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig, exploration
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.ops.accounts import DailyEntries, LearningCohort
from goldbot.risk import RiskLimits
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_exploration_engine import (
    CLOSE,
    _actions,
    _arm,
    _at,
    _rules,  # noqa: F401 - autouse: the test arms' fixed rules and stop loss
    _sent,
    _Terminal,
    qualified,
)


def _engine(tmp_path: Path, arms: list[Arm], *, unlimited: bool, cohort: LearningCohort | None = None,
            daily: DailyEntries | None = None, caps: float = 40.0, learning_max_open: int = 0,
            pot: Pot | None = None) -> tuple[Engine, Any]:
    arms = [qualified(a) for a in arms]
    cfg = ExplorationConfig(enabled=True, budget_share=0.01, max_open_total=1,
                            pots={"intraday": pot or Pot(gbp=2.0, max_open=1)}, arms=tuple(arms), daily=daily,
                            cohort=cohort, learning_max_open=learning_max_open)
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=1, probe_only=True,
                        exploration_arms={a.arm_id: caps for a in arms}, daily_loss_limit_abs=220.0,
                        demo_unlimited=unlimited)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg),
                 pb, [], {}, ApprovalCenter({111}), limits=limits)
    return eng, pb


def _passes(eng: Engine, pb: Any, n: int) -> None:
    for k in range(n):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))


def _refusals(eng: Engine) -> set[str]:
    return {d["action"].removeprefix("exploration_refused:") for d in _actions(eng, "exploration_refused:")}


def test_with_the_flag_every_slot_room_and_budget_cap_lets_the_entries_through(tmp_path) -> None:
    """One slot everywhere, a £2 pot, a 1 % budget share and a £0.50 arm cap: capped, at most one entry goes out."""
    arms = [_arm("a"), _arm("b"), _arm("c")]
    eng, pb = _engine(tmp_path / "capped", arms, unlimited=False, caps=0.5)
    _passes(eng, pb, 2)
    assert len(_sent(eng)) <= 1 and _refusals(eng)
    eng, pb = _engine(tmp_path / "free", arms, unlimited=True, caps=0.5)
    _passes(eng, pb, 2)
    assert len(_sent(eng)) == 6, _refusals(eng)
    assert not _refusals(eng) - {"duplicate"}


def test_with_the_flag_the_daily_entry_budget_no_longer_refuses(tmp_path) -> None:
    daily = DailyEntries(max_per_day=1, max_per_hour=1)
    eng, pb = _engine(tmp_path / "capped", [_arm("a")], unlimited=False, daily=daily, pot=Pot(gbp=50, max_open=9))
    _passes(eng, pb, 3)
    assert len(_sent(eng)) == 1
    eng, pb = _engine(tmp_path / "free", [_arm("a")], unlimited=True, daily=daily, pot=Pot(gbp=50, max_open=9))
    _passes(eng, pb, 3)
    assert len(_sent(eng)) == 3, _refusals(eng)


def test_with_the_flag_every_order_is_still_the_minimum_lot_through_the_riskgate_with_its_own_stop(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b")], unlimited=True)
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy                                     # type: ignore[method-assign]
    _passes(eng, pb, 1)
    assert len(seen) == 2 and all(i.purpose == "exploration" and gd.allowed for i, gd in seen)
    assert all(gd.lots == pytest.approx(0.01) for _, gd in seen)
    assert all(p.sl is not None and p.sl > 0 for p in pb.positions())
    assert {r.evidence_class for r in exploration.trades(str(tmp_path), "icm-probe")} == {"qualified"}   # a strategy arm's fills: `qualified`


def test_with_the_flag_a_replay_only_row_still_never_sends(tmp_path) -> None:
    arm = _arm("a")
    arm = arm.model_copy(update={"spec": arm.spec.model_copy(update={"control_mode": "replay_only"})})
    eng, pb = _engine(tmp_path, [arm], unlimited=True)
    _passes(eng, pb, 1)
    assert _sent(eng) == []


def test_the_arms_frozen_max_open_still_binds_with_the_flag(tmp_path) -> None:
    """max_open is in the hash-checked ARM-SPEC: overlapping trades of one arm change its effective n (the power_first
    overlap design effect), so it is protocol, not an account limit."""
    eng, pb = _engine(tmp_path, [_arm("a", max_open=1)], unlimited=True, pot=Pot(gbp=50, max_open=9))
    _passes(eng, pb, 3)
    assert len(_sent(eng)) == 1 and "arm_max_open" in _refusals(eng)


def test_every_entry_on_an_unlimited_account_is_tagged_and_none_on_a_capped_one(tmp_path) -> None:
    for free, tag in ((True, exploration.LIMITS_EPOCH), (False, None)):
        eng, pb = _engine(tmp_path / str(free), [_arm("a")], unlimited=free, pot=Pot(gbp=50, max_open=9))
        _passes(eng, pb, 1)
        recs = exploration.trades(str(tmp_path / str(free)), "icm-probe")
        assert recs and {r.limits_epoch for r in recs} == {tag}


def test_a_fill_in_a_news_shock_window_is_sent_and_tagged_so_reads_can_drop_it(tmp_path) -> None:
    """ADR 0097 M1: the shock refusal (ADR 0072 M2) is lifted on an unlimited demo account by the owner's rule; the
    fill carries `in_blackout`/`blackout_kind`, so cost and learning reads can leave shock windows out."""
    eng, pb = _engine(tmp_path, [_arm("a")], unlimited=True, pot=Pot(gbp=50, max_open=9))
    tick = _at(eng, pb, CLOSE)
    eng.state.in_blackout, eng.state.blackout_kind = True, "news_shock"
    exploration.on_pass(eng, tick)
    [rec] = exploration.trades(str(tmp_path), "icm-probe")
    assert (rec.in_blackout, rec.blackout_kind, rec.limits_epoch) == (True, "news_shock", "unlimited")


def test_the_drawdown_pause_is_lifted_but_an_unknown_drawdown_still_pauses(tmp_path, monkeypatch) -> None:
    eng, pb = _engine(tmp_path, [_arm("a")], unlimited=True, pot=Pot(gbp=50, max_open=9))
    monkeypatch.setattr(exploration, "drawdown", lambda e: 0.3)
    assert not exploration.paused(eng, CLOSE)
    capped, _ = _engine(tmp_path / "c", [_arm("a")], unlimited=False)
    assert exploration.paused(capped, CLOSE)
    monkeypatch.setattr(exploration, "drawdown", lambda e: None)
    assert exploration.paused(eng, CLOSE)


def test_a_drawdown_of_half_the_mark_is_journalled_before_the_floor(tmp_path, monkeypatch) -> None:
    eng, pb = _engine(tmp_path, [_arm("a")], unlimited=True)
    monkeypatch.setattr(exploration, "drawdown", lambda e: 0.2)
    exploration.paused(eng, CLOSE)
    assert not _actions(eng, "exploration_dd_warning")
    monkeypatch.setattr(exploration, "drawdown", lambda e: 0.55)
    exploration.paused(eng, CLOSE)
    assert _actions(eng, "exploration_dd_warning")


def test_one_runaway_arm_is_held_before_it_can_take_the_shared_demo_to_its_floor(tmp_path) -> None:
    """Trading-safety M5: containment of the shared learning capacity, not a cap on normal learning: an arm whose loss
    today plus its open risk and this trade would pass a tenth of the mark is refused `arm_runaway`."""
    eng, pb = _engine(tmp_path, [_arm("a")], unlimited=True, pot=Pot(gbp=50, max_open=9))
    tick = _at(eng, pb, CLOSE)
    eng.state.balance_closed_hwm = 5.0              # a tenth is 0.50: below one £1 stop
    exploration.on_pass(eng, tick)
    assert _sent(eng) == [] and "arm_runaway" in _refusals(eng)


def test_while_exits_go_first_the_signals_stay_queued_for_the_next_pass(tmp_path) -> None:
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b")], unlimited=True, pot=Pot(gbp=50, max_open=9))
    ex = eng.exploration
    assert ex is not None
    eng._kill_pending = True
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert _sent(eng) == [] and len(ex.queue) == 2
    assert [d["reason"] for d in _actions(eng, "entry_yield")] == ["exit_first"]
    eng._kill_pending = False
    exploration.on_pass(eng, _at(eng, pb, CLOSE, lag_s=4.0))
    assert len(_sent(eng)) == 2 and not ex.queue

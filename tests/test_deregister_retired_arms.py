"""BACKLOG 187: a retired arm's registration is dropped automatically once the account is flat in it.

Rule (R133, profitability-2026-11-12.md section 4): the retired I0-<instrument> controls and ZCD stay registered "so
their open positions keep every exit (a dropped registration is closed at once as `exploration_orphan`)"; "once each
account shows no open position of them, drop the rows". The engine does it itself after a successful broker position
read (`exploration.sweep` at every reconciliation): a spec-retired arm with no open position, no order in flight and
no waiting signal is de-registered (no signal work, no candidate row), journalled once (aux.db
`exploration_arm_deregistered`) and never touches a position."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import exploration, exploration_deregister
from goldbot.engine.exploration import Arm
from goldbot.execution.broker import Tick
from goldbot.ops import accounts as acc_mod
from tests.test_exploration_engine import CLOSE, TICKS, _arm, _at, _engine
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

ACTION = exploration_deregister.ACTION
R133 = {"icm-probe": {"I0-USTEC", "I0-XAUEUR", "I0-DE40", "I0-US500"}, "vantage-demo": {"I0-US30", "I0-XAUJPY"},
        "icm-demo": {"ZCD"}}


def _retired(arm: Arm) -> Arm:
    return arm.model_copy(update={"spec": arm.spec.model_copy(update={"retired_day": "2026-11-13",
                                                                      "retired_rule": "test ruling"})})


def _journalled(eng) -> list[dict]:
    return [d for d in eng.decisions if d.get("action") == ACTION]


def test_a_retired_arm_with_no_open_position_is_deregistered_after_a_broker_read_and_journalled_once(tmp_path):
    eng, pb = _engine(tmp_path, [_retired(_arm("r")), _arm("live")])
    tick = _at(eng, pb, CLOSE)
    assert eng.exploration is not None and eng.exploration.deregistered == frozenset()
    eng._reconcile(tick.ts_utc)
    assert eng.exploration.deregistered == {"r"}                    # the active arm is never a candidate
    [row] = _journalled(eng)
    assert row["arm"] == "r" and row["rule"] == "test ruling"
    eng._reconcile(tick.ts_utc)                                      # idempotent: no second journal row
    assert len(_journalled(eng)) == 1
    assert exploration_deregister.recorded(str(tmp_path), "icm-probe") == {"r"}


def test_a_restart_deregisters_the_flat_arm_again_without_journalling_it_twice(tmp_path):
    eng, pb = _engine(tmp_path, [_retired(_arm("r"))])
    eng._reconcile(_at(eng, pb, CLOSE).ts_utc)
    eng2, _ = _engine(tmp_path, [_retired(_arm("r"))], pb=pb)
    eng2._reconcile(_at(eng2, pb, CLOSE).ts_utc)
    assert eng2.exploration is not None and eng2.exploration.deregistered == {"r"}
    assert _journalled(eng2) == []


def test_nothing_is_deregistered_when_the_broker_positions_cannot_be_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_retired(_arm("r"))])
    tick = _at(eng, pb, CLOSE)

    def down(*a, **kw):
        raise RuntimeError("terminal down")

    monkeypatch.setattr(pb, "positions", down)
    eng._reconcile(tick.ts_utc)
    assert eng.exploration is not None and eng.exploration.deregistered == frozenset()
    assert _journalled(eng) == []


def test_a_retired_arm_stays_registered_while_its_position_is_open_and_the_position_is_never_touched(tmp_path):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [arm])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)
    before = [(p.position_id, p.sl, p.tp, p.lots) for p in pb.positions()]
    eng2, _ = _engine(tmp_path, [_retired(arm)], pb=pb)                 # the restart after the retiring deploy
    eng2.open.clear()
    eng2._reconcile(tick.ts_utc)
    assert eng2.exploration is not None and eng2.exploration.deregistered == frozenset()
    assert eng2.open[pid].agent_id == "explore:s" and _journalled(eng2) == []
    assert [(p.position_id, p.sl, p.tp, p.lots) for p in pb.positions()] == before   # untouched
    pb.close(pid)                                                        # the broker's stop takes it out
    eng2.open.pop(pid)
    eng2._reconcile(tick.ts_utc)
    assert eng2.exploration.deregistered == {"s"} and len(_journalled(eng2)) == 1


def test_an_order_in_flight_or_a_waiting_signal_keeps_a_retired_arm_registered(tmp_path):
    eng, pb = _engine(tmp_path, [_retired(_arm("r"))])
    tick = _at(eng, pb, CLOSE)
    ex = eng.exploration
    assert ex is not None
    eng._orders["x"] = SimpleNamespace(status="sending", agent_id="explore:r")   # type: ignore[assignment]
    assert exploration_deregister.holds(eng, ex, "r") == "order in flight"
    eng._orders.pop("x")
    ex.deferred["r"] = SimpleNamespace(arm_id="r")                      # type: ignore[assignment]
    assert exploration_deregister.holds(eng, ex, "r") == "signal waiting"
    ex.deferred.clear()
    assert exploration_deregister.holds(eng, ex, "r") is None
    eng._reconcile(tick.ts_utc)
    assert ex.deregistered == {"r"}


def test_a_deregistered_arm_takes_no_bar_work_at_its_bar_close(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_retired(_arm("r"))])
    tick = _at(eng, pb, CLOSE)
    read: list[str] = []
    shared = exploration._shared_bars

    def spy(frame, tf, through, ns):
        read.append(tf)
        return shared(frame, tf, through, ns)

    monkeypatch.setattr(exploration, "_shared_bars", spy)
    exploration.step(eng, CLOSE)
    assert read == ["15m"]                                  # a retired arm still costs its book a resample
    read.clear()
    eng._reconcile(tick.ts_utc)
    exploration.step(eng, CLOSE)
    assert read == []                                       # de-registered: no bar work at all


def test_the_r133_retired_arms_are_the_ones_dropped_once_flat_and_no_runnable_arm_is():
    found: dict[str, set[str]] = {}
    for acc in acc_mod.load_accounts(logins=False).values():
        cfg = exploration.config_for(acc)
        if cfg is None:
            continue
        found[acc.account_id] = set(exploration_deregister.candidates(cfg))
        runnable, _refused = exploration.qualification_by_account([acc])[acc.account_id]
        assert not set(runnable) & found[acc.account_id], acc.account_id
    for account_id, arms in R133.items():
        assert arms <= found.get(account_id, set()), (account_id, arms - found.get(account_id, set()))

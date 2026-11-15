"""Replay-only controls and the S0R cost-read exemption (performance-analyst spec 2026-11-15;
docs/research/prereg-amendment-2026-11-15-replay-only-controls.md).

* An unpaired gold random control in `control_mode: replay_only` records and labels every candidate and never sends
  an order: nothing it signals reaches the RiskGate, an order intent or the order core.
* S0R (a cost arm by its section 4 read, arms-2026-10-25.md) is exempt from control thinning (propensity 1) until it
  has 100 stop exits, then stops itself.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.engine import cost_probe, exploration
from goldbot.engine import exploration_budget as budget
from goldbot.engine.exploration import LEDGER, Arm, ExplorationRecord
from goldbot.ops.accounts import DailyEntries
from goldbot.research import arms as arms_mod
from goldbot.research import exploration_data as ed
from goldbot.research.arms import Arm as SpecArm
from tests.test_exploration_daily_budget import START, _engine, _history
from tests.test_exploration_engine import (
    _actions,
    _arm,
    _at,
    _rules,  # noqa: F401 - the always-long rule and the fixed loss (autouse)
    _sent,
)

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.usefixtures("_rules")


def _replay_only(arm: Arm) -> Arm:
    spec = arm.spec.model_copy(update={"role": "control", "control_mode": "replay_only"})
    return arm.model_copy(update={"spec": spec})


def _spec_rows() -> dict[str, SpecArm]:
    return {a.id: a for a in arms_mod.load_arms(ROOT / "config" / "exploration_arms.yaml")}


# ------------------------------------------------------------------------------------------------ the schema
def test_replay_only_is_a_control_mode_and_refused_on_any_other_role():
    with pytest.raises(ValidationError, match="replay_only"):
        SpecArm(id="X", rule="random_bracket", timeframe="1m", role="strategy", control_mode="replay_only",
                params={"minutes": [0], "window_utc": ["07:00", "19:50"], "stop_usd": 3.0, "target_r": 1.0,
                        "hold_bars": 30})


def test_replay_only_is_refused_on_a_non_gold_control():
    with pytest.raises(ValidationError, match="gold"):
        SpecArm(id="X", rule="random_bracket", timeframe="1m", role="control", control_mode="replay_only",
                params={"instrument": "XAGUSD", "minutes": [0], "window_utc": ["07:00", "19:50"], "stop_atr": 2.0,
                        "target_r": 1.0, "hold_bars": 30})


def test_s0_is_replay_only_and_not_retired_so_it_keeps_recording_its_twin():
    rows = _spec_rows()
    assert rows["S0"].control_mode == "replay_only" and not rows["S0"].retired


def test_s0r_and_the_execution_controls_stay_broker_traded():
    rows = _spec_rows()
    for a in ("S0R", "GS1C", "IEX1H", "VEX15", "VEX1H"):
        assert rows[a].control_mode == "broker" and not rows[a].retired, a


def test_s1a_and_s1b_carry_the_retirement_the_control_thinning_amendment_named():
    rows = _spec_rows()
    for a in ("S1A", "S1B"):
        assert rows[a].retired_day == "2026-11-13" and "control-thinning" in rows[a].retired_rule, a


# ------------------------------------------------------------------------------------------------ the engine
def test_a_replay_only_control_records_every_candidate_and_never_creates_an_order_intent(tmp_path, monkeypatch):
    calls: list[str] = []
    intent, place = exploration._intent, cost_probe._place

    def spy_intent(*a: Any, **k: Any) -> Any:
        calls.append("intent:" + a[1].arm_id)
        return intent(*a, **k)

    def spy_place(*a: Any, **k: Any) -> Any:
        calls.append("place:" + k["journal"]["arm"])
        return place(*a, **k)

    monkeypatch.setattr(exploration, "_intent", spy_intent)
    monkeypatch.setattr(cost_probe, "_place", spy_place)
    eng, pb = _engine(tmp_path, [_replay_only(_arm("S0", style="scalp", control_share=0.25, max_open=10)),
                                 _arm("e", style="scalp", max_open=10)], None)
    check = eng.gate.check

    def spy_check(i: Any, *a: Any, **k: Any) -> Any:
        calls.append("gate:" + i.arm_id)
        return check(i, *a, **k)

    eng.gate.check = spy_check                                 # type: ignore[method-assign,assignment]
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, START + pd.Timedelta(minutes=15 * k)))
    assert not [c for c in calls if c.endswith(":S0")], calls
    assert {d["arm"] for d in _sent(eng)} == {"e"}                       # the edge arm still trades
    assert not [d for d in eng.decisions if d.get("arm") == "S0" and str(d.get("action", "")).startswith("gate")]
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    s0 = obs[obs["arm_id"] == "S0"]
    assert len(s0) == 3 and not s0["taken"].any()
    assert set(s0["blocked_reason"]) == {"replay_only"} and set(s0["mu"]) == {0.0} and s0["is_control"].all()
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        assert not LEDGER.select(conn, "arm_id = ?", ("S0",))           # no order row, filled or not


def test_a_replay_only_control_takes_no_place_in_the_daily_budget(tmp_path):
    eng, pb = _engine(tmp_path, [_replay_only(_arm("S0", style="scalp", control_share=0.25, max_open=10))],
                      DailyEntries(max_per_day=50))
    exploration.on_pass(eng, _at(eng, pb, START))
    # refused before any budget check: its one journal row says why, no budget refusal is ever reached
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:replay_only"}
    assert not _sent(eng)


def test_replay_only_candidates_are_recorded_and_journalled_even_when_no_entry_may_go(tmp_path, monkeypatch):
    """Code review LOW: an unsettled order (or no room this pass) holds the queue; a replay-only candidate never
    waits on it, so it is recorded at once (blocked replay_only, mu 0) instead of going stale."""
    eng, pb = _engine(tmp_path, [_replay_only(_arm("S0", style="scalp", control_share=0.25, max_open=10))], None)
    monkeypatch.setattr(exploration, "_unsettled", lambda *a: True)
    exploration.on_pass(eng, _at(eng, pb, START))
    [row] = _actions(eng, "exploration_refused:replay_only")
    assert row["arm"] == "S0"
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    s0 = obs[obs["arm_id"] == "S0"]
    assert list(s0["blocked_reason"]) == ["replay_only"] and list(s0["mu"]) == [0.0]
    assert not eng.exploration.queue                                     # type: ignore[union-attr]


def test_a_position_open_when_its_arm_turns_replay_only_still_exits_at_its_stop_and_is_recorded(tmp_path):
    """Trading-safety LOW: the switch is entries only. An S0 position opened while it was broker-traded keeps its
    stop (and target and hold); the broker's stop closes it and its close is written to the ledger."""
    from goldbot.execution.broker import Tick
    from tests.test_exploration_engine import CLOSE
    from tests.test_exploration_engine import _engine as _plain_engine
    eng, pb = _plain_engine(tmp_path, [_arm("S0", control_share=0.25, max_open=1)])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    ex = eng.exploration
    assert ex is not None
    ex.arms["S0"] = _replay_only(ex.arms["S0"])                          # the release that makes S0 replay-only
    sl = eng.open[pid].sl
    assert sl is not None
    eng._last_reconcile = CLOSE
    eng.on_tick(Tick(ts_utc=CLOSE + pd.Timedelta(seconds=40), bid=sl - 1.0, ask=sl - 0.8))
    assert not pb.positions() and pid not in eng.open
    [rec] = exploration.trades(str(tmp_path))
    assert rec.arm_id == "S0" and rec.exit_utc is not None and rec.exit_reason == "stop"


# ------------------------------------------------------------------------------------------------ S0R
def _p_zero_history(tmp_path: Path) -> None:
    """S0R's pair (Z1P) filled only in another hour: p = 0 in START's cell under the thinning rule."""
    _history(tmp_path, fills={"Z1P": [START - pd.Timedelta(days=1, hours=5)]},
             signals={"S0R": [START - pd.Timedelta(days=1)]})


def test_s0r_is_exempt_from_thinning_with_propensity_1_before_its_100_stop_exits(tmp_path):
    _p_zero_history(tmp_path)
    s0r = _arm("S0R", style="scalp", control_share=0.05, max_open=10)
    assert budget.pairs({"S0R": s0r, "Z1P": _arm("Z1P", style="scalp")})["S0R"] == ("Z1P",)
    eng, pb = _engine(tmp_path, [s0r, _arm("Z1P", style="scalp", max_open=10)], DailyEntries(max_per_day=50))
    exploration.on_pass(eng, _at(eng, pb, START))
    assert "S0R" in {d["arm"] for d in _sent(eng)}, eng.decisions
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    today = obs[(obs["arm_id"] == "S0R") & (obs["decision_utc"] == START)]
    assert list(today["mu"]) == [1.0] and bool(today["taken"].iloc[0])


def _exits(tmp_path: Path, n: int, *, reason: str = "stop", first: int = 9000) -> None:
    """n closed S0R trades of icm-probe two days ago, each closed by `reason`."""
    db = exploration.ledger_db(str(tmp_path))
    at = START - pd.Timedelta(days=2)
    with db.write() as conn:
        for i in range(first, first + n):
            LEDGER.upsert(conn, ExplorationRecord(
                client_order_id=f"r{i}", account_id="icm-probe", arm_id="S0R", family="random_bracket", tf="1m",
                side=1, lots=0.01, signal_utc=at, sent_utc=at, atr_usd=3.0, stop_usd=3.0, target_usd=3.0, hold_s=1800,
                position_id=i, fill_price=2000.0, exit_utc=at + pd.Timedelta(minutes=5), exit_price=1997.0,
                exit_reason=reason, pnl=-3.0, censored=False))


def test_s0r_keeps_entering_at_99_stop_exits_and_its_target_exits_never_count(tmp_path):
    _exits(tmp_path, 99)
    _exits(tmp_path, 50, reason="target", first=8000)
    eng, pb = _engine(tmp_path, [_arm("S0R", style="scalp", control_share=0.05, max_open=10)], None)
    exploration.on_pass(eng, _at(eng, pb, START))
    assert "S0R" not in eng.exploration.retired                          # type: ignore[union-attr]
    assert "S0R" in {d["arm"] for d in _sent(eng)}


def test_s0r_refuses_entries_while_its_stop_exit_count_cannot_be_read(tmp_path, monkeypatch):
    """Trading-safety LOW: the exemption rests on the count; unread, S0R enters nothing (fail closed) and says so
    once. The cost arms keep their old behaviour (they run and warn)."""
    def broken(*a: Any) -> int:
        raise OSError("ledger locked")
    monkeypatch.setattr(exploration, "stop_exits", broken)
    eng, pb = _engine(tmp_path, [_arm("S0R", style="scalp", control_share=0.05, max_open=10)], None)
    exploration.on_pass(eng, _at(eng, pb, START))
    assert not _sent(eng)
    assert _actions(eng, "exploration_stop_exit_read_unread")
    assert _actions(eng, "exploration_refused:stop_exit_read_unread")


def test_s0r_stops_itself_at_its_100th_stop_exit_and_says_so(tmp_path):
    _exits(tmp_path, 100)
    eng, pb = _engine(tmp_path, [_arm("S0R", style="scalp", control_share=0.05, max_open=10)], None)
    exploration.on_pass(eng, _at(eng, pb, START))
    ex = eng.exploration
    assert ex is not None and "S0R" in ex.retired and ex.unqualified["S0R"] == arms_mod.STOP_EXIT_READ_DONE
    assert not _sent(eng)
    [done] = _actions(eng, "exploration_arm_cost_read_complete")
    assert done["arm"] == "S0R" and done["stop_exits"] == 100

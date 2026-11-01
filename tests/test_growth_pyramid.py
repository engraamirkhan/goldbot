"""Growth sprint 2026-11-01 (docs/research/growth-sprint-2026-11-01.md section 3, the deferred part): GS1's pyramid
adds, and GS1C's identical adds so the control matches.

"Add a unit at each +1 R from the first unit's entry, up to 3 adds. After every add, open risk stays <= 1 unit: the
stops of the earlier units move up so the total loss at the stops is <= the first unit's risk." Every add goes through
the RiskGate (`cost_probe._place`), carries `group_id` = the first unit's client order id, and a refused add is a
blocked candidate (a shadow fill). Exits are never gated; after a restart the open groups are adopted and the ladder
resumes where it was."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import exploration as X
from goldbot.engine import exploration_pyramid as P
from goldbot.engine.exploration import Arm, Pot
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules
from goldbot.research.growth_ledger import fold_groups
from tests.test_exploration_engine import CLOSE, LONG, SHORT, _arm, _at, _engine, _rule


@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(arm_rules.RULE_FUNCS, LONG, _rule(1))
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))


def _pyr(arm_id: str, *, side: int = 1, adds: int = 3, control_share: float | None = None,
         target_r: float = 10.0) -> Arm:
    a = _arm(arm_id, side=side, style="growth", stop_usd=5.0, target_r=target_r, hold_bars=16, max_open=1,
             control_share=control_share)
    return a.model_copy(update={"adds": adds})


def _eng(tmp_path: Path, arms: list[Arm], *, risk: float = 0.001, pb: PaperBroker | None = None,
         growth: bool = True) -> tuple[Any, PaperBroker]:
    eng, pb = _engine(tmp_path, arms, pots={"growth": Pot(gbp=200.0, max_open=2)},
                      caps={a.arm_id: 40.0 for a in arms}, pb=pb)
    ids = frozenset(a.arm_id for a in arms) if growth else frozenset()
    eng.gate.limits = eng.gate.limits.model_copy(update={"risk_per_trade": risk, "growth_arms": ids})
    return eng, pb


def _tick(eng: Any, pb: PaperBroker, seconds: float, mid: float) -> Tick:
    """A quote `seconds` after CLOSE inside the same 15m bar (no new signal), the engine's pass on it."""
    t = Tick(ts_utc=CLOSE + pd.Timedelta(seconds=seconds), bid=round(mid - 0.1, 2), ask=round(mid + 0.1, 2))
    pb.on_tick(t)
    eng._last_tick = t
    eng._refresh_account(t)
    X.on_pass(eng, t)
    return t


def _first(eng: Any, tmp_path: Path, arm_id: str = "GS1") -> X.ExplorationRecord:
    [rec] = [r for r in X.trades(str(tmp_path)) if r.arm_id == arm_id and r.group_id is None]
    assert rec.fill_price is not None
    return rec


def _fill(rec: X.ExplorationRecord) -> float:
    assert rec.fill_price is not None
    return rec.fill_price


def _units(pb: PaperBroker, cid_prefix: str) -> list[Any]:
    return sorted((p for p in pb.positions() if str(p.comment or "").startswith(cid_prefix)),
                  key=lambda p: p.open_time_utc)


def _group_risk(units: list[Any], side: int) -> float:
    """Lots x USD per oz the group loses if every stop is hit (signed: a stop past its entry locks a gain)."""
    return sum(p.lots * side * (p.open_price - p.sl) for p in units)


def _start(tmp_path: Path, arms: list[Arm], **kw: Any) -> tuple[Any, PaperBroker]:
    eng, pb = _eng(tmp_path, arms, **kw)
    X.on_pass(eng, _at(eng, pb, CLOSE))
    return eng, pb


# ------------------------------------------------------------------------------------------------ the ladder
def test_an_add_goes_out_at_each_plus_one_r_from_the_first_units_entry_up_to_three_adds(tmp_path) -> None:
    eng, pb = _start(tmp_path, [_pyr("GS1")])
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    prefix = first.client_order_id[:7]
    assert len(_units(pb, prefix)) == 1
    _tick(eng, pb, 5, e0 + 0.9 * r)
    assert len(_units(pb, prefix)) == 1                            # below +1 R: no add
    for k, s in ((1, 10), (2, 20), (3, 30)):
        _tick(eng, pb, s, e0 + k * r + 0.05)
        units = _units(pb, prefix)
        assert len(units) == 1 + k, eng.decisions[-6:]
        assert units[-1].open_price >= e0 + k * r                  # added at its level
        assert units[-1].lots == pytest.approx(units[0].lots)      # a unit: the first unit's lots
    _tick(eng, pb, 40, e0 + 5 * r)
    assert len(_units(pb, prefix)) == 4                            # three adds at most
    assert all(str(d.get("action")) != "exploration_error" for d in eng.decisions)


def test_open_risk_stays_at_most_one_unit_after_every_add_and_earlier_stops_only_move_up(tmp_path) -> None:
    eng, pb = _start(tmp_path, [_pyr("GS1")])
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    prefix = first.client_order_id[:7]
    budget = _units(pb, prefix)[0].lots * r                        # the first unit's risk, lots x USD/oz
    stops: dict[int, float] = {p.position_id: p.sl for p in _units(pb, prefix)}
    for k, s in ((1, 10), (2, 20), (3, 30)):
        _tick(eng, pb, s, e0 + k * r + 0.05)
        units = _units(pb, prefix)
        assert len(units) == 1 + k
        assert _group_risk(units, 1) <= budget + 0.01 * len(units) * units[0].lots + 1e-9
        for p in units:
            assert p.sl >= stops.get(p.position_id, p.sl) - 1e-9       # a ratchet: never looser
            stops[p.position_id] = p.sl
            assert eng.open[p.position_id].sl == pytest.approx(p.sl)   # the engine's copy is the broker's
    # the newest add keeps its own 1 R stop; every earlier unit sits at it, where none of them loses
    units = _units(pb, prefix)
    assert units[-1].sl == pytest.approx(units[-1].open_price - r, abs=0.02)
    assert all(p.sl >= units[-1].sl - 0.01 for p in units[:-1])
    assert all(p.sl >= p.open_price - 1e-9 for p in units[:-1])


def test_the_stop_rule_puts_the_earlier_units_where_the_group_loses_exactly_one_unit() -> None:
    long = [P.Unit(position_id=1, lots=0.02, price=100.0, sl=95.0, add_no=0),
            P.Unit(position_id=2, lots=0.02, price=105.0, sl=100.0, add_no=1)]
    assert P.group_stop(long, budget=0.02 * 5.0, side=1) == pytest.approx(100.0)
    three = [*long, P.Unit(position_id=3, lots=0.02, price=110.0, sl=105.0, add_no=2)]
    # the earlier units go to the newest add's stop: no earlier unit loses there, the group loses its one unit
    assert P.group_stop(three, budget=0.02 * 5.0, side=1) == pytest.approx(105.0)
    short = [P.Unit(position_id=1, lots=0.02, price=100.0, sl=105.0, add_no=0),
             P.Unit(position_id=2, lots=0.01, price=95.0, sl=100.0, add_no=1)]
    assert P.group_stop(short, budget=0.02 * 5.0, side=-1) == pytest.approx(100.0)
    # an add whose stop the gate floored wider (7 USD, R = 5): its own risk is 0.14 > the 0.10 budget, so the earlier unit
    # locks 0.04: 0.02 x (100 - S) = 0.10 - 0.14 -> S = 102
    wide = [long[0], P.Unit(position_id=2, lots=0.02, price=105.0, sl=98.0, add_no=1)]
    assert P.group_stop(wide, budget=0.02 * 5.0, side=1) == pytest.approx(102.0)
    assert P.group_stop(long[:1], budget=0.1, side=1) is None                            # no add: nothing moves
    assert P.tighter_round(100.001, 1) == pytest.approx(100.01)
    assert P.tighter_round(100.009, -1) == pytest.approx(100.0)


def test_all_three_adds_fit_an_arm_day_of_one_unit_because_earlier_units_lose_nothing_at_the_newest_stop(
        tmp_path) -> None:
    """icm-demo's shape (GS1's day = its per-trade cap, one unit): the engine counts each unit's loss at its own stop
    with a locked gain at 0, so a rule leaving an earlier unit a loss (the earlier units at their average entry: 1.5
    units counted at add 2) would refuse adds 2 and 3 by the arm's day."""
    arms = [_pyr("GS1")]
    eng, pb = _engine(tmp_path, arms, pots={"growth": Pot(gbp=40.0, max_open=2)}, caps={"GS1": 12.0})
    eng.gate.limits = eng.gate.limits.model_copy(update={"risk_per_trade": 0.001, "growth_arms": frozenset({"GS1"})})
    X.on_pass(eng, _at(eng, pb, CLOSE))
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    for k, s in ((1, 10), (2, 20), (3, 30)):
        _tick(eng, pb, s, e0 + k * r + 0.05)
    assert len(_units(pb, first.client_order_id[:7])) == 4, [d for d in eng.decisions if "refused" in str(d)]


# ------------------------------------------------------------------------------------------------ group id
def test_every_add_carries_the_first_units_client_order_id_as_its_group_id(tmp_path) -> None:
    eng, pb = _start(tmp_path, [_pyr("GS1")])
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    _tick(eng, pb, 10, e0 + r + 0.05)
    _tick(eng, pb, 20, e0 + 2 * r + 0.05)
    recs = [x for x in X.trades(str(tmp_path)) if x.arm_id == "GS1"]
    adds = [x for x in recs if x.client_order_id != first.client_order_id]
    assert len(adds) == 2 and {x.group_id for x in adds} == {first.client_order_id}
    assert first.group_id is None
    assert all(x.fill_price is not None and x.stop_loss for x in adds)
    # the add ids are deterministic from the group (restart-safe) and decode back to it
    for k, x in enumerate(sorted(adds, key=lambda x: x.sent_utc), start=1):
        assert P.group_of(eng, eng.exploration.arms["GS1"], x.client_order_id) == (first.client_order_id, k)
    assert P.group_of(eng, eng.exploration.arms["GS1"], first.client_order_id) == (first.client_order_id, 0)
    # once closed the read folds the group into one trade, R over the first unit's stop
    for pid in list(eng.open):
        eng._try_close(pid, "time_exit", CLOSE + pd.Timedelta(minutes=5))
    eng._flush_closed()
    closed = [x for x in X.trades(str(tmp_path)) if x.arm_id == "GS1"]
    folded = fold_groups([x.model_copy(update={"arm_id": "GS1"}) for x in closed])
    assert len(folded) == 1


# ------------------------------------------------------------------------------------------------ the paired control
def test_the_control_gets_identical_adds_at_the_same_r_levels(tmp_path) -> None:
    eng, pb = _start(tmp_path, [_pyr("GS1"), _pyr("GS1C", control_share=0.3)], growth=False)
    a, c = _first(eng, tmp_path, "GS1"), _first(eng, tmp_path, "GS1C")
    e0, r = _fill(a), a.stop_usd
    assert c.stop_usd == pytest.approx(r) and _fill(c) == pytest.approx(e0)
    for k, s in ((1, 10), (2, 20), (3, 30)):
        _tick(eng, pb, s, e0 + k * r + 0.05)
    ua, uc = _units(pb, a.client_order_id[:7]), _units(pb, c.client_order_id[:7])
    assert len(ua) == len(uc) == 4
    levels = [round((p.open_price - e0) / r) for p in ua]
    assert levels == [round((p.open_price - e0) / r) for p in uc] == [0, 1, 2, 3]
    assert [p.lots for p in ua] == [p.lots for p in uc]
    assert [p.sl for p in ua] == pytest.approx([p.sl for p in uc])


# ------------------------------------------------------------------------------------------------ refusals
def test_an_add_over_the_net_lot_cap_is_refused_recorded_blocked_and_leaves_the_stops_alone(tmp_path) -> None:
    # 0.25% of £10,000 at a $5 stop: 0.05 lots a unit, so the second add would make 0.15 net lots (> 0.10)
    eng, pb = _start(tmp_path, [_pyr("GS1")], risk=0.0025)
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    prefix = first.client_order_id[:7]
    assert _units(pb, prefix)[0].lots == pytest.approx(0.05)
    _tick(eng, pb, 10, e0 + r + 0.05)
    assert len(_units(pb, prefix)) == 2
    before = {p.position_id: p.sl for p in _units(pb, prefix)}
    _tick(eng, pb, 20, e0 + 2 * r + 0.05)
    assert len(_units(pb, prefix)) == 2
    assert {p.position_id: p.sl for p in _units(pb, prefix)} == before        # nothing moved for a refused add
    refused = [d for d in eng.decisions if d.get("action") == "exploration_refused:same_side"]
    assert len(refused) == 1
    add2 = P.add_id(eng, eng.exploration.arms["GS1"], first.client_order_id, 2)
    with eng.exploration.db.connection() as conn:
        row = conn.execute("SELECT taken, blocked_reason FROM exploration_obs WHERE candidate_id = ?",
                           (add2,)).fetchone()
    assert tuple(row) == (0, "same_side")                                   # a shadow fill for the read
    _tick(eng, pb, 25, e0 + 2 * r + 0.10)
    assert len([d for d in eng.decisions if d.get("action") == "exploration_refused:same_side"]) == 1   # not retried


def test_an_add_goes_through_the_riskgate(tmp_path) -> None:
    eng, pb = _start(tmp_path, [_pyr("GS1")])
    first = _first(eng, tmp_path)
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd.model_copy(update={"allowed": False, "reasons": ["daily_loss_limit"]})

    eng.gate.check = spy
    e0, r = _fill(first), first.stop_usd
    prefix = first.client_order_id[:7]
    stop0 = _units(pb, prefix)[0].sl
    _tick(eng, pb, 10, e0 + r + 0.05)
    assert len(seen) == 1 and seen[0][0].purpose == "exploration" and seen[0][0].arm_id == "GS1"
    assert len(_units(pb, prefix)) == 1 and _units(pb, prefix)[0].sl == stop0      # refused by the gate: no move
    assert any(d.get("action") == "exploration_refused:daily_loss_limit" for d in eng.decisions)


def test_an_arm_without_adds_never_pyramids(tmp_path) -> None:
    eng, pb = _start(tmp_path, [_pyr("GS1", adds=0)])
    first = _first(eng, tmp_path)
    _tick(eng, pb, 10, _fill(first) + 3.5 * first.stop_usd)
    assert len(_units(pb, first.client_order_id[:7])) == 1


# ------------------------------------------------------------------------------------------------ restart
@pytest.mark.parametrize("rows", [True, False], ids=["by-order-row", "by-comment"])
def test_after_a_restart_open_groups_are_adopted_and_the_ladder_resumes(tmp_path, rows: bool) -> None:
    arms = [_pyr("GS1")]
    eng, pb = _start(tmp_path, arms)
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    prefix = first.client_order_id[:7]
    _tick(eng, pb, 10, e0 + r + 0.05)
    assert len(_units(pb, prefix)) == 2
    eng._save_orders()
    if not rows:
        (tmp_path / "orders_icm-probe.json").unlink()
    (tmp_path / "open_trades_icm-probe.json").unlink(missing_ok=True)
    eng2, _ = _eng(tmp_path, arms, pb=pb)
    eng2.open.clear()
    eng2.bars_1m = eng.bars_1m.copy()
    eng2._last_tick = pb.last_tick("XAUUSD")
    eng2._reconcile(CLOSE + pd.Timedelta(seconds=12))
    assert len([t for t in eng2.open.values() if t.agent_id == "explore:GS1"]) == 2
    eng2._closed_through = CLOSE
    eng2.exploration.last_close = CLOSE
    _tick(eng2, pb, 15, e0 + 1.5 * r)
    assert len(_units(pb, prefix)) == 2                          # add 1 is not sent again
    _tick(eng2, pb, 20, e0 + 2 * r + 0.05)
    units = _units(pb, prefix)
    assert len(units) == 3, eng2.decisions[-6:]
    add2 = [x for x in X.trades(str(tmp_path)) if x.position_id == units[-1].position_id]
    assert add2 and add2[0].group_id == first.client_order_id
    assert _group_risk(units, 1) <= units[0].lots * r + 0.01 * 3 * units[0].lots + 1e-9


def test_after_a_restart_a_group_whose_stops_were_not_moved_is_brought_back_to_one_unit(tmp_path) -> None:
    """A crash between an add's fill and the stop move: the restarted engine moves the earlier stops on its first pass,
    whatever the entry state (exits are never gated)."""
    arms = [_pyr("GS1")]
    eng, pb = _start(tmp_path, arms)
    first = _first(eng, tmp_path)
    e0, r = _fill(first), first.stop_usd
    prefix = first.client_order_id[:7]
    _tick(eng, pb, 10, e0 + r + 0.05)
    u0 = _units(pb, prefix)[0]
    pb.modify(u0.position_id, round(e0 - r, 2), u0.tp)              # as if the move never reached the broker
    eng._save_orders()
    (tmp_path / "open_trades_icm-probe.json").unlink(missing_ok=True)
    eng2, _ = _eng(tmp_path, arms, pb=pb)
    eng2.open.clear()
    eng2.bars_1m = eng.bars_1m.copy()
    eng2._last_tick = pb.last_tick("XAUUSD")
    eng2._reconcile(CLOSE + pd.Timedelta(seconds=12))
    eng2.exploration.retired = frozenset({"GS1"})                   # no entry: the stop move still happens
    eng2._closed_through = CLOSE
    eng2.exploration.last_close = CLOSE
    units = _units(pb, prefix)
    assert _group_risk(units, 1) > units[0].lots * r * 1.5
    _tick(eng2, pb, 15, e0 + r + 0.05)
    units = _units(pb, prefix)
    assert len(units) == 2
    assert _group_risk(units, 1) <= units[0].lots * r + 0.02 * units[0].lots + 1e-9


# ------------------------------------------------------------------------------------------------ the account
def test_pyramid_adds_register_only_on_a_growth_sized_arm() -> None:
    base: dict[str, Any] = {"broker": "icm", "mode": "demo", "server": "S", "login": None, "terminal_path": "t",
                            "server_tz": "NY+7", "symbol": "XAUUSD", "magic_base": 1, "probe_only": True,
                            "enabled": True, "daily_loss_limit_abs": 220,
                            "exploration": {"enabled": True, "budget_share": 0.9,
                                            "pots": {"growth": {"gbp": 60, "max_open": 2}},
                                            "arms": {"GS1": {"style": "growth", "max_loss": 30.0, "adds": 3}}}}
    with pytest.raises(ValueError, match="adds"):
        acc_mod.Account.model_validate({"account_id": "icm-demo", **base})
    base["exploration"]["arms"]["GS1"]["sizing"] = "growth"
    acc = acc_mod.Account.model_validate({"account_id": "icm-demo", **base})
    assert acc.exploration.arms["GS1"].adds == 3


def test_icm_demo_registers_three_adds_on_gs1_and_on_its_control() -> None:
    import yaml
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "accounts.yaml").read_text())
    arms = raw["accounts"]["icm-demo"]["exploration"]["arms"]
    assert arms["GS1"]["adds"] == arms["GS1C"]["adds"] == 3


# ------------------------------------------------------------------------------------------------ shadow fills
def test_a_refused_growth_candidate_is_recorded_unquarantined_so_the_nightly_replay_can_score_it(tmp_path) -> None:
    """Quant requirement 5 ("a leg refused by a cap still records a shadow fill"): the replay (`label_candidates`)
    skips quarantined rows, and every growth-pot candidate was quarantined as an unknown style until 2026-11-02."""
    from goldbot.research.exploration_data import read_obs, record_candidate
    db = X.ledger_db(str(tmp_path))
    record_candidate(db, candidate_id="add2", account_id="icm-demo", broker="icm", arm_id="GS1",
                     family="session_range_break", style="growth", tf="15m", side=1,
                     decision_utc=pd.Timestamp("2026-11-02 09:00", tz="UTC"), stop_dist=5.0, target_dist=15.0,
                     max_hold_s=3600, taken=False, blocked_reason="same_side", branch="blocked", u=0.5, mu=0.0)
    with db.connection() as conn:
        assert conn.execute("SELECT quarantine FROM exploration_obs WHERE candidate_id = 'add2'").fetchone()[0] is None
    assert list(read_obs(db, broker="icm")["candidate_id"]) == ["add2"]


def test_the_one_off_repair_releases_only_growth_rows_quarantined_for_their_style_alone(tmp_path) -> None:
    import importlib.util
    import sys

    from goldbot.research.exploration_data import record_candidate
    path = Path(__file__).parents[1] / "scripts" / "unquarantine_growth_obs_2026_11_02.py"
    spec = importlib.util.spec_from_file_location("unq_growth", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["unq_growth"] = mod
    spec.loader.exec_module(mod)
    db = X.ledger_db(str(tmp_path))
    for cid, arm, style in (("g1", "GS1", "growth"), ("g2", "GS1C", "growth"), ("g3", "GS1", "growth"),
                            ("o1", "I0", "intraday")):
        record_candidate(db, candidate_id=cid, account_id="icm-demo", broker="icm", arm_id=arm, family="f",
                         style=style, tf="15m", side=1, decision_utc=pd.Timestamp("2026-11-02 09:00", tz="UTC"),
                         stop_dist=5.0, target_dist=15.0, max_hold_s=3600, taken=False, blocked_reason="pot",
                         branch="blocked", u=0.5, mu=0.0)
    with db.write() as conn:
        conn.execute("UPDATE exploration_obs SET quarantine = ? WHERE candidate_id IN ('g1', 'g2')", (mod.REASON,))
        conn.execute("UPDATE exploration_obs SET quarantine = ? WHERE candidate_id = 'g3'",
                     (mod.REASON + "; stop distance not a positive USD/oz",))
        conn.execute("UPDATE exploration_obs SET quarantine = 'other' WHERE candidate_id = 'o1'")
    assert mod.repair(db) == 2
    assert mod.repair(db) == 0                                       # idempotent
    with db.connection() as conn:
        rows = dict(conn.execute("SELECT candidate_id, quarantine FROM exploration_obs").fetchall())
    assert rows["g1"] is None and rows["g2"] is None
    assert rows["g3"] is not None and rows["o1"] == "other"

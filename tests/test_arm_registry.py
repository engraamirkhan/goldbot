"""Build 3 (owner directive 2026-11-18): the hot-loaded arm registry, engine/arm_registry.py.

Rules held here (docs/research/arm-lifecycle-2026-11-18.md section 3; trading-safety requirements 2026-11-18):
"demo and probe accounts only, refused at load for any account that is not demo"; "spec_rule arms only"; "every row
validated by the same load_arms checks"; "caps and RiskGate inherited"; "a bad row is rejected and journalled; it
never crashes the engine"; "a bad row refuses only its own arm"; "a hash-chain break ... refuses new entries on all
hot-loaded arms until the chain verifies, and raises an alert"; "on a DB lock or read error, keep the last good
snapshot for at most 3 cadences, then refuse new hot-loaded entries"; "after a restart, start with zero hot-loaded
arms until a full validated load"; exits never look up the registry row."""
from __future__ import annotations

import itertools
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import arm_registry as R
from goldbot.engine import exploration
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.research import arm_rules
from goldbot.research.arms import Arm as SpecArm
from goldbot.research.arms import arm_hash
from goldbot.risk.gate import LIVE_GATES, is_demo
from tests.test_exploration_engine import CLOSE, QUALIFIED, _arm, _at, _engine
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)
from tests.test_spec_arm_demo_only import spec_arm

ACCOUNT = "icm-probe"
HOT = "D2610-1"


def _hot_row(arm_id: str = HOT, **extra: Any) -> dict[str, Any]:
    """The frozen spec_rule row as autodisc-register writes it, qualified (not hashed) so it may enter."""
    a = spec_arm().model_copy(update={"id": arm_id})
    a = a.model_copy(update={"config_hash": arm_hash(a)})
    row = {**a.model_dump(include={"id", "rule", "timeframe", "params", "max_open", "config_hash"}),
           "forward_only": True, "status": "run", "qualification": QUALIFIED}
    return {**row, **extra}


def _register(tmp_path: Path, row: dict[str, Any] | None = None, *, max_loss: float = 5.0,
              account: str = ACCOUNT) -> None:
    R.append(str(tmp_path), account_id=account, row=row or _hot_row(), style="intraday", max_loss=max_loss,
             source="test")


@pytest.fixture
def spec_rule_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discovered arm's rule as a fixed long signal on the bar that just closed ($5 stop, 2 R, 4 bars)."""
    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], "side": [1], "risk_usd": [5.0], "target_r": [2.0],
                             "max_bars": [3]})
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "spec_rule", gen)


def _boundary(eng: Any, pb: Any, close: pd.Timestamp = CLOSE) -> Tick:
    tick = _at(eng, pb, close)
    exploration.on_pass(eng, tick)
    return tick


def _ex(eng: Any) -> exploration.Exploration:
    assert eng.exploration is not None
    return eng.exploration


def _actions(eng: Any, action: str) -> list[dict[str, Any]]:
    return [d for d in eng.decisions if d.get("action") == action]


def _brackets_kept(eng: Any, pb: Any, pid: int, sl_tp: list[tuple[Any, Any]]) -> None:
    """The position still at the broker with its own stop and target (none widened), or closed by one of them at the
    broker (its own persisted brackets: the engine looked nothing up)."""
    at_broker = [(p.sl, p.tp) for p in pb.positions() if p.position_id == pid]
    if at_broker:
        assert at_broker == sl_tp
        return
    [deal] = [d for d in pb._deals if d["position_id"] == pid and d["type"] != "entry"]
    assert deal["type"] in ("stop", "target"), deal
    sl, tp = sl_tp[0]
    assert deal["price"] <= sl + 1e-9 or abs(deal["price"] - tp) < 1e-9


def _trade_mode(pb: Any, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    base: Any = type(pb).__mro__[1]

    def account(self: Any = pb) -> AccountInfo:
        return base.account(pb).model_copy(update={"trade_mode": mode})
    monkeypatch.setattr(pb, "account", account)


# ---------------------------------------------------------------- the demo predicate
@pytest.mark.parametrize("mode,gates,terminal,probe_only",
                         list(itertools.product(["demo", "live"], [[], [next(iter(LIVE_GATES))]], ["demo", "real"],
                                                [True, False])))
def test_the_demo_predicate_passes_only_when_all_four_facts_say_demo(mode, gates, terminal, probe_only):
    want = mode == "demo" and gates == [] and terminal == "demo" and probe_only
    assert is_demo(mode, gates, terminal, probe_only) is want


@pytest.mark.parametrize("missing", range(4))
def test_the_demo_predicate_fails_closed_on_any_unknown_fact(missing):
    facts: list[Any] = ["demo", [], "demo", True]
    facts[missing] = None
    assert is_demo(*facts) is False


# ---------------------------------------------------------------- the hot load
def test_a_registered_row_is_loaded_at_the_next_pass_boundary_with_its_cap_in_the_gate_and_no_restart(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    ex = eng.exploration
    assert ex is not None and HOT not in ex.arms and ex.registry_ok
    _register(tmp_path)
    _boundary(eng, pb)
    assert HOT in ex.arms and HOT in ex.hot_known and HOT not in ex.retired
    assert eng.gate.limits.exploration_arms[HOT] == 5.0                  # the per-trade cap reaches the RiskGate
    [row] = _actions(eng, R.LOADED)
    assert row["arm"] == HOT and row["max_loss"] == 5.0


def test_a_hot_loaded_arm_enters_through_the_riskgate_at_the_minimum_lot(tmp_path, spec_rule_signals):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    R.refresh(eng, _ex(eng), None)
    _boundary(eng, pb)
    mine = [t for t in eng.open.values() if t.agent_id == exploration.PREFIX + HOT]
    assert len(mine) == 1 and mine[0].lots == 0.01


def test_a_non_demo_terminal_is_refused_at_load_and_nothing_is_hot_loaded(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    _trade_mode(pb, monkeypatch, "real")
    _boundary(eng, pb)
    ex = eng.exploration
    assert ex is not None and HOT not in ex.arms and not ex.registry_ok
    assert len(_actions(eng, R.NOT_DEMO)) == 1
    assert HOT not in eng.gate.limits.exploration_arms


def test_a_row_for_another_account_is_never_loaded_here(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path, account="vantage-demo")
    _boundary(eng, pb)
    assert eng.exploration is not None and HOT not in eng.exploration.arms


FORBIDDEN_ROWS: dict[str, dict[str, Any]] = {
    "growth sizing": {"sizing": "growth"},
    "a lot above the minimum": {"unit_lots": 0.05},
    "pyramid adds": {"adds": 2},
    "an owner experiment": {"role": "owner_experiment"},
    "the cost-measurement purpose": {"purpose": "cost_measurement"},
    "the learning-probe purpose": {"learning_probe": True},
    "a bracket-widening param": {"params": {**spec_arm().params, "max_stop_usd": 40.0}},
    "an exit param": {"params": {**spec_arm().params, "exit": "runner"}},
    "another rule": {"rule": "random_bracket"},
    "a bad hash": {"config_hash": "0" * 16},
    "more than one open": {"max_open": 3},
}


@pytest.mark.parametrize("what", sorted(FORBIDDEN_ROWS))
def test_a_row_with_a_forbidden_or_invalid_field_is_refused_and_journalled_while_the_good_row_loads(tmp_path, what):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    _register(tmp_path, _hot_row("D2610-9", **FORBIDDEN_ROWS[what]))
    _boundary(eng, pb)
    ex = eng.exploration
    assert ex is not None and HOT in ex.arms and "D2610-9" not in ex.arms and ex.registry_ok
    [row] = [d for d in _actions(eng, R.REFUSED) if d["arm"] == "D2610-9"]
    assert row["why"]
    _boundary(eng, pb, CLOSE + pd.Timedelta(minutes=15))                  # journalled once, not every read
    assert len([d for d in _actions(eng, R.REFUSED) if d["arm"] == "D2610-9"]) == 1


def test_a_per_trade_cap_above_the_style_ceiling_is_refused(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("peer")])          # ceiling min(40, 0.3 x £48, 0.9 x £220) = 14.4
    _register(tmp_path, max_loss=15.0)
    _boundary(eng, pb)
    assert eng.exploration is not None and HOT not in eng.exploration.arms
    [row] = _actions(eng, R.REFUSED)
    assert "outside the intraday caps" in row["why"]


# ---------------------------------------------------------------- integrity and failure modes
def _corrupt(tmp_path: Path, sql: str) -> None:
    with Database.open(str(tmp_path), "aux").write() as conn:
        conn.execute(sql)


@pytest.mark.parametrize("damage", ["truncated", "altered cap", "removed row"])
def test_a_broken_chain_refuses_every_hot_loaded_entry_and_alerts_until_it_verifies(tmp_path, damage, spec_rule_signals):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    _register(tmp_path, _hot_row("D2610-2"))
    _boundary(eng, pb)
    ex = eng.exploration
    assert ex is not None and {HOT, "D2610-2"} <= ex.hot_known and ex.registry_ok
    _corrupt(tmp_path, {"truncated": "UPDATE arm_registry SET row = substr(row, 1, 20) WHERE seq = 2",
                        "altered cap": "UPDATE arm_registry SET max_loss = 14 WHERE seq = 2",
                        "removed row": "DELETE FROM arm_registry WHERE seq = 1"}[damage])
    eng.open.clear()
    _boundary(eng, pb, CLOSE + pd.Timedelta(minutes=15))
    assert not ex.registry_ok and len(_actions(eng, R.CHAIN)) == 1
    assert R.entry_refusal(eng, ex, HOT) == R.UNVERIFIED and R.entry_refusal(eng, ex, "peer") is None
    assert [d for d in eng.decisions if d.get("action") == "exploration_refused:" + R.UNVERIFIED]
    assert not [t for t in eng.open.values() if t.agent_id in (exploration.PREFIX + HOT, exploration.PREFIX + "D2610-2")]


def test_a_locked_database_keeps_the_last_good_load_for_three_reads_then_refuses_hot_entries(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    ex = eng.exploration
    assert ex is not None
    R.refresh(eng, ex, None)
    assert ex.registry_ok and HOT in ex.arms

    def locked(state_dir: str) -> list[R.RegistryRow]:
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(R, "read_rows", locked)
    for i in range(R.STALE_CADENCES):
        R.refresh(eng, ex, CLOSE + pd.Timedelta(minutes=i))
        assert ex.registry_ok, i                                         # the last good load stands
    R.refresh(eng, ex, CLOSE + pd.Timedelta(minutes=R.STALE_CADENCES))
    assert not ex.registry_ok and R.entry_refusal(eng, ex, HOT) == R.UNVERIFIED
    assert len(_actions(eng, R.STALE)) == 1
    assert HOT in ex.arms                                                # still known: exits and adoption keep it
    monkeypatch.undo()
    R.refresh(eng, ex, CLOSE + pd.Timedelta(minutes=10))
    assert ex.registry_ok


def test_a_restart_mid_load_applies_nothing_and_no_hot_arm_enters_until_a_full_validated_load(tmp_path, monkeypatch):
    _register(tmp_path)
    _register(tmp_path, _hot_row("D2610-2"))
    real = R.refusal
    seen: list[str] = []

    def dies_on_the_second(eng: Any, ex: Any, r: R.RegistryRow) -> Any:
        seen.append(r.arm_id)
        if len(seen) == 2:
            raise RuntimeError("process killed mid-load")
        return real(eng, ex, r)
    monkeypatch.setattr(R, "refusal", dies_on_the_second)
    eng, pb = _engine(tmp_path, [_arm("peer")])
    ex = eng.exploration
    assert ex is not None and not ex.registry_ok and ex.hot_known == frozenset()   # zero hot-loaded arms
    assert len(_actions(eng, R.UNREAD)) == 1
    monkeypatch.setattr(R, "refusal", real)
    _boundary(eng, pb)
    assert ex.registry_ok and {HOT, "D2610-2"} <= ex.hot_known


def test_after_a_restart_the_arm_set_is_the_same(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    _boundary(eng, pb)
    before = (set(eng.exploration.arms), dict(eng.exploration.hot_caps))  # type: ignore[union-attr]
    eng2, _ = _engine(tmp_path, [_arm("peer")], pb=pb)
    ex2 = eng2.exploration
    assert ex2 is not None and ex2.registry_ok
    assert (set(ex2.arms), dict(ex2.hot_caps)) == before
    assert eng2.gate.limits.exploration_arms[HOT] == 5.0


def test_a_hot_load_does_not_disturb_open_positions(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _boundary(eng, pb)
    assert eng.open
    before = {pid: (t.agent_id, t.sl, t.tp, t.lots) for pid, t in eng.open.items()}
    broker = [(p.position_id, p.sl, p.tp, p.lots) for p in pb.positions()]
    _register(tmp_path)
    R.refresh(eng, _ex(eng), None)
    assert HOT in eng.exploration.arms  # type: ignore[union-attr]
    assert {pid: (t.agent_id, t.sl, t.tp, t.lots) for pid, t in eng.open.items()} == before
    assert [(p.position_id, p.sl, p.tp, p.lots) for p in pb.positions()] == broker


# ---------------------------------------------------------------- a flip to live, retire, exits
def test_an_account_flipping_to_live_refuses_the_next_signal_at_the_engine_and_the_gate_and_exits_still_fire(
        tmp_path, monkeypatch, spec_rule_signals):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    R.refresh(eng, _ex(eng), None)
    _boundary(eng, pb)
    [pid] = [k for k, t in eng.open.items() if t.agent_id == exploration.PREFIX + HOT]
    sl_tp = [(p.sl, p.tp) for p in pb.positions() if p.position_id == pid]
    _trade_mode(pb, monkeypatch, "real")
    nxt = CLOSE + pd.Timedelta(minutes=15)
    _boundary(eng, pb, nxt)
    refused = [d for d in eng.decisions if d.get("agent") == exploration.PREFIX + HOT
               and str(d.get("action", "")).startswith("exploration_refused:")]
    assert refused and refused[-1]["action"] in ("exploration_refused:not_demo",
                                                 "exploration_refused:" + R.UNVERIFIED)
    assert len([d for d in eng.decisions if d.get("agent") == exploration.PREFIX + HOT
                and d.get("action") == "exploration:executed:auto"]) == 1                    # no second entry
    from goldbot.risk.gate import _exploration_reasons
    intent = exploration._intent(eng, eng.exploration.arms[HOT], 1, 2400.0,  # type: ignore[union-attr]
                                 exploration.Pending(arm_id=HOT, bar_ts=nxt, side=1, stop_usd=5.0, target_usd=10.0,
                                                     hold_bars=4), nxt, eng._sizing_terms(1, 2400.0, 5.0), 5.0)
    assert intent is not None and "exploration_terminal_not_demo" in _exploration_reasons(intent, eng.gate.limits)
    _brackets_kept(eng, pb, pid, sl_tp)                                  # no stop widened
    if pid in eng.open:
        eng._try_close(pid, "kill_switch", nxt)                         # the kill switch's flatten still closes it
    assert pid not in {p.position_id for p in pb.positions()}


def test_a_retired_hot_arm_with_an_open_position_takes_no_entry_and_keeps_its_brackets(tmp_path, spec_rule_signals):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _register(tmp_path)
    R.refresh(eng, _ex(eng), None)
    _boundary(eng, pb)
    [pid] = [k for k, t in eng.open.items() if t.agent_id == exploration.PREFIX + HOT]
    sl_tp = [(p.sl, p.tp) for p in pb.positions() if p.position_id == pid]
    with Database.open(str(tmp_path), "aux").write() as conn:
        conn.execute("INSERT INTO exploration_arm_status (broker, arm_id, status, day, rule, recorded_ns) "
                     "VALUES ('icm', ?, 'retired', '2026-11-18', 'arm lifecycle: RETIRE', 0)", (HOT,))
    nxt = CLOSE + pd.Timedelta(minutes=15)
    _boundary(eng, pb, nxt)
    assert HOT in eng.exploration.retired  # type: ignore[union-attr]
    assert len([t for t in eng.open.values() if t.agent_id == exploration.PREFIX + HOT]) <= 1   # no new entry
    assert not [d for d in eng.decisions if d.get("agent") == exploration.PREFIX + HOT
                and d.get("action") == "exploration:executed:auto" and d["ts"] > CLOSE.isoformat()[:10] + "T20:10"]
    _brackets_kept(eng, pb, pid, sl_tp)
    assert exploration.agent_for(eng, exploration.PREFIX + HOT) is not None   # exits keep the arm's barrier


def test_no_position_is_closed_as_an_orphan_while_no_registry_load_has_succeeded(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("peer")])
    _boundary(eng, pb)
    [pid] = list(eng.open)
    ex = eng.exploration
    assert ex is not None
    ex.arms.pop("peer")                                                  # as an arm the start could not load
    ex.registry_settled = False
    closed: list[str] = []
    monkeypatch.setattr(eng, "_try_close", lambda p, why, now: closed.append(why))
    exploration.exits(eng, CLOSE)
    assert closed == []
    ex.registry_settled = True
    exploration.exits(eng, CLOSE)
    assert closed == [exploration.ORPHAN_EXIT]
    assert pid in eng.open


def test_append_refuses_on_a_broken_chain_and_a_duplicate_registration(tmp_path):
    _register(tmp_path)
    with pytest.raises(ValueError, match="registered on"):
        _register(tmp_path)
    _corrupt(tmp_path, "UPDATE arm_registry SET style = 'swing' WHERE seq = 1")
    with pytest.raises(ValueError, match="chain broken"):
        _register(tmp_path, _hot_row("D2610-2"))
    assert R.chain_break(R.read_rows(str(tmp_path))) is not None
    assert json.loads(R.read_rows(str(tmp_path))[0].row)["id"] == HOT


# ---------------------------------------------------------------- forward-qualified arms never reach real money
def _forward_qualified(tmp_path: Path) -> Path:
    q = {**QUALIFIED, "confirm": "forward:" + "a" * 12}
    a = SpecArm(id="FQ1", rule="random_bracket", timeframe="15m", max_open=1, qualification=q,
                params={"minutes": [0], "window_utc": ["07:00", "19:50"], "stop_usd": 3.0, "target_r": 1.0,
                        "hold_bars": 4})
    a = a.model_copy(update={"config_hash": arm_hash(a)})
    p = tmp_path / "fq.yaml"
    p.write_text("arms:\n  - " + a.model_dump_json(exclude={"entry_refused"}, exclude_defaults=True) + "\n")
    return p


def test_a_forward_qualified_arm_is_refused_on_any_account_that_is_not_demo(tmp_path):
    from goldbot.research.arms import load_arms, qualification_refusal
    [live] = load_arms(_forward_qualified(tmp_path), check_hash=False, live_class=True)
    assert "never on an account that is not demo" in live.entry_refused
    assert "never on an account that is not demo" in str(qualification_refusal(live, live_class=True))
    [demo] = load_arms(_forward_qualified(tmp_path), check_hash=False)
    assert "never on an account that is not demo" not in demo.entry_refused


def test_a_forward_qualified_arm_in_a_real_accounts_config_places_no_order(tmp_path):
    """A live engine never builds an exploration block (refused at start), so no arm of any kind can send there."""
    from goldbot.engine.exploration import Exploration
    eng, _ = _engine(tmp_path, [_arm("peer")])
    eng.cfg = eng.cfg.model_copy(update={"mode": "live"})
    with pytest.raises(ValueError, match="demo probe_only accounts only"):
        Exploration.start(eng)

"""Engine-level entry refusals (docs/research/prereg-amendment-2026-11-07-kill-only.md, goldbot/engine/entry_refusals.py):
the gold 02:00-07:00 UTC block and the F-1 pit window refuse entries only, before any room, slot or cap check, fail
closed, and are shadowed through the untaken-candidate path; exits, stops, targets, holds and the Friday cut of a
position opened before 02:00 are untouched; the hook is called from the entry branch of `exploration._send` only."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.timeutil import epoch_ns
from goldbot.engine import entry_refusals, exploration
from goldbot.engine.entry_refusals import ERROR_PREFIX, F1_PIT, WINDOW_0207, EntryRefusal
from goldbot.engine.exploration import Arm, Pending, Pot
from goldbot.execution.broker import Tick
from goldbot.research.arms import Arm as SpecArm
from tests.test_exploration_engine import LONG, TICKS, _actions, _arm, _at, _engine, _sent
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

ROOT = Path(__file__).parents[1]
TUE = pd.Timestamp("2025-03-04", tz="UTC")                  # a Tuesday (US winter time: New York = UTC-5)
SUMMER = pd.Timestamp("2025-07-01", tz="UTC")               # a Tuesday in US summer time (New York = UTC-4)


def _at_utc(day: pd.Timestamp, hms: str) -> pd.Timestamp:
    return day + pd.Timedelta(hms if hms.count(":") == 2 else hms + ":00")


def _sig(arm: Arm, ts: pd.Timestamp) -> Pending:
    return Pending(arm_id=arm.arm_id, side=1, bar_ts=ts, stop_usd=5.0, target_usd=10.0, hold_bars=4)


def _spec_arm(arm_id: str, *, role: str = "strategy", instrument: str | None = None, cost_read: str | None = None) -> Arm:
    params: dict[str, Any] = {"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4}
    if instrument is not None:
        params["instrument"] = instrument
    spec = SpecArm(id=arm_id, rule=LONG, timeframe="15m", max_open=5, params=params, role=role,  # type: ignore[arg-type]
                   cost_read=cost_read or "")
    return Arm(spec=spec, style="intraday", control_share=0.05 if role == "control" else None)


def _cfg_engine(weekend: bool = False) -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(cfg=SimpleNamespace(weekend_window=object() if weekend else None))


# ------------------------------------------------------------------------------------------------ the windows
@pytest.mark.parametrize("hms, refused", [("01:59:59", False), ("02:00:00", True), ("06:59:59", True),
                                          ("07:00:00", False), ("04:30:00", True), ("21:00:00", False)])
def test_window_0207_refuses_a_gold_signal_by_decision_time_from_0200_inclusive_to_0700_exclusive(hms, refused):
    arm = _spec_arm("A")
    assert entry_refusals.window_0207(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, hms))) is refused


@pytest.mark.parametrize("arm_id, role", [("S0", "control"), ("ZCD", "control"), ("LX", "learning"),
                                          ("GS1", "strategy")])
def test_window_0207_covers_every_gold_role_but_cost_and_their_controls_alike(arm_id, role):
    arm = _spec_arm(arm_id, role=role)
    assert entry_refusals.window_0207(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "03:00")))


@pytest.mark.parametrize("arm_id, role", [("F1A", "cost"), ("G1A", "cost"), ("F1C", "control"), ("G1C", "control")])
def test_cost_arms_and_their_dedicated_controls_are_exempt_from_window_0207(arm_id, role):
    """Amendment section 2: F1A/G1A (role cost) and F1C/G1C feed the O-11/O-12 cost reads; G1A's own window lies in
    the blocked hours (amendment 10.4: "G1A signal at 03:00 UTC sent")."""
    arm = _spec_arm(arm_id, role=role, cost_read="O-12" if role == "cost" else None)
    assert not entry_refusals.window_0207(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "03:00")))


def test_a_gold_cross_is_not_gold_for_window_0207():
    arm = _spec_arm("S0-XAUEUR", role="control", instrument="XAUEUR")
    assert not entry_refusals.window_0207(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "03:00")))


def test_the_weekend_lane_is_outside_the_amendments_three_accounts():
    arm = _spec_arm("WKS0", role="control")
    assert not entry_refusals.window_0207(_cfg_engine(weekend=True), arm, _sig(arm, _at_utc(TUE, "03:00")))


@pytest.mark.parametrize("day, hms, refused", [
    (TUE, "12:59:00", True),        # 07:59 New York: before the pit
    (TUE, "13:00:00", False),       # 08:00 New York: inside
    (TUE, "18:29:59", False),       # 13:29:59 New York
    (TUE, "18:30:00", True),        # 13:30 New York: outside (end exclusive)
    (TUE, "03:00:00", True),
    (SUMMER, "12:00:00", False),    # 08:00 New York in summer time: the window moves with US DST
    (SUMMER, "17:30:00", True),     # 13:30 New York in summer time
])
@pytest.mark.parametrize("arm_id, role", [("F1A", "cost"), ("F1C", "control")])
def test_f1_pit_refuses_f1a_and_its_control_outside_0800_to_1330_new_york(day, hms, refused, arm_id, role):
    arm = _spec_arm(arm_id, role=role, cost_read="O-11" if role == "cost" else None)
    assert entry_refusals.f1_pit(_cfg_engine(), arm, _sig(arm, _at_utc(day, hms))) is refused


def test_f1_pit_touches_no_other_arm():
    arm = _spec_arm("G1A", role="cost", cost_read="O-12")
    assert not entry_refusals.f1_pit(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "03:00")))


def test_the_f1_pit_window_is_the_one_the_f1_rows_record():
    """F1A and F1C record `ny_pit` [08:00, 13:30, America/New_York] in their frozen params: the refusal is that window."""
    import yaml
    rows = {r["id"]: r for r in yaml.safe_load((ROOT / "config" / "exploration_arms.yaml").read_text())["arms"]}
    lo, hi = entry_refusals.F1_PIT_WINDOW
    for arm_id in entry_refusals.F1_PIT_ARMS:
        assert rows[arm_id]["params"]["record"]["ny_pit"] == [lo.strftime("%H:%M"), hi.strftime("%H:%M"),
                                                              str(entry_refusals.F1_PIT_TZ)]


# ------------------------------------------------------------------------------------------------ in the engine
def _spy_gate(eng: Any) -> list[Any]:
    """Every intent the RiskGate is asked about."""
    seen: list[Any] = []
    check = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        seen.append(a)
        return check(*a, **k)

    eng.gate.check = spy
    return seen


def _obs(tmp_path: Path) -> list[dict[str, Any]]:
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        conn.row_factory = __import__("sqlite3").Row
        return [dict(r) for r in conn.execute("SELECT * FROM exploration_obs ORDER BY arm_id, decision_ns")]


@pytest.mark.parametrize("close, sent", [("01:45", True), ("02:00", False), ("06:45", False), ("07:00", True)])
def test_the_engine_sends_or_refuses_a_gold_signal_by_its_decision_close(tmp_path, close, sent):
    eng, pb = _engine(tmp_path, [_arm("a")])
    exploration.on_pass(eng, _at(eng, pb, _at_utc(TUE, close)))
    assert bool(_sent(eng)) is sent, eng.decisions
    assert bool(_actions(eng, "exploration_refused:window_0207")) is not sent


def test_a_refused_signal_is_shadowed_with_its_fields_and_its_quote_and_the_riskgate_never_sees_it(tmp_path):
    """Amendment 3.2/10.2: `exploration_obs` taken 0, blocked_reason window_0207, side, stop, target, hold, decision
    time and is_control; the journal row carries the tick's bid and ask. Controls are refused by the same path."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("c", control_share=0.05)])
    seen = _spy_gate(eng)
    close = _at_utc(TUE, "02:00")
    tick = _at(eng, pb, close)
    exploration.on_pass(eng, tick)
    assert not seen and not pb.positions() and not _sent(eng)
    rows = _actions(eng, "exploration_refused:")
    assert {(d["agent"], d["action"]) for d in rows} == {("explore:a", "exploration_refused:window_0207"),
                                                         ("explore:c", "exploration_refused:window_0207")}
    assert all(d["bid"] == tick.bid and d["ask"] == tick.ask for d in rows)
    a, c = _obs(tmp_path)
    for row, side, control in ((a, 1, 0), (c, 1, 1)):
        assert row["taken"] == 0 and row["blocked_reason"] == WINDOW_0207 and row["branch"] == "blocked"
        assert row["side"] == side and row["is_control"] == control and row["mu"] == 0.0
        assert row["stop_dist"] == 5.0 and row["target_dist"] == 10.0 and row["max_hold_s"] == 4 * 900
        assert row["decision_ns"] == int(epoch_ns(pd.DatetimeIndex([close]))[0])


def test_a_refusal_that_raises_sends_no_order_and_journals_refusal_error_with_its_reason(tmp_path, monkeypatch):
    def boom(*_: Any) -> bool:
        raise ZeroDivisionError("bad")

    monkeypatch.setattr(entry_refusals, "REFUSALS", (EntryRefusal(name="boom", applies=boom),))
    eng, pb = _engine(tmp_path, [_arm("a")])
    seen = _spy_gate(eng)
    exploration.on_pass(eng, _at(eng, pb, _at_utc(TUE, "20:00")))
    assert not seen and not pb.positions() and not _sent(eng)
    [row] = _actions(eng, "exploration_refused:")
    assert row["action"] == "exploration_refused:" + ERROR_PREFIX + "boom" and row["error"] == "ZeroDivisionError"
    [obs] = _obs(tmp_path)
    assert obs["blocked_reason"] == "refusal_error:boom" and obs["taken"] == 0


def test_the_first_refusal_that_applies_names_it_and_a_later_one_is_not_consulted():
    calls: list[str] = []

    def mark(name: str, hit: bool) -> EntryRefusal:
        def applies(*_: Any) -> bool:
            calls.append(name)
            return hit
        return EntryRefusal(name=name, applies=applies)

    arm = _spec_arm("A")
    got = entry_refusals.first_refusal(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "12:00")),
                                       (mark("x", False), mark("y", True), mark("z", True)))
    assert got is not None and got.reason == "y" and got.error is None and calls == ["x", "y"]
    assert entry_refusals.first_refusal(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "12:00")), ()) is None


def test_the_shipped_refusals_start_with_window_0207_then_f1_pit():
    """The rest (WS-6, `refusals_novel`) are pinned in tests/test_refusals_novel.py."""
    assert [r.name for r in entry_refusals.REFUSALS][:2] == [WINDOW_0207, F1_PIT]


# ------------------------------------------------------------------------------------------------ exits untouched
def _forced(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every refusal forced on: one always refuses, one raises; returns the log of their calls."""
    calls: list[str] = []

    def always(*_: Any) -> bool:
        calls.append("always")
        return True

    def raises(*_: Any) -> bool:
        calls.append("raises")
        raise RuntimeError("forced")

    monkeypatch.setattr(entry_refusals, "REFUSALS", (EntryRefusal(name="always", applies=always),
                                                     EntryRefusal(name="raises", applies=raises)))
    return calls


@pytest.mark.parametrize("exit_kind", ["stop", "target"])
def test_a_position_opened_at_0145_keeps_its_server_stop_and_target_with_every_refusal_forced_on(
        tmp_path, monkeypatch, exit_kind):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=1)])
    opened = _at_utc(TUE, "01:45")
    exploration.on_pass(eng, _at(eng, pb, opened))
    [pid] = list(eng.open)
    calls = _forced(monkeypatch)
    tr = eng.open[pid]
    assert tr.sl is not None and tr.tp is not None
    eng._last_reconcile = opened
    at = _at_utc(TUE, "02:00:40")                           # inside the block
    px = tr.sl - 1.0 if exit_kind == "stop" else tr.tp + 1.0
    eng.on_tick(Tick(ts_utc=at, bid=px, ask=px + 0.2))
    assert not pb.positions() and pid not in eng.open
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == exit_kind and not calls


def test_a_1m_position_opened_at_0141_keeps_its_hold_exit_with_every_refusal_forced_on(tmp_path, monkeypatch):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=30, stop_usd=20.0, target_r=1.0)   # no broker exit meanwhile
    eng, pb = _engine(tmp_path, [arm])
    close = _at_utc(TUE, "01:40")
    minute = close + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, close)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    assert len(eng.open) == 1, eng.decisions
    [pid] = list(eng.open)
    calls = _forced(monkeypatch)
    opened = eng.open[pid].opened_utc
    assert opened is not None
    eng._retry_closes(opened + pd.Timedelta(minutes=30))     # 02:11: the hold ends inside the block
    eng._flush_closed()
    assert pid not in eng.open and not pb.positions() and not calls
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == exploration.HOLD_EXIT


def test_a_losing_position_opened_at_0145_friday_is_closed_by_the_friday_cut_with_every_refusal_forced_on(
        tmp_path, monkeypatch):
    fri = pd.Timestamp("2025-03-07", tz="UTC")
    eng, pb = _engine(tmp_path, [_arm("a", max_open=1, hold_bars=60)],
                      pots={"intraday": Pot(gbp=48, max_open=5)}, caps={"a": 40.0})
    exploration.on_pass(eng, _at(eng, pb, _at_utc(fri, "01:45")))
    assert len(eng.open) == 1, eng.decisions
    [pid] = list(eng.open)
    calls = _forced(monkeypatch)
    entry = eng.open[pid].open_price
    assert entry is not None
    cut = Tick(ts_utc=_at_utc(fri, "19:31"), bid=entry - 1.0, ask=entry - 0.8)
    pb.on_tick(cut)
    assert eng._weekend_rule(cut)
    eng._flush_closed()
    assert not pb.positions() and pid not in eng.open and not calls
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == "weekend_close_loser"


# ------------------------------------------------------------------------------------------------ the call-site pin
def _calls_in(tree: ast.AST, attr: str) -> list[tuple[str, int]]:
    """(enclosing function, line) of every reference to `attr` (a Name or an Attribute) in `tree`."""
    out: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
            if (isinstance(child, ast.Attribute) and child.attr == attr) or (isinstance(child, ast.Name)
                                                                            and child.id == attr):
                out.append((scope, child.lineno))
            visit(child, inner)

    visit(tree, "<module>")
    return out


def test_the_hook_is_called_only_from_the_entry_branch_of_send_and_never_from_an_exit_path():
    """Trading-safety: `first_refusal` and the module are referenced in goldbot only by `exploration._send` (never
    `exits`, `_pyramid`, `cost_probe._place`, the Friday rule or any other module)."""
    hits: list[tuple[str, str]] = []
    for path in sorted((ROOT / "goldbot").rglob("*.py")):
        if path.name == "entry_refusals.py":
            continue
        tree = ast.parse(path.read_text())
        hits += [(path.name, fn) for fn, _ in _calls_in(tree, "first_refusal")]
        hits += [(path.name, fn) for fn, _ in _calls_in(tree, "REFUSALS")]
    assert hits == [("exploration.py", "_send")]


def test_in_send_the_refusals_run_before_the_order_core_and_the_riskgate_check_is_the_only_call_before_the_order():
    """The refusal precedes `cost_probe._place`; in `_place` the gate's check (`books.check`, RiskGate.check on the
    account's state) precedes `Engine._execute`, the only order send; the hook module never reaches an order or a
    close (no `_execute`, `_place`, `order_send`, broker or close call)."""
    ex = ast.parse((ROOT / "goldbot" / "engine" / "exploration.py").read_text())
    send = [n for n in ast.walk(ex) if isinstance(n, ast.FunctionDef) and n.name == "_send"][0]
    [(_, refusal)] = _calls_in(send, "first_refusal")
    places = [ln for _, ln in _calls_in(send, "_place")]
    assert places and refusal < min(places)
    cp = ast.parse((ROOT / "goldbot" / "engine" / "cost_probe.py").read_text())
    place = [n for n in ast.walk(cp) if isinstance(n, ast.FunctionDef) and n.name == "_place"][0]
    checks = [ln for _, ln in _calls_in(place, "check")]
    executes = [ln for _, ln in _calls_in(place, "_execute")]
    assert len(checks) == 1 and len(executes) == 1 and checks[0] < executes[0]
    hook = ast.parse((ROOT / "goldbot" / "engine" / "entry_refusals.py").read_text())
    for name in ("_execute", "_place", "order_send", "broker", "_try_close", "check", "send"):
        assert not _calls_in(hook, name), name

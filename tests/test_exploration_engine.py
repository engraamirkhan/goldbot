"""Exploration arms in the engine (plan 2026-10-25, lane L2; ADR 0043 budget): every arm signal is sent at the minimum
lot with its own stop, hold and target through the RiskGate (purpose `exploration`), inside the engine's caps (global,
budget share, pots, arm budget, arm and pot slots, per-trade cap with the round trip, same side, weekend hold, -8%
pause); exits are never gated; nothing the engine decides on is written."""
from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, cost_probe, exploration, magic
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.risk import RiskLimits
from goldbot.risk.gate import Stage
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.telegram.approvals import ApprovalCenter

TICKS = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
CLOSE = pd.Timestamp("2025-03-04 20:00", tz="UTC")        # a Tuesday 15m close in market hours
LOSS = 1.0                                                 # every loss at a stop, and every round trip, in the tests


class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


def _long(m: pd.DataFrame, p: Any) -> int:
    return 1


def _short(m: pd.DataFrame, p: Any) -> int:
    return -1


@pytest.fixture(autouse=True)
def _families(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two built-in test arms' rules, and a fixed loss at every stop (the budget arithmetic is then exact)."""
    monkeypatch.setitem(exploration.FAMILIES, "test_long", _long)
    monkeypatch.setitem(exploration.FAMILIES, "test_short", _short)
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)


def _arm(arm_id: str, tf: str = "15m", *, family: str = "test_long", style: str = "intraday", **kw: Any) -> Arm:
    base: dict[str, Any] = dict(arm_id=arm_id, family=family, tf=tf, stop=1.0, hold_bars=4, target=2.0, max_open=5,
                                daily_gbp=100.0, style=style)
    return Arm(**{**base, **kw})


def _engine(tmp_path: Path, arms: list[Arm], *, pots: dict[str, Pot] | None = None, max_open_total: int = 18,
            budget_share: float = 0.9, caps: dict[str, float] | None = None, agents: list[Any] | None = None,
            pb: PaperBroker | None = None, enabled: bool = True) -> tuple[Engine, PaperBroker]:
    pots = pots if pots is not None else {"scalp": Pot(gbp=50, max_open=8), "intraday": Pot(gbp=48, max_open=5),
                                          "swing": Pot(gbp=40, max_open=1)}
    cfg = ExplorationConfig(enabled=enabled, budget_share=budget_share, max_open_total=max_open_total, pots=pots,
                            arms=tuple(arms))
    pb = pb if pb is not None else _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=50, probe_only=True,
                        exploration_arms=caps if caps is not None else {a.arm_id: 40.0 for a in arms},
                        daily_loss_limit_abs=220.0)
    agents = agents if agents is not None else []
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg),
                 pb, agents, {a.family: ConstantModel(p=0.9) for a in agents}, ApprovalCenter({111}), limits=limits,
                 live_shares={a.agent_id: 1.0 for a in agents})
    return eng, pb


def _at(eng: Engine, pb: PaperBroker, close: pd.Timestamp, lag_s: float = 2.0) -> Tick:
    """The engine as it is just after `close`: its 1m bars before it, the close processed, a tick `lag_s` later."""
    ticks = TICKS[TICKS["ts_utc"] < close]
    eng.bars_1m = ticks_to_1m(ticks)
    px = float(ticks["bid"].iloc[-1])
    tick = Tick(ts_utc=close + pd.Timedelta(seconds=lag_s), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    eng._last_tick = tick
    eng._refresh_account(tick)
    eng._closed_through = close
    return tick


def _actions(eng: Engine, prefix: str) -> list[dict]:
    return [d for d in eng.decisions if str(d.get("action", "")).startswith(prefix)]


def _sent(eng: Engine) -> list[dict]:
    return _actions(eng, "exploration:")


# ------------------------------------------------------------------------------------------------ the order
def test_every_signal_is_sent_at_the_minimum_lot_with_its_own_stop_hold_and_target_through_the_riskgate(tmp_path):
    arm = _arm("i-long", stop=1.0, target=2.0, hold_bars=4)
    eng, pb = _engine(tmp_path, [arm])
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy                                     # type: ignore[method-assign]
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    [order] = _sent(eng)
    assert order["action"] == "exploration:executed:auto", eng.decisions
    [(intent, gd)] = seen
    assert intent.purpose == "exploration" and intent.arm_id == "i-long" and gd.allowed
    assert intent.volume_max == intent.volume_min == 0.01 and gd.lots == pytest.approx(0.01)
    [pos] = pb.positions()
    [tr] = eng.open.values()
    assert pos.magic == eng.cfg.magic_base + magic.EXPLORATION_OFFSET and pos.lots == pytest.approx(0.01)
    atr_usd = intent.atr_usd
    assert tr.sl == pytest.approx(tick.ask - 1.0 * atr_usd, abs=0.01)       # its own stop: 1 ATR
    assert tr.tp == pytest.approx(tick.ask + 2.0 * atr_usd, abs=0.01)       # its own target: 2 ATR
    assert tr.max_bars == 4 and tr.agent_id == "explore:i-long"              # its own hold: 4 of its 15m bars
    [rec] = exploration.trades(str(tmp_path), "icm-probe")
    assert rec.position_id == pos.position_id and rec.hold_s == 3600 and rec.arm_id == "i-long"


def test_order_ids_fit_the_mt5_comment_and_carry_the_arm_key():
    eng_cfg = EngineConfig(account_id="icm-probe-with-a-long-name", broker_name="icm")

    class _E:
        cfg = eng_cfg

    arm = _arm("an-arm-with-a-very-long-identifier-0001")
    oid = exploration.order_id(_E(), arm, CLOSE)                                       # type: ignore[arg-type]
    assert len(oid) <= 29 and oid.startswith("ex" + arm.key + "-")
    assert oid.split("-")[1] == f"{int(CLOSE.timestamp()):010d}"


def test_the_exploration_magic_is_reserved_and_no_family_lands_on_any_reserved_offset():
    from goldbot.specialists import SPECIALISTS
    assert len(set(magic.RESERVED)) == len(magic.RESERVED) and magic.EXPLORATION_OFFSET == 95
    clash = {f for f in SPECIALISTS if zlib.crc32(f.encode()) % 100 in magic.RESERVED}
    assert not clash, f"families whose magic is reserved: {clash}"


def test_exploration_runs_only_on_a_demo_probe_only_account(tmp_path):
    cfg = ExplorationConfig(enabled=True, pots={"intraday": Pot(gbp=1, max_open=1)}, arms=(_arm("a"),))
    with pytest.raises(ValueError, match="probe_only"):
        Engine(EngineConfig(account_id="x", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                            exploration_config=cfg), _Terminal(), [], {}, ApprovalCenter({1}))


# ------------------------------------------------------------------------------------------------ the caps
def test_the_arm_budget_admits_exactly_what_fits_and_refuses_the_next(tmp_path):
    """Its day: today's realised loss + open risk + the new loss at the stop <= its share of the pot, never above
    its own ceiling (daily_gbp 2.0 with a loss of 1.0 a trade: exactly two)."""
    eng, pb = _engine(tmp_path, [_arm("a", daily_gbp=2.0)])
    for k in range(3):
        close = CLOSE + pd.Timedelta(minutes=15 * k)
        exploration.on_pass(eng, _at(eng, pb, close))
    assert len(_sent(eng)) == 2 and len(eng.open) == 2
    [refused] = _actions(eng, "exploration_refused:")
    assert refused["action"] == "exploration_refused:arm_budget" and "propensity" in refused


def test_an_exhausted_pot_refuses_the_next_entry_whatever_the_arms_shares_say(tmp_path, monkeypatch):
    """The arms' shares fit the pot by construction; the pot is the backstop when they do not (shares that moved
    during the day, a reader that over-allocates): realised + open + new <= the pot's £."""
    arms = [_arm("a", daily_gbp=10), _arm("b", daily_gbp=10, family="test_short")]
    eng, pb = _engine(tmp_path, arms, pots={"intraday": Pot(gbp=1.0, max_open=5)})
    monkeypatch.setattr(exploration, "shares", lambda ex, now: {"a": 1.0, "b": 1.0})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:pot"]


def test_the_budget_share_of_the_day_binds_across_every_pot(tmp_path):
    arms = [_arm("a"), _arm("b", family="test_short", style="scalp")]
    eng, pb = _engine(tmp_path, arms, budget_share=1.5 / 200)          # £1.50 of the £200 day: one trade of £1
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:budget"]


def test_one_swing_position_at_a_time(tmp_path):
    arms = [_arm("w0", style="swing"), _arm("w1", style="swing", family="test_short")]
    eng, pb = _engine(tmp_path, arms)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:pot_slots"]


def test_the_per_trade_cap_counts_the_round_trip_cost_beside_the_loss_at_the_stop(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")], caps={"a": 1.5})          # loss 1.0 + cost 1.0 > 1.5
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not _sent(eng) and not pb.positions()
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_cap"]


def test_the_global_cap_holds_the_rest_queued_until_they_go_stale(tmp_path):
    arms = [_arm(f"a{i}", style="intraday") for i in range(3)]
    eng, pb = _engine(tmp_path, arms, max_open_total=2)
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    assert len(_sent(eng)) == 2 and len(pb.positions()) == 2
    assert eng.exploration is not None and len(eng.exploration.queue) == 1
    later = Tick(ts_utc=CLOSE + pd.Timedelta(seconds=61), bid=tick.bid, ask=tick.ask)
    exploration.on_pass(eng, later)
    assert [d["action"] for d in _actions(eng, "exploration_stale")] == ["exploration_stale"]
    assert len(pb.positions()) == 2 and not eng.exploration.queue


def test_the_live_cap_from_equity_binds_below_max_open_total(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")], max_open_total=18)
    _at(eng, pb, CLOSE)
    eng.state.equity = 1_000.0                                           # 6 x £1,000 / ~£2,900 a lot -> 2, x 0.9 -> 1
    assert eng.exploration is not None
    terms = eng._sizing_terms(1, 2900.0, 5.0)
    assert exploration.global_cap(eng, eng.exploration, 2900.0, terms) == 1


def test_at_most_four_entries_a_pass_in_fair_rotation_across_arms(tmp_path):
    arms = [_arm(f"a{i}", max_open=1) for i in range(6)]
    eng, pb = _engine(tmp_path, arms, pots={"intraday": Pot(gbp=48, max_open=10)})
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    first = [d["arm"] for d in _sent(eng)]
    assert first == ["a0", "a1", "a2", "a3"]
    exploration.on_pass(eng, Tick(ts_utc=tick.ts_utc + pd.Timedelta(seconds=1), bid=tick.bid, ask=tick.ask))
    assert [d["arm"] for d in _sent(eng)][4:] == ["a4", "a5"]


def test_the_fair_order_serves_each_arm_once_a_round_oldest_first():
    ex = object.__new__(exploration.Exploration)
    p = exploration.Pending
    ex.queue = __import__("collections").deque([p(arm_id="b", side=1, bar_ts=CLOSE, atr_usd=1.0),
                                                p(arm_id="b", side=1, bar_ts=CLOSE + pd.Timedelta(minutes=1), atr_usd=1.0),
                                                p(arm_id="a", side=1, bar_ts=CLOSE, atr_usd=1.0),
                                                p(arm_id="c", side=1, bar_ts=CLOSE + pd.Timedelta(minutes=1), atr_usd=1.0)])
    ex.last_arm = "a"
    assert [s.arm_id for s in exploration._order(ex)] == ["b", "a", "c", "b"]


def test_no_scalp_or_intraday_entry_whose_hold_reaches_the_friday_cut(tmp_path):
    fri = pd.Timestamp("2025-03-07 19:15", tz="UTC")                    # 21:15 Athens; the cut is 21:30
    eng, _ = _engine(tmp_path, [_arm("a")])
    assert exploration.crosses_friday(eng, fri, 3600)
    assert not exploration.crosses_friday(eng, fri, 600)
    assert not exploration.crosses_friday(eng, CLOSE, 3600)


def test_at_minus_eight_percent_every_arm_pauses_and_minus_six_warns(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    eng.state.balance_closed_hwm = eng.state.equity / 0.94                # -6%: warning, entries go on
    exploration.on_pass(eng, tick)
    assert len(_actions(eng, "exploration_dd_warning")) == 1 and len(_sent(eng)) == 1
    tick = _at(eng, pb, CLOSE + pd.Timedelta(minutes=15))
    eng.state.balance_closed_hwm = eng.state.equity / 0.92                # -8%: paused
    exploration.on_pass(eng, tick)
    assert len(_sent(eng)) == 1 and len(_actions(eng, "exploration_paused_dd")) == 1


# ------------------------------------------------------------------------------------------------ the 1m arms
def test_a_1m_arm_reads_only_complete_minutes_and_never_writes_the_engines_bars_or_ticks(tmp_path):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [arm])
    minute = CLOSE + pd.Timedelta(minutes=3)
    tick = _at(eng, pb, CLOSE)
    part = TICKS[(TICKS["ts_utc"] >= CLOSE) & (TICKS["ts_utc"] < minute + pd.Timedelta(seconds=30))]
    eng.ticks = [Tick(ts_utc=ts, bid=float(b), ask=float(a)) for ts, b, a in zip(part["ts_utc"], part["bid"], part["ask"])]
    bars, ticks, through = eng.bars_1m.copy(), list(eng.ticks), eng._closed_through
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=1), bid=tick.bid, ask=tick.ask))
    assert not _sent(eng)                                   # the minute closed 1 s ago: not complete yet (1.5 s)
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    assert len(_sent(eng)) == 1
    [rec] = exploration.trades(str(tmp_path))
    assert rec.signal_utc == minute
    pd.testing.assert_frame_equal(eng.bars_1m, bars)
    assert eng.ticks == ticks and eng._closed_through == through


def test_a_1m_hold_exits_on_the_host_clock_while_halted_and_without_a_quote(tmp_path):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [arm])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)
    eng.state.stage = Stage.HALTED
    eng.cfg.approval_mode = "propose"
    opened = eng.open[pid].opened_utc
    assert opened is not None
    eng._retry_closes(opened + pd.Timedelta(seconds=299))
    assert pid in eng.open
    eng._quote_invalid_since = opened
    eng.clock = lambda: opened + pd.Timedelta(seconds=300)
    eng.cfg.live_clock = True
    eng.no_quote_pass()
    assert pid not in eng.open and not pb.positions()
    eng._flush_closed()
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == exploration.HOLD_EXIT and rec.censored is False
    assert load_closed_trades(tmp_path)[0] == []                        # never in closed_trades.jsonl


# ------------------------------------------------------------------------------------------------ decisions, restart, failures
def test_the_live_agents_15m_decisions_are_identical_with_exploration_on(tmp_path):
    class _Long(SessionOpenSpecialist):
        def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
            return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})

    agent = _Long().agent_id

    def run(path: Path, on: bool) -> list[tuple[Any, ...]]:
        path.mkdir()
        eng, pb = _engine(path, [_arm("a"), _arm("b", tf="1h", family="test_short")], agents=[_Long()], enabled=on)
        for k in range(4):
            close = CLOSE + pd.Timedelta(minutes=15 * k)
            tick = _at(eng, pb, close)
            eng.on_bar_close(close, tick)
            exploration.on_pass(eng, tick)
        sent.append(len(_sent(eng)))
        return [(d.get("agent"), d.get("action"), d.get("p")) for d in eng.decisions if d.get("agent") == agent]

    sent: list[int] = []
    off, on = run(tmp_path / "off", False), run(tmp_path / "on", True)
    assert off and off == on and sent[0] == 0 and sent[1] >= 4      # the arms traded beside the agent


def test_after_a_restart_an_arm_position_is_adopted_by_its_comment_and_its_hold_resumes(tmp_path):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [arm])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)
    (tmp_path / "orders_icm-probe.json").unlink()
    (tmp_path / "open_trades_icm-probe.json").unlink(missing_ok=True)
    eng2, _ = _engine(tmp_path, [arm], pb=pb)
    eng2.open.clear()
    eng2._reconcile(tick.ts_utc)
    assert eng2.open[pid].agent_id == "explore:s"
    opened = eng2.open[pid].opened_utc
    assert opened is not None
    eng2._retry_closes(opened + pd.Timedelta(seconds=300))
    assert pid not in eng2.open


def test_a_position_of_an_arm_no_longer_configured_is_closed_at_once(tmp_path):
    arm = _arm("gone")
    eng, pb = _engine(tmp_path, [arm])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng2, _ = _engine(tmp_path, [_arm("other")], pb=pb)
    eng2.open.clear()
    eng2._reconcile(CLOSE)
    eng2._retry_closes(CLOSE)
    assert pid not in eng2.open and not pb.positions()


def test_an_unreadable_ledger_refuses_every_entry_and_exits_continue(tmp_path, monkeypatch):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=1, stop=8.0, target=8.0)   # no broker exit meanwhile
    eng, pb = _engine(tmp_path, [arm, _arm("i")])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)

    def broken(*a: Any, **k: Any) -> Any:
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(exploration, "realised", broken)
    _at(eng, pb, CLOSE + pd.Timedelta(minutes=15))
    exploration.on_pass(eng, Tick(ts_utc=CLOSE + pd.Timedelta(minutes=15, seconds=2), bid=tick.bid, ask=tick.ask))
    assert _actions(eng, "exploration_refused:ledger_unreadable") and len(_sent(eng)) == 1
    eng._retry_closes(CLOSE + pd.Timedelta(minutes=15, seconds=3))      # the 1m hold (60 s) is long past
    assert pid not in eng.open


@pytest.mark.parametrize("reason,censored", [("weekend_close_loser", True), ("kill_switch_close", True),
                                             (exploration.HOLD_EXIT, False), ("time_exit", False)])
def test_a_close_by_anything_but_the_arms_own_barrier_is_censored(tmp_path, reason, censored):
    eng, pb = _engine(tmp_path, [_arm("a")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng._try_close(pid, reason, CLOSE)
    eng._flush_closed()
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == reason and rec.censored is censored


def test_a_closed_15m_arm_trade_reaches_the_research_observations(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng._try_close(pid, "time_exit", CLOSE)
    eng._flush_closed()
    [rec] = exploration.trades(str(tmp_path))
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM exploration_obs").fetchone()[0]
    assert rec.exit_utc is not None and n == 1


# ------------------------------------------------------------------------------------------------ config and limits
def _account(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="a", broker="icm", mode="demo", server="s", login=None, terminal_path="t",
                                server_tz="UTC", symbol="XAUUSD", magic_base=1, enabled=True)
    return acc_mod.Account(**{**base, **kw})


def _explore(**kw: Any) -> acc_mod.Exploration:
    base: dict[str, Any] = dict(enabled=True, budget_share=0.1, max_open_total=1,
                                arms={"s0": acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.25)},
                                pots={"scalp": acc_mod.ExplorationPot(gbp=20, max_open=8)})
    return acc_mod.Exploration(**{**base, **kw})


def _probe_account(**kw: Any) -> acc_mod.Account:
    return _account(probe_only=True, shadow_host=True, cost_probe=acc_mod.CostProbe(enabled=True, budget_share=0.9,
                                                                                     max_loss=4.0),
                    daily_loss_limit_abs=220.0, **kw)


def test_engine_limits_carry_the_exploration_fence_only_for_a_demo_probe_only_account():
    from goldbot.config import load_settings
    from goldbot.ops.services.engine import engine_limits
    s = load_settings()
    on = engine_limits(s, _probe_account(exploration=_explore()))
    assert on.probe_only and on.exploration_arms == {"s0": 6.5}
    paper = engine_limits(s, _probe_account(exploration=_explore()), paper=True)
    assert not paper.probe_only and paper.exploration_arms == {}
    plain = engine_limits(s, _account())
    assert not plain.probe_only and plain.exploration_arms == {}
    live = engine_limits(s, _account(mode="live", login=1))
    assert not live.probe_only and live.exploration_arms == {}


def test_every_enabled_arm_needs_a_pot_and_the_pots_fit_the_budget_share():
    with pytest.raises(ValueError, match="pot"):
        _probe_account(exploration=_explore(pots={}))
    with pytest.raises(ValueError, match="pots must fit"):
        _probe_account(exploration=_explore(pots={"scalp": acc_mod.ExplorationPot(gbp=30, max_open=8)}))


def test_the_engine_config_refuses_an_arm_whose_style_has_no_pot():
    with pytest.raises(ValueError, match="no pot"):
        ExplorationConfig(pots={}, arms=(_arm("a"),))

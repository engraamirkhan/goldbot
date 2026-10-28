"""Exploration arms in the engine (plan 2026-10-25, lane L2; ADR 0043 budget): every arm signal is sent at the minimum
lot with its own stop, hold and target through the RiskGate (purpose `exploration`), inside the engine's caps (global,
budget share, pots, arm budget, arm and pot slots, per-trade cap with the round trip, same side, weekend hold, -8%
pause); exits are never gated; nothing the engine decides on is written."""
from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, cost_probe, exploration, magic
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.research import arm_rules
from goldbot.research.arms import Arm as SpecArm
from goldbot.risk import RiskLimits
from goldbot.risk.gate import Stage
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.telegram.approvals import ApprovalCenter

TICKS = synthetic_ticks("2025-03-03", "2025-03-08", ticks_per_minute=1, seed=5)
CLOSE = pd.Timestamp("2025-03-04 20:00", tz="UTC")        # a Tuesday 15m close in market hours
LOSS = 1.0                                                 # every loss at a stop, and every round trip, in the tests
LONG, SHORT = "random_bracket", "tsmom"                    # spec rule names whose generators the tests replace


class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


def _rule(side: int) -> Any:
    """A candidate on the bar that just closed: the arm's own stop (params stop_usd), target (target_r R) and hold."""
    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        p = arm.params
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], "side": [side], "risk_usd": [float(p["stop_usd"])],
                             "target_r": [float(p["target_r"])], "max_bars": [int(p["hold_bars"]) - 1]})
    return gen


@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two built-in test arms' rules (always long, always short), and a fixed loss at every stop (the budget
    arithmetic is then exact)."""
    monkeypatch.setitem(arm_rules.RULE_FUNCS, LONG, _rule(1))
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)


def _arm(arm_id: str, tf: str = "15m", *, side: int = 1, style: str = "intraday", max_open: int = 5,
         hold_bars: int = 4, stop_usd: float = 5.0, target_r: float = 2.0, control_share: float | None = None,
         status: Literal["run", "blocked"] = "run") -> Arm:
    spec = SpecArm(id=arm_id, rule=LONG if side > 0 else SHORT, timeframe=tf, max_open=max_open,  # type: ignore[arg-type]
                   params={"stop_usd": stop_usd, "target_r": target_r, "hold_bars": hold_bars}, status=status,
                   note="test" if status != "run" else "")
    return Arm(spec=spec, style=style, control_share=control_share)


def _engine(tmp_path: Path, arms: list[Arm], *, pots: dict[str, Pot] | None = None, max_open_total: int = 18,
            budget_share: float = 0.9, caps: dict[str, float] | None = None, agents: list[Any] | None = None,
            pb: PaperBroker | None = None, enabled: bool = True, max_positions: int = 50,
            scheduled: bool = False) -> tuple[Engine, PaperBroker]:
    pots = pots if pots is not None else {"scalp": Pot(gbp=50, max_open=8), "intraday": Pot(gbp=48, max_open=5),
                                          "swing": Pot(gbp=40, max_open=1)}
    cfg = ExplorationConfig(enabled=enabled, budget_share=budget_share, max_open_total=max_open_total, pots=pots,
                            arms=tuple(arms))
    pb = pb if pb is not None else _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=max_positions, probe_only=True,
                        exploration_arms=caps if caps is not None else {a.arm_id: 40.0 for a in arms},
                        daily_loss_limit_abs=220.0)
    agents = agents if agents is not None else []
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg, cost_probe=scheduled,
                              cost_probe_scheduled=acc_mod.ScheduledProbe(enabled=scheduled)),
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
    arm = _arm("i-long", stop_usd=5.0, target_r=2.0, hold_bars=4)
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
    assert tr.sl == pytest.approx(tick.ask - 5.0, abs=0.01)                 # its own stop: $5
    assert tr.tp == pytest.approx(tick.ask + 10.0, abs=0.01)                # its own target: 2 R
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
    its share (a control's 0.5 of a £4 pot = £2 with a loss of £1 a trade: exactly two)."""
    eng, pb = _engine(tmp_path, [_arm("a", control_share=0.5)], pots={"intraday": Pot(gbp=4.0, max_open=5)})
    for k in range(3):
        close = CLOSE + pd.Timedelta(minutes=15 * k)
        exploration.on_pass(eng, _at(eng, pb, close))
    assert len(_sent(eng)) == 2 and len(eng.open) == 2
    [refused] = _actions(eng, "exploration_refused:")
    assert refused["action"] == "exploration_refused:arm_budget" and "propensity" in refused


def test_an_arm_whose_share_is_below_one_trade_still_takes_one_trade_a_day(tmp_path):
    """Live 2026-10-26: share x pot (W1A: 0.20 x £40 = £8) was below one trade's loss at the stop (≈ £29), so the
    arm was refused on every bar. Its day is at least its per-trade cap: one trade, then refused (the pot, the host
    budget and the RiskGate still bind)."""
    eng, pb = _engine(tmp_path, [_arm("a", control_share=0.01)], pots={"intraday": Pot(gbp=48.0, max_open=5)},
                      caps={"a": 17.0})                    # share 0.01 x £48 = £0.48: below one trade
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_sent(eng)) == 1
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:arm_budget"}


@pytest.mark.parametrize("pnl", [0.0, 3.0])
def test_the_first_trade_rule_admits_one_trade_a_day_even_when_that_trade_closed_flat_or_in_profit(tmp_path, pnl):
    """The first-trade rule (2026-10-26) is the arm's FIRST trade of the day: a trade closed today, at a flat or a
    winning P&L, has spent it. Netted to no loss it used to make the arm fresh again, so it traded at its per-trade
    cap on every bar while its share of the pot (£0.48 here) was never the bound."""
    eng, pb = _engine(tmp_path, [_arm("a", control_share=0.01)], pots={"intraday": Pot(gbp=48.0, max_open=5)},
                      caps={"a": 17.0})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng._try_close(pid, "time_exit", CLOSE)
    eng._flush_closed()
    [rec] = exploration.trades(str(tmp_path))
    with exploration.ledger_db(str(tmp_path)).write() as conn:
        exploration.LEDGER.upsert(conn, rec.model_copy(update={"pnl": pnl}))
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))
    assert len(_sent(eng)) == 1 and not eng.open
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_budget"]


@pytest.mark.parametrize(("cap", "pot", "sent"), [
    (6.0, 80.0, 3),      # floor 3 x (£6 cap + £1 round trip) = £21 under 0.30 x £80 = £24: losses of £7 at 0, 7, 14
    (17.0, 50.0, 3),     # floor 3 x (£17 + £1) = £54, capped at 0.30 x £50 = £15: losses at 0, 7, 14 (uncapped: six)
])
def test_a_strategy_arms_day_is_three_per_trade_caps_with_the_round_trip_capped_at_the_share_cap_of_the_pot(
        tmp_path, cap, pot, sent):
    """Live 2026-10-27: 86 `arm_budget` refusals (69 on icm-probe): 17 strategy arms in the 55% of the scalp pot the
    controls leave had 3.24% of £138 = £4.47, so one $3 loss spent a day. ADR 0057 (with the quant's C4): a strategy
    arm's day is at least ARM_ROOM_LOSSES x (its per-trade cap + the round trip), never above SHARE_CAP x the pot (so a
    wide-stop arm cannot take a disproportionate slice); its share here is 0.10 x 0.30 x the pot, far below."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b", side=-1), _arm("c", control_share=0.9)],
                      pots={"intraday": Pot(gbp=pot, max_open=5)}, caps={"a": cap, "b": 0.5, "c": 0.5})
    assert exploration.ARM_ROOM_LOSSES == 3 and exploration.SHARE_CAP == 0.30
    for k in range(6):
        close = CLOSE - pd.Timedelta(hours=1) + pd.Timedelta(minutes=15 * k)      # 19:00-20:15, before 20:45 UTC
        exploration.on_pass(eng, _at(eng, pb, close))
        for pid in [p for p, t in eng.open.items() if t.agent_id == "explore:a"]:
            eng._try_close(pid, "time_exit", close)
            eng._flush_closed()
            [rec] = [r for r in exploration.trades(str(tmp_path)) if r.position_id == pid]
            with exploration.ledger_db(str(tmp_path)).write() as conn:
                exploration.LEDGER.upsert(conn, rec.model_copy(update={"pnl": -7.0}))   # a loss at about its cap
    assert [d["arm"] for d in _sent(eng)] == ["a"] * sent
    assert {d["action"] for d in _actions(eng, "exploration_refused:") if d["arm"] == "a"} == \
        {"exploration_refused:arm_budget"}


def test_the_engine_journals_the_room_floor_it_runs_so_a_read_can_flag_fills_before_the_switch(tmp_path):
    """Quant C3 (prereg amendment (d) 10.2): the room floor's switch time is the first `exploration_room_floor` row a
    host journals; fills sent before it are pre-floor."""
    eng, _ = _engine(tmp_path, [_arm("a")])
    [row] = _actions(eng, "exploration_room_floor")
    assert (row["losses"], row["share_cap"]) == (3, 0.30)


def test_a_controls_day_stays_its_fixed_share_with_no_three_loss_floor(tmp_path):
    """Controls are the null at a fixed share (ADR 0043: "never allocated by results"); the three-loss floor is for
    strategy arms only, so a control keeps its share plus the first-trade rule (0.01 x £48: one trade a day)."""
    eng, pb = _engine(tmp_path, [_arm("a", control_share=0.01)], pots={"intraday": Pot(gbp=48.0, max_open=5)},
                      caps={"a": 17.0})
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_sent(eng)) == 1


def _stopped_at_the_broker(eng: Engine, pb: PaperBroker) -> int:
    """The arm's one position hits its stop at the broker; the 30 s reconcile's `sweep` queues its record (not yet
    written: the tick's records are flushed after `on_pass`)."""
    [pid] = list(eng.open)
    sl = eng.open[pid].sl
    assert sl is not None
    pb.on_tick(Tick(ts_utc=CLOSE + pd.Timedelta(seconds=40), bid=sl - 1.0, ask=sl - 0.8))
    exploration.sweep(eng, {p.position_id for p in pb.positions()})
    assert pid not in eng.open and [c.position_id for c in eng._closes] == [pid]
    return pid


def _fresh_arm(tmp_path: Path, **kw: Any) -> tuple[Engine, PaperBroker]:
    """An arm whose share (£0.48) is below one trade: only the first-trade rule lets it trade."""
    return _engine(tmp_path, [_arm("a", control_share=0.01)], pots={"intraday": Pot(gbp=48.0, max_open=5)},
                   caps={"a": 17.0}, **kw)


def test_a_close_swept_in_the_same_tick_spends_the_arms_first_trade_before_its_record_is_written(tmp_path):
    """Trading-safety HIGH (2026-10-26): the reconcile's sweep queues the close and the record is written after
    `on_pass`, so the signal in that tick saw the arm flat with nothing in the ledger and fresh again (PR #186)."""
    eng, pb = _fresh_arm(tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    _stopped_at_the_broker(eng, pb)
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))
    assert len(_sent(eng)) == 1 and not eng.open
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_budget"]


def test_a_close_queued_before_on_pass_on_the_bar_close_path_counts_its_loss_against_the_budget(tmp_path):
    """The bar close's exits (`_manage_open`) queue their records before `on_pass` too. The queued close counts at
    its loss at the stop plus its cost (£2 here) until its record lands: with £1 open, a day of £2.50 has no room."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b", side=-1, style="scalp")], budget_share=2.5 / 200)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 2
    [pid] = [p for p, t in eng.open.items() if t.agent_id == "explore:b"]
    eng._try_close(pid, "time_exit", CLOSE)
    assert [c.position_id for c in eng._closes] == [pid]
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))
    assert len(_sent(eng)) == 2
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:budget"}


def test_a_record_that_failed_to_write_or_was_lost_keeps_counting_after_a_restart(tmp_path, monkeypatch):
    """A record write that fails is retried for `closed_record_retries` attempts and then reported lost; the close
    counts (at its loss at the stop plus its cost) the whole time, and after a restart from its id alone."""
    eng, pb = _fresh_arm(tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    pid = _stopped_at_the_broker(eng, pb)

    def fail(*a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(eng, "_record_closed", fail)
    eng._flush_closed()
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))          # retrying: still counted
    for _ in range(eng.cfg.closed_record_retries):
        for c in eng._closes:
            c.next_try = None
        eng._flush_closed()
    assert eng._records_lost == [pid] and not eng._closes
    now = CLOSE + pd.Timedelta(minutes=30)
    assert eng.exploration is not None
    assert exploration._Pass(eng, eng.exploration, now).lost == {"a": pytest.approx(2 * LOSS)}   # lost: counted
    eng._save_orders()
    eng2, _ = _fresh_arm(tmp_path, pb=pb)
    assert eng2._records_lost == [pid] and not eng2._throttle_lost
    exploration.on_pass(eng2, _at(eng2, pb, now))                                    # restarted: from its id
    assert len(_sent(eng)) == 1 and not _sent(eng2) and not eng.open and not eng2.open
    refused = _actions(eng, "exploration_refused:") + _actions(eng2, "exploration_refused:")
    assert [d["action"] for d in refused] == ["exploration_refused:arm_budget"] * 2


def _lost(eng: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every queued record fails to write until it is given up as lost."""
    def fail(*a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(eng, "_record_closed", fail)
    for _ in range(eng.cfg.closed_record_retries):
        for c in eng._closes:
            c.next_try = None
        eng._flush_closed()
    assert not eng._closes


def test_a_lost_record_of_a_strategy_or_probe_trade_never_blocks_exploration(tmp_path):
    """Trading-safety MEDIUM (2026-10-26): `_records_lost` lists every trade's lost record. An id with no exploration
    ledger row is not exploration's to count; it used to make the pass raise, stopping exploration for good."""
    eng, pb = _fresh_arm(tmp_path)
    eng._records_lost = [987654]
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1 and not _actions(eng, "exploration_refused:")
    assert not _actions(eng, "exploration_blocked_lost_record")


def test_a_lost_exploration_close_counts_on_its_own_day_only_and_keeps_the_arm_spent_after_it(tmp_path, monkeypatch):
    """Its loss counts on the day it closed (sent + hold_s on or after the day's start when only its id survives); on
    later days the arm stays a key at 0 (no daily re-charge of the budgets, and not fresh: its share binds until the
    record is repaired). A lost exploration record that holds an arm is journalled once a day."""
    eng, pb = _fresh_arm(tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [rec] = exploration.trades(str(tmp_path))
    with exploration.ledger_db(str(tmp_path)).write() as conn:       # sent on the host clock: the tick's, as live
        exploration.LEDGER.upsert(conn, rec.model_copy(update={"sent_utc": CLOSE + pd.Timedelta(seconds=2)}))
    pid = _stopped_at_the_broker(eng, pb)
    _lost(eng, monkeypatch)
    assert eng.exploration is not None
    wed = CLOSE + pd.Timedelta(days=1)
    eng._last_reset = None                                  # the day's start is the risk day's reset, else midnight
    assert exploration._Pass(eng, eng.exploration, CLOSE).lost == {"a": pytest.approx(2 * LOSS)}
    assert exploration._Pass(eng, eng.exploration, wed).lost == {"a": 0.0}          # this process: its close time
    eng._save_orders()
    eng2, _ = _fresh_arm(tmp_path, pb=pb)
    assert eng2.exploration is not None
    eng2._last_reset = None
    assert exploration._Pass(eng2, eng2.exploration, CLOSE).lost == {"a": pytest.approx(2 * LOSS)}   # by its id
    ps = exploration._Pass(eng2, eng2.exploration, wed)
    assert ps.lost == {"a": 0.0} and ps.lost_in(eng2.exploration) == 0.0
    for k in range(2):
        exploration.on_pass(eng2, _at(eng2, pb, wed + pd.Timedelta(minutes=15 * k)))
    assert not _sent(eng2)
    assert [d["action"] for d in _actions(eng2, "exploration_refused:")] == ["exploration_refused:arm_budget"] * 2
    blocked = _actions(eng2, "exploration_blocked_lost_record")                     # Tuesday's and Wednesday's
    assert [(d["position"], d["arm"], d["ts"][:10]) for d in blocked] == [(pid, "a", "2025-03-04"),
                                                                          (pid, "a", "2025-03-05")]


def test_a_queued_close_is_counted_once_when_its_record_is_already_in_the_ledger(tmp_path):
    """No double count: a close whose record is in the ledger counts from the ledger only, queued or not."""
    eng, pb = _fresh_arm(tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    pid = _stopped_at_the_broker(eng, pb)
    now = CLOSE + pd.Timedelta(minutes=15)
    _at(eng, pb, now)
    assert eng.exploration is not None
    queued = exploration._Pass(eng, eng.exploration, now).lost
    [c] = eng._closes
    exploration.record_close(eng, pid, c.trade, c.reason, c.price, c.at)              # written, still queued
    eng._recorded.discard(pid)
    written = exploration._Pass(eng, eng.exploration, now).lost
    eng._flush_closed()
    flushed = exploration._Pass(eng, eng.exploration, now).lost
    ledger = exploration.realised(eng, eng.exploration, now.floor("D"))
    assert queued == {"a": pytest.approx(2 * LOSS)}                                   # stop + cost, before the record
    assert written == flushed == ledger and set(ledger) == {"a"}


def test_an_exhausted_pot_refuses_the_next_entry_whatever_the_arms_shares_say(tmp_path, monkeypatch):
    """The arms' shares fit the pot by construction; the pot is the backstop when they do not (shares that moved
    during the day, a reader that over-allocates): realised + open + new <= the pot's £."""
    arms = [_arm("a"), _arm("b", side=-1)]
    eng, pb = _engine(tmp_path, arms, pots={"intraday": Pot(gbp=1.0, max_open=5)})
    monkeypatch.setattr(exploration, "shares", lambda ex, now: {"a": 1.0, "b": 1.0})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:pot"]


def test_the_budget_share_of_the_day_binds_across_every_pot(tmp_path):
    arms = [_arm("a"), _arm("b", side=-1, style="scalp")]
    eng, pb = _engine(tmp_path, arms, budget_share=1.5 / 200)          # £1.50 of the £200 day: one trade of £1
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:budget"]


def test_one_swing_position_at_a_time(tmp_path):
    arms = [_arm("w0", style="swing"), _arm("w1", style="swing", side=-1)]
    eng, pb = _engine(tmp_path, arms)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:pot_slots"]


def test_the_per_trade_cap_counts_the_round_trip_cost_beside_the_loss_at_the_stop(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")], caps={"a": 1.5})          # loss 1.0 + cost 1.0 > 1.5
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not _sent(eng) and not pb.positions()
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_cap"]


def test_the_live_cap_from_equity_binds_below_max_open_total(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")], max_open_total=18)
    _at(eng, pb, CLOSE)
    eng.state.equity = 1_000.0                                           # 6 x £1,000 / ~£2,900 a lot -> 2, x 0.9 -> 1
    assert eng.exploration is not None
    terms = eng._sizing_terms(1, 2900.0, 5.0)
    assert exploration.global_cap(eng, eng.exploration, 2900.0, terms) == 1


@pytest.mark.parametrize("scheduled,sent", [(True, 2), (False, 3)])
def test_one_host_slot_is_kept_for_the_scheduled_cost_probe_whatever_else_is_open(tmp_path, scheduled, sent):
    """ADR 0043: "one of 18 kept for the scheduled probe". max_open_total counts exploration positions only, so the
    mirror probes (and any other position) could fill the host's max_positions and starve the scheduled probe. With
    scheduled probes on, an arm is refused (`probe_slot`) when its entry would leave no host slot free; off, the
    host cap is unchanged (never raised, never lowered)."""
    arms = [_arm("a"), _arm("b", side=-1), _arm("c")]
    eng, pb = _engine(tmp_path, arms, max_positions=3, scheduled=scheduled)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == sent and len(eng.open) == sent
    refused = [d["action"] for d in _actions(eng, "exploration_refused:")]
    assert refused == (["exploration_refused:probe_slot"] if scheduled else [])
    [blocked] = [r for r in _obs(tmp_path) if r[1] == 0] if scheduled else [None]
    assert blocked is None or blocked[5] == "probe_slot"


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
    kw: dict[str, Any] = dict(side=1, stop_usd=1.0, target_usd=2.0, hold_bars=4)
    later = CLOSE + pd.Timedelta(minutes=1)
    ex.queue = __import__("collections").deque([p(arm_id="b", bar_ts=CLOSE, **kw), p(arm_id="b", bar_ts=later, **kw),
                                                p(arm_id="a", bar_ts=CLOSE, **kw), p(arm_id="c", bar_ts=later, **kw)])
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
        eng, pb = _engine(path, [_arm("a"), _arm("b", tf="1h", side=-1)], agents=[_Long()], enabled=on)
        for k in range(4):
            close = CLOSE + pd.Timedelta(minutes=15 * k)
            tick = _at(eng, pb, close)
            eng.on_bar_close(close, tick)
            exploration.on_pass(eng, tick)
        sent.append(len(_sent(eng)))
        return [(d.get("agent"), d.get("action"), d.get("p")) for d in eng.decisions if d.get("agent") == agent]

    sent: list[int] = []
    off, on = run(tmp_path / "off", False), run(tmp_path / "on", True)
    assert off and off == on and sent[0] == 0 and sent[1] >= 2      # the arms traded beside the agent


def test_a_slot_freed_by_the_brokers_stop_is_released_at_the_next_reconcile_not_the_next_bar_close(tmp_path):
    """A 1m/5m arm signals every minute; its position stopped out at the broker held the arm's slot (and its open
    risk) until the engine's next decision-bar close swept it. Exploration positions the broker no longer lists are
    swept at every reconciliation (`reconcile_every_s`, 30 s of tick time) once their exit deal is seen."""
    eng, pb = _engine(tmp_path, [_arm("a", max_open=1)])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    sl = eng.open[pid].sl
    assert sl is not None
    eng._last_reconcile = CLOSE
    stop = CLOSE + pd.Timedelta(seconds=40)                  # past reconcile_every_s, long before the next 15m close
    eng.on_tick(Tick(ts_utc=stop, bid=sl - 1.0, ask=sl - 0.8))
    assert not pb.positions()                                # the broker's stop closed it
    assert pid not in eng.open
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_utc is not None and rec.exit_reason == "stop"


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
    arm = _arm("s", tf="1m", style="scalp", hold_bars=1, stop_usd=20.0, target_r=1.0)   # no broker exit meanwhile
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


def _obs(tmp_path: Path) -> list[tuple[Any, ...]]:
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        return conn.execute("SELECT arm_id, taken, branch, mu, u, blocked_reason, label_fill_r IS NOT NULL, "
                            "quarantine FROM exploration_obs ORDER BY arm_id").fetchall()


def test_every_candidate_taken_or_not_is_recorded_with_its_propensity_and_a_closed_trade_its_fill(tmp_path):
    """Lane L3's contract: a taken candidate (mu = the random branch's), a blocked one (mu 0 and its reason), and
    the taken one's fill at its close, keyed by the order id."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b", side=-1, control_share=0.01)], caps={"a": 40.0, "b": 0.5})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))                 # b: its per-trade cap is below its loss at the stop
    [pid] = list(eng.open)
    eng._try_close(pid, "time_exit", CLOSE)
    eng._flush_closed()
    [a, b] = _obs(tmp_path)
    assert a[:4] == ("a", 1, "random", exploration.PI_RANDOM) and 0 <= a[4] < 1 and a[6] == 1 and a[7] is None
    assert b[:4] == ("b", 0, "blocked", 0.0) and b[5] == "arm_cap" and b[6] == 0 and b[7] is None


def test_at_champion_zero_the_ranked_branch_is_never_used(tmp_path, monkeypatch):
    """Quant ruling 2026-10-25: with no champion model every candidate goes through the random branch with its
    propensity recorded; and the engine never ranks on a model it cannot score, even when one is served."""
    from goldbot.research import exploration_data
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b", style="scalp", side=-1)])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert [r[2] for r in _obs(tmp_path)] == ["random", "random"]
    monkeypatch.setattr(exploration_data, "serving_model", lambda db, style, now, broker: {"model_version": "m1"})
    exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15)))
    rows = _obs(tmp_path)
    assert len(rows) == 4 and {r[2] for r in rows} == {"random"} and {r[3] for r in rows} == {exploration.PI_RANDOM}


# ------------------------------------------------------------------------------------------------ review round 1
def test_after_an_order_raises_no_other_arm_sends_in_the_same_pass(tmp_path, monkeypatch):
    """Trading-safety M1: an order that raised (or came back ambiguous) is in no count yet (global cap, net lots,
    pot slots), so nothing else is sent until it is settled; the rest are recorded blocked."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b"), _arm("c")])
    calls: list[str] = []

    def boom(prop: Any, *a: Any, **k: Any) -> Any:
        calls.append(prop.proposal_id)
        raise TimeoutError("bridge timed out")

    monkeypatch.setattr(eng, "_execute", boom)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(calls) == 1 and not eng.open
    rows = _obs(tmp_path)
    assert [r[1] for r in rows] == [1, 0, 0]                # the one sent (state unknown) and two held back
    assert [r[5] for r in rows if r[1] == 0] == ["order_unsettled", "order_unsettled"]


def test_the_per_trade_cap_and_the_open_risk_use_the_loss_at_the_stop_the_gate_set(tmp_path, monkeypatch):
    """Trading-safety L1: the gate may floor the stop wider (stops level, spread); the cap check and the open risk use
    that stop's loss, never the rule's."""
    monkeypatch.setattr(cost_probe, "stop_loss", lambda eng, side, price, distance, lots: distance)   # £1 per $1
    eng, pb = _engine(tmp_path, [_arm("a", stop_usd=5.0)], caps={"a": 12.0})
    check = eng.gate.check

    def wider(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        return gd.model_copy(update={"stop_distance": 3 * gd.stop_distance})

    eng.gate.check = wider                                   # type: ignore[method-assign]
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not eng.open and [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_cap"]
    eng2, pb2 = _engine(tmp_path / "b", [_arm("a", stop_usd=5.0, control_share=1.0)], caps={"a": 40.0})
    check2 = eng2.gate.check

    def wider2(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check2(intent, *a, **kw)
        return gd.model_copy(update={"stop_distance": 3 * gd.stop_distance})

    eng2.gate.check = wider2                                 # type: ignore[method-assign]
    exploration.on_pass(eng2, _at(eng2, pb2, CLOSE))
    [pid] = list(eng2.open)
    assert eng2.exploration is not None and eng2.exploration.open_loss[pid] == pytest.approx(15.0)


def test_a_blocked_arm_is_dropped_and_journalled_and_the_engine_runs_the_rest(tmp_path):
    """Trading-safety L2: one bad arm never stops the engine."""
    eng, pb = _engine(tmp_path, [_arm("ok"), _arm("off", status="blocked")])
    assert eng.exploration is not None and list(eng.exploration.arms) == ["ok"]
    assert [d["arm"] for d in _actions(eng, "exploration_arm_dropped")] == ["off"]
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1


def test_when_the_global_cap_is_full_every_waiting_signal_is_recorded_blocked_and_dropped(tmp_path):
    """Quant Q1: a signal the cap holds back is a blocked candidate now (mu 0, `global_cap`), not a stale one later."""
    eng, pb = _engine(tmp_path, [_arm(f"a{i}") for i in range(4)], max_open_total=2)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 2 and eng.exploration is not None and not eng.exploration.queue
    assert sorted(r[5] for r in _obs(tmp_path) if r[1] == 0) == ["global_cap", "global_cap"]


def test_a_censored_close_leaves_the_fill_label_empty(tmp_path):
    """Quant Q2: a trade cut short (Friday cut, kill switch) has no label: training never reads a censored R."""
    eng, pb = _engine(tmp_path, [_arm("a")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    eng._try_close(pid, "weekend_close_loser", CLOSE)
    eng._flush_closed()
    [row] = _obs(tmp_path)
    assert row[6] == 0                                      # label_fill_r IS NULL


def test_no_entry_of_any_style_whose_hold_reaches_the_friday_cut(tmp_path):
    """Quant Q2: swing too (`weekend_hold`): no label may straddle the weekend gap."""
    fri = pd.Timestamp("2025-03-07 16:00", tz="UTC")                    # 18:00 Athens; the cut is 21:30
    eng, pb = _engine(tmp_path, [_arm("w", tf="4h", style="swing", hold_bars=24)])
    exploration.on_pass(eng, _at(eng, pb, fri))
    assert not _sent(eng)
    assert [d["action"] for d in _actions(eng, "exploration_refused:")] == ["exploration_refused:weekend_hold"]


def test_strategy_shares_are_scaled_by_what_the_controls_leave_and_recorded_with_the_candidate(tmp_path):
    """Quant Q3: a control keeps its fixed share; the strategy arms' research shares (equal fallback held in 5-30%)
    are scaled by 1 - the controls' sum; each candidate row carries its arm's share."""
    arms = [_arm("c0", control_share=0.25), _arm("s1"), _arm("s2", side=-1)]
    eng, pb = _engine(tmp_path, arms)
    assert eng.exploration is not None
    sh = exploration.shares(eng.exploration, CLOSE)
    assert sh == pytest.approx({"c0": 0.25, "s1": 0.75 * 0.30, "s2": 0.75 * 0.30})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        got = dict(conn.execute("SELECT arm_id, arm_share FROM exploration_obs").fetchall())
    assert got == pytest.approx(sh)


def test_the_engine_records_its_broker_on_every_candidate_and_reads_only_its_brokers_weights(tmp_path):
    """ADR 0043 amendment (quant 2026-10-26): evidence is keyed by broker; an ICM host never allocates on another
    broker's bandit run, and every candidate it records carries `icm`."""
    from goldbot.research.exploration_data import write_weights
    eng, pb = _engine(tmp_path, [_arm("s1"), _arm("s2", side=-1)])
    assert eng.exploration is not None
    styles = {"s1": "intraday", "s2": "intraday"}
    write_weights(eng.exploration.db, CLOSE.value, {"s1": 0.30, "s2": 0.05}, styles, broker="vantage")
    assert exploration.shares(eng.exploration, CLOSE) == pytest.approx({"s1": 0.30, "s2": 0.30})   # equal shares
    write_weights(eng.exploration.db, CLOSE.value, {"s1": 0.25, "s2": 0.10}, styles, broker="icm")
    eng.exploration.weights_cache = None
    assert exploration.shares(eng.exploration, CLOSE) == pytest.approx({"s1": 0.25, "s2": 0.10})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        assert {r[0] for r in conn.execute("SELECT broker FROM exploration_obs")} == {"icm"}


def test_the_equal_share_fallback_is_held_inside_five_to_thirty_percent(tmp_path, monkeypatch):
    from goldbot.research import exploration_data

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("reader down")

    monkeypatch.setattr(exploration_data, "weights", broken)
    eng, _ = _engine(tmp_path, [_arm("s1"), _arm("s2")])
    assert eng.exploration is not None
    assert exploration.shares(eng.exploration, CLOSE) == pytest.approx({"s1": 0.30, "s2": 0.30})


def test_the_exploration_ledger_comes_with_the_aux_migrations():
    """Integration I3: the table is the unreleased aux migration 16's, not created by the engine."""
    from goldbot.db.migrations import AUX
    [m16] = [m for m in AUX if m.version == 16]
    assert any("exploration_trades" in st for st in m16.statements)
    assert not hasattr(exploration, "DDL")


def test_the_engine_config_comes_from_the_frozen_spec_and_the_accounts_registration(tmp_path):
    """Integration I1: rules from config/exploration_arms.yaml (hash-checked), registration (style, caps) from
    accounts.yaml; an arm registered but missing from the spec, or blocked there, is dropped and named."""
    acc = _probe_account(exploration=_explore(
        arms={"S0": acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.25),
              "I2A": acc_mod.ExplorationArm(max_loss=17.0, style="intraday"),
              "S6": acc_mod.ExplorationArm(max_loss=6.5, style="scalp"),
              "ZZ9": acc_mod.ExplorationArm(max_loss=6.5, style="scalp")},
        pots={"scalp": acc_mod.ExplorationPot(gbp=10, max_open=8), "intraday": acc_mod.ExplorationPot(gbp=10,
                                                                                                       max_open=5)}))
    cfg = exploration.config_for(acc)
    assert cfg is not None and cfg.enabled
    assert [a.arm_id for a in cfg.arms] == ["I2A", "S0"] and sorted(cfg.dropped) == ["S6", "ZZ9"]
    s0 = next(a for a in cfg.arms if a.arm_id == "S0")
    assert s0.tf == "1m" and s0.control_share == 0.25 and s0.spec.rule == "random_bracket"
    assert exploration.config_for(_probe_account()) is None             # not enabled: no exploration


def test_the_two_hosts_ship_the_adr_0043_budget_with_exploration_on():
    """Integration I2 (PD), per-style hosts (ADR 0043 amendment, PD 2026-10-26): icm-probe cost probes 30/220 and the
    scalp pot 168/220 with 14 slots (H-81 amendment (a) 7.1, ADR 0057: re-split from 60 / 138 / 12, the £198 kept); icm-demo intraday £108 / swing £90 = 198/220; swing
    £40 a trade and one open, intraday £17, scalp £6.50; 17 open at most on each (one host slot in reserve)."""
    accs = acc_mod.load_accounts(logins=False)
    probe, demo = accs["icm-probe"].exploration, accs["icm-demo"].exploration
    assert probe.enabled and demo.enabled and probe.max_open_total == demo.max_open_total == 17
    assert probe.budget_share * 220 == pytest.approx(168, abs=0.05)
    assert demo.budget_share * 220 == pytest.approx(198, abs=0.05)
    assert accs["icm-probe"].cost_probe.budget_share * 220 == pytest.approx(30, abs=0.05)
    assert (probe.budget_share + accs["icm-probe"].cost_probe.budget_share) * 220 == pytest.approx(198, abs=0.01)
    assert {k: (v.gbp, v.max_open) for k, v in probe.pots.items()} == {"scalp": (168, 14)}
    # ADR 0049 D1a (CFO 2026-10-27): a scalp pot for H-81 carved inside the same £198 (was intraday 108 / 9, swing 90 / 2)
    assert {k: (v.gbp, v.max_open) for k, v in demo.pots.items()} == {"scalp": (90, 8), "intraday": (68, 7),
                                                                       "swing": (40, 2)}
    assert {a.max_loss for a in probe.arms.values()} == {6.5}
    assert {a.max_loss for a in demo.arms.values() if a.style == "intraday"} == {17.0, 30.0}   # £30: wave-1 1h arms
    assert {a.max_loss for a in demo.arms.values() if a.style == "swing"} == {40.0}


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


# ------------------------------------------------------------------------------------------------ live 2026-10-26
def test_a_bar_stamped_ahead_of_the_host_clock_is_never_a_candidate_and_never_blocks_the_real_one(tmp_path):
    """Live 2026-10-26: a broker clock read 1 h ahead (server_tz Athens after EU DST vs the broker's NY+7) stamped the
    06:00 bar as 07:00. Its candidate (refused for the clock) took the id of the real 07:00 candidate, so the trade
    sent at 07:00 kept a `blocked` research row (first record stands) and its fill could never be recorded. A bar
    closing after the host clock is now skipped and journalled, unrecorded, and the real bar is evaluated when it
    comes."""
    eng, pb = _engine(tmp_path, [_arm("i0")])
    eng.cfg.live_clock = True
    host = CLOSE - pd.Timedelta(hours=1) + pd.Timedelta(seconds=2)
    eng.clock = lambda: host
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    assert not _sent(eng) and _obs(tmp_path) == []
    [ahead] = _actions(eng, "exploration_clock_ahead")
    assert ahead["ahead_s"] == pytest.approx(3598.0)
    eng.clock = lambda: CLOSE + pd.Timedelta(seconds=2)  # the clock is right again
    eng._refresh_account(tick)
    exploration.on_pass(eng, tick)
    assert len(_sent(eng)) == 1
    [row] = _obs(tmp_path)
    assert row[:3] == ("i0", 1, "random")                   # the real candidate: taken, never shadowed


# ------------------------------------------------------------------------------------------------ PD 2026-10-26
def test_a_demo_probe_only_account_may_hold_its_own_max_positions_and_every_other_keeps_the_global_two():
    """PD decision (A), ADR 0043 host cap: a learning host's RiskGate holds up to 18 positions through a per-account,
    demo-and-probe_only override; the global `risk.max_positions_per_account` (2) binds every other account."""
    from goldbot.config import load_settings
    from goldbot.ops.services.engine import engine_limits
    s = load_settings()
    assert s.risk.max_positions_per_account == 2
    assert engine_limits(s, _probe_account(max_positions=18)).max_positions == 18
    assert engine_limits(s, _probe_account(max_positions=18), paper=True).max_positions == 2   # paper: the defaults
    assert engine_limits(s, _probe_account()).max_positions == 2
    accs = acc_mod.load_accounts(logins=False)
    assert accs["icm-probe"].max_positions == accs["icm-demo"].max_positions == 18     # both learning hosts (PD 10-26)
    assert all(a.max_positions is None and engine_limits(s, a).max_positions == 2
               for a in accs.values() if not a.probe_only and not a.pilot.enabled)
    assert engine_limits(s, accs["icm-live"]).max_positions == 1          # ADR 0055: the live pilot's one position


@pytest.mark.parametrize("kw", [dict(mode="live", login=1), dict(probe_only=False)])
def test_a_max_positions_override_is_refused_off_a_demo_probe_only_account(kw):
    with pytest.raises(ValueError, match="max_positions"):
        _account(max_positions=18, **kw)


def test_the_max_positions_override_survives_the_limits_policy():
    """The engine resolves its limits through the policy every refresh: the override must reach the gate there too."""
    from goldbot.config import load_settings
    from goldbot.ops.services.engine import engine_limits_policy
    acc = acc_mod.load_accounts(logins=False)["icm-probe"]
    pol = engine_limits_policy(load_settings(), acc)
    assert pol.defaults.max_positions == 18



# ------------------------------------------------------------------------------------------------ WFRI and the Friday cut
THU_19_NY = pd.Timestamp("2025-03-07 00:00", tz="UTC")      # Thursday 19:00 New York (EST): WFRI's decision close
WFRI_HOLD = 43                                               # 30m bars to Friday 16:30 New York (21:30 UTC)


def _wfri(arm_id: str = "WFRI", *, follows: bool = True) -> Arm:
    """A WFRI-shaped arm (long, 30m, held to Friday 16:30 New York) on `friday_long`, the rule that declares the cut
    its exit, or on a rule that does not (`follows=False`)."""
    spec = SpecArm(id=arm_id, rule="friday_long" if follows else LONG, timeframe="30m", max_open=1,
                   params={"stop_usd": 5.0, "target_r": 50.0, "hold_bars": WFRI_HOLD})
    return Arm(spec=spec, style="swing")


def test_only_a_rule_that_declares_the_friday_cut_its_exit_may_hold_across_the_cut(tmp_path):
    eng, _ = _engine(tmp_path, [_arm("a")])
    hold = WFRI_HOLD * 1800
    assert arm_rules.follows_friday_cut(_wfri().spec) and not arm_rules.follows_friday_cut(_wfri(follows=False).spec)
    assert exploration.crosses_friday(eng, THU_19_NY, hold)                        # every other rule: refused
    assert not exploration.crosses_friday(eng, THU_19_NY, hold, follows_cut=True)  # WFRI: across the cut
    # never into the weekend: a hold to Saturday 00:00 server (22:00 UTC), or an entry after the week's cut
    assert exploration.crosses_friday(eng, THU_19_NY, hold + 1800, follows_cut=True)
    after_cut = pd.Timestamp("2025-03-07 19:31", tz="UTC")                        # 21:31 Athens
    assert exploration.crosses_friday(eng, after_cut, 600, follows_cut=True)    # (the gate's `weekend` also refuses)


def _wfri_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Engine, PaperBroker]:
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "friday_long", _rule(1))
    return _engine(tmp_path, [_wfri(), _wfri("NOCUT", follows=False)],
                   pots={"swing": Pot(gbp=40, max_open=2), "intraday": Pot(gbp=48, max_open=5)})


def test_wfri_enters_on_thursday_and_a_rule_without_the_cut_is_refused_the_same_hold(tmp_path, monkeypatch):
    eng, pb = _wfri_engine(tmp_path, monkeypatch)
    exploration.on_pass(eng, _at(eng, pb, THU_19_NY))
    assert [d["agent"] for d in _sent(eng)] == ["explore:WFRI"], eng.decisions
    assert [(d["agent"], d["action"]) for d in _actions(eng, "exploration_refused:")] == [
        ("explore:NOCUT", "exploration_refused:weekend_hold")]


def test_the_friday_cut_still_closes_a_losing_wfri_and_that_close_is_its_own_exit_not_a_censoring(tmp_path,
                                                                                                    monkeypatch):
    eng, pb = _wfri_engine(tmp_path, monkeypatch)
    exploration.on_pass(eng, _at(eng, pb, THU_19_NY))
    [pid] = list(eng.open)
    entry = eng.open[pid].open_price
    assert entry is not None
    cut = Tick(ts_utc=pd.Timestamp("2025-03-07 19:31", tz="UTC"), bid=entry - 1.0, ask=entry - 0.8)
    pb.on_tick(cut)
    assert eng._weekend_rule(cut)
    eng._flush_closed()
    assert not pb.positions() and pid not in eng.open
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == "weekend_close_loser" and rec.censored is False


def test_a_wfri_winner_is_closed_on_the_host_clock_at_1630_new_york_even_if_a_bar_is_missing(tmp_path, monkeypatch):
    """Trading-safety LOW (final review): the bar count alone holds a WFRI winner past Friday 16:30 New York when a
    decision bar is missing; `exploration.exits` closes it on the host clock at its signal close plus its hold."""
    eng, pb = _wfri_engine(tmp_path, monkeypatch)
    exploration.on_pass(eng, _at(eng, pb, THU_19_NY))
    [pid] = list(eng.open)
    eng.open[pid].bars_held = 0                                   # every bar of the hold missed: the count never ends
    closes: list[tuple[int, str]] = []
    monkeypatch.setattr(eng, "_try_close", lambda p, why, now, **kw: closes.append((p, why)))
    exit_ny = pd.Timestamp("2025-03-07 21:30", tz="UTC")           # Friday 16:30 New York (EST)
    exploration.exits(eng, exit_ny - pd.Timedelta(seconds=1))
    assert not closes
    exploration.exits(eng, exit_ny)
    assert closes == [(pid, "time_exit")]


def test_a_rule_without_the_cut_keeps_the_bar_count_time_exit_only(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", tf="1h", hold_bars=2)])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    closes: list[str] = []
    eng._try_close = lambda p, why, now, **kw: closes.append(why)  # type: ignore[assignment,method-assign]
    exploration.exits(eng, CLOSE + pd.Timedelta(days=2))
    assert closes == [] and pid in eng.open


def test_the_cuts_tightened_stop_is_wfris_own_exit_and_any_other_moved_stop_or_close_censors():
    wfri, other = _wfri(), _wfri(follows=False)

    def trade(sl: float) -> Any:
        return SimpleNamespace(side=1, initial_sl=95.0, sl=sl)

    assert not exploration._censored(wfri, "stop", trade(97.5))                   # tightened at the cut
    assert not exploration._censored(wfri, "weekend_close_loser", trade(95.0))
    assert exploration._censored(wfri, "stop", trade(94.0))                       # loosened
    assert exploration._censored(wfri, "weekend_close_no_quote", trade(95.0))     # the dead-feed flatten
    assert exploration._censored(other, "weekend_close_loser", trade(95.0))
    assert exploration._censored(other, "stop", trade(97.5))
    assert not exploration._censored(other, "time_exit", trade(95.0))


# ------------------------------------------------------------------------------------------------ PDC4 / DOND (8.15)
TUE_4H = pd.Timestamp("2025-03-04 08:00", tz="UTC")          # a Tuesday 4h close
TUE_1D = pd.Timestamp("2025-03-04 18:30", tz="UTC")          # a Tuesday 1d close (13:30 New York, EST)


def _capped_rule(side: int) -> Any:
    """The real rules' 8.15 step on a test candidate: the frozen hold capped at the week's end, the cut carried."""
    from goldbot.research.arm_rules_weekcut import capped
    gen = _rule(side)
    return lambda arm, ctx: capped(gen(arm, ctx), ctx.close_ts, arm.timeframe)


def _week_arm(arm_id: str, rule: str, tf: str, hold: int) -> Arm:
    spec = SpecArm(id=arm_id, rule=rule, timeframe=tf, max_open=1,  # type: ignore[arg-type]
                   params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": hold})
    return Arm(spec=spec, style="swing")


def _week_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rule: str, tf: str,
                 hold: int) -> tuple[Engine, PaperBroker]:
    """A PDC4- or DOND-shaped arm on its real rule name (capped), and the same frozen hold on a rule without the cut."""
    monkeypatch.setitem(arm_rules.RULE_FUNCS, rule, _capped_rule(1))
    eng, pb = _engine(tmp_path, [_week_arm("WEEK", rule, tf, hold), _week_arm("NOCUT", LONG, tf, hold)],
                      pots={"swing": Pot(gbp=40, max_open=2), "intraday": Pot(gbp=48, max_open=5)})
    assert eng.exploration is not None
    eng.exploration.needs_context = frozenset()               # DOND's stored-bar context is exploration_context's
    return eng, pb


@pytest.mark.parametrize(("rule", "tf", "hold", "close"), [("pdc_break", "4h", 32, TUE_4H),
                                                           ("donchian_break", "1d", 10, TUE_1D)])
def test_pdc4_and_dond_enter_on_a_weekday_and_the_same_frozen_hold_without_the_cut_is_refused(
        tmp_path, monkeypatch, rule, tf, hold, close):
    eng, pb = _week_engine(tmp_path, monkeypatch, rule, tf, hold)
    exploration.on_pass(eng, _at(eng, pb, close))
    assert [d["agent"] for d in _sent(eng)] == ["explore:WEEK"], eng.decisions
    assert [(d["agent"], d["action"]) for d in _actions(eng, "exploration_refused:")] == [
        ("explore:NOCUT", "exploration_refused:weekend_hold")]
    [rec] = exploration.trades(str(tmp_path))
    end = close + pd.Timedelta(seconds=rec.hold_s)
    assert end <= pd.Timestamp("2025-03-07 21:45", tz="UTC")       # Saturday 00:00 Athens less the slack
    assert rec.hold_s < hold * tf_seconds(tf)                         # capped at the week's end


def test_the_friday_cut_still_closes_a_losing_pdc4_and_that_close_is_its_own_exit(tmp_path, monkeypatch):
    eng, pb = _week_engine(tmp_path, monkeypatch, "pdc_break", "4h", 32)
    exploration.on_pass(eng, _at(eng, pb, TUE_4H))
    [pid] = list(eng.open)
    entry = eng.open[pid].open_price
    assert entry is not None
    cut = Tick(ts_utc=pd.Timestamp("2025-03-07 19:31", tz="UTC"), bid=entry - 1.0, ask=entry - 0.8)
    pb.on_tick(cut)
    assert eng._weekend_rule(cut)
    eng._flush_closed()
    assert not pb.positions() and pid not in eng.open
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == "weekend_close_loser" and rec.censored is False


def test_a_pdc4_winner_is_closed_at_its_wall_clock_time_exit_before_the_weekend(tmp_path, monkeypatch):
    """The engine counts a 15m+ hold in decision bars, and the daily break has none: a hold counted in bars from a
    Tuesday would run past Friday's close. A rule under the cut exits on the wall clock (`exploration.exits`), the
    history run's close of bar i + hold; the bar count stays as a backstop."""
    eng, pb = _week_engine(tmp_path, monkeypatch, "pdc_break", "4h", 32)
    exploration.on_pass(eng, _at(eng, pb, TUE_4H))
    [pid] = list(eng.open)
    tr = eng.open[pid]
    end = TUE_4H + pd.Timedelta(seconds=tr.max_bars * tf_seconds(eng.cfg.decision_tf))
    assert end == pd.Timestamp("2025-03-07 20:00", tz="UTC")       # Friday's last 4h close before the week ends
    closes: list[tuple[int, str]] = []
    monkeypatch.setattr(eng, "_try_close", lambda p, why, now, **kw: closes.append((p, why)))
    exploration.exits(eng, end - pd.Timedelta(seconds=1))
    assert not closes
    exploration.exits(eng, end)
    assert closes == [(pid, "time_exit")]
    assert not exploration._censored(eng.exploration.arms["WEEK"], "time_exit", tr)   # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------------ stop-band skips
def test_a_candidate_outside_the_arms_stop_band_is_skipped_journalled_and_recorded_with_its_propensity(tmp_path):
    """Arms expansion section 4: "A candidate outside its £ cap or stop band is skipped and logged with its
    propensity": no order, an `exploration_skipped:<reason>` journal entry with the arm's share, and a blocked
    candidate (mu 0) with its reason for the off-policy estimates."""
    spec = SpecArm(id="band", rule=LONG, timeframe="15m", max_open=1,
                   params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4, "max_stop_usd": 4.0})
    low = SpecArm(id="floor", rule=LONG, timeframe="15m", max_open=1,
                  params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4, "min_stop_usd": 6.0})
    eng, pb = _engine(tmp_path, [Arm(spec=spec, style="intraday"), Arm(spec=low, style="intraday")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not _sent(eng) and not pb.positions()
    skips = {d["arm"]: d for d in _actions(eng, "exploration_skipped:")}
    assert skips["band"]["action"] == "exploration_skipped:stop_gt_cap"
    assert skips["floor"]["action"] == "exploration_skipped:stop_lt_floor"
    assert all(d["propensity"] is not None and d["propensity"] > 0 and d["stop_usd"] == 5.0 for d in skips.values())
    ex = eng.exploration
    assert ex is not None
    u = {a: exploration.draw(exploration.order_id(eng, ex.arms[a], CLOSE)) for a in ("band", "floor")}
    assert [r[:6] for r in _obs(tmp_path)] == [("band", 0, "blocked", 0.0, pytest.approx(u["band"]), "stop_gt_cap"),
                                               ("floor", 0, "blocked", 0.0, pytest.approx(u["floor"]), "stop_lt_floor")]


# ------------------------------------------------------------------------------------------------ the quarter-hour
def test_a_scalp_arm_sends_a_few_seconds_after_a_quarter_hour_close_fixed_by_its_arm_id():
    """SRE 2026-10-27: the quarter-hour closes carry the 15m+ work and sends; a 1m/5m arm's order on such a close goes
    out 3-7 s after it, the same delay for the same arm every time (no state, restart-safe), inside its stale window
    and the first minute; on every other close and for 15m+ arms, at once."""
    from goldbot.research.arms import send_delay_s
    qh, off = CLOSE, CLOSE + pd.Timedelta(minutes=3)
    ids = [f"S{i}" for i in range(40)]
    got = {a: send_delay_s(a, "1m", qh) for a in ids}
    assert all(3.0 <= d <= 7.0 for d in got.values()) and len(set(got.values())) >= 5
    assert got == {a: send_delay_s(a, "1m", qh + pd.Timedelta(hours=5)) for a in ids}
    assert max(got.values()) < exploration.stale_s("1m") - 2.0
    assert all(send_delay_s(a, "5m", qh) == got[a] for a in ids)
    assert send_delay_s("S0", "1m", off) == 0.0 and send_delay_s("S0", "15m", qh) == 0.0
    assert send_delay_s("S0", "5m", CLOSE + pd.Timedelta(minutes=5)) == 0.0


def test_a_scalp_signal_on_a_quarter_hour_close_waits_its_delay_and_keeps_its_decision_bar(tmp_path):
    from goldbot.research.arms import send_delay_s
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [arm])
    tick = _at(eng, pb, CLOSE)
    wait = send_delay_s("s", "1m", CLOSE)
    exploration.on_pass(eng, Tick(ts_utc=CLOSE + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    ex = eng.exploration
    assert ex is not None and not _sent(eng) and [s.bar_ts for s in ex.queue] == [CLOSE]
    exploration.on_pass(eng, Tick(ts_utc=CLOSE + pd.Timedelta(seconds=wait - 0.1), bid=tick.bid, ask=tick.ask))
    assert not _sent(eng)
    exploration.on_pass(eng, Tick(ts_utc=CLOSE + pd.Timedelta(seconds=wait), bid=tick.bid, ask=tick.ask))
    assert len(_sent(eng)) == 1 and not ex.queue
    [rec] = exploration.trades(str(tmp_path))
    assert rec.signal_utc == CLOSE

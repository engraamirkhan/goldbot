"""Engine lane E3 (exit path): H-80's BXR stop trailed live by `runner_stop` (a ratchet through the engine's own stop
move, never loosened, resumed after a restart, equal to the history walk on the same bars); BXT/BXF as virtual twins on
BXR's own fills that never place an order; XC15's netting-host shift behind an open KA15, excluded from pairing."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import Engine, cost_probe, exploration
from goldbot.engine.exploration import Arm
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.research import arm_rules
from goldbot.research import arm_rules_clock_box as K
from goldbot.research.arms import Arm as SpecArm
from tests.test_exploration_engine import TICKS, _actions, _engine, _sent, _Terminal

T0 = pd.Timestamp("2025-03-04 12:00", tz="UTC")       # a Tuesday 15m close in market hours, far from the daily break
R_USD = 3.0                                             # BXR's R (1.5 ATR15) in these tests
STRUCT_R = 2.7


class _Netting(_Terminal):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"margin_mode": "netting"})


def _at_close(bars: pd.DataFrame, close: pd.Timestamp) -> pd.DataFrame:
    return bars[bars["visible_at"] <= close]


def _only_at(close: pd.Timestamp, frame: dict[str, Any]) -> Any:
    """A rule with one candidate, on the bar closing at `close` (the rule's own columns in `frame`)."""
    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        if pd.Timestamp(ctx.bars["visible_at"].iloc[-1]) != close:
            return pd.DataFrame({k: pd.Series(dtype=float) for k in ("idx", *frame)})
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], **{k: [v] for k, v in frame.items()}})
    return gen


@pytest.fixture(autouse=True)
def _fixed_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: 1.0)


def _bxr(monkeypatch: pytest.MonkeyPatch, side: int = 1, arm_id: str = "BXR15", exit_mode: str = "runner") -> Arm:
    target = K.NO_TARGET_R if exit_mode == "runner" else 20.0             # a target the trend does not reach here
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "box_next", _only_at(T0, {
        "side": side, "risk_usd": R_USD, "target_r": target, "max_bars": 95, "struct_r": STRUCT_R}))
    spec = SpecArm(id=arm_id, rule="box_next", timeframe="15m", params={"exit": exit_mode})
    return Arm(spec=spec, style="intraday")


def _trend(side: int, per_min: float = 0.1, minutes: int = 240) -> pd.DataFrame:
    """The engine's 1m bars: the synthetic feed up to T0, then a steady move of `per_min` a minute in `side`'s favour."""
    hist = TICKS[TICKS["ts_utc"] < T0]
    px = float(hist["bid"].iloc[-1])
    ts = T0 + pd.to_timedelta(np.arange(minutes * 4) * 15, unit="s")
    bid = px + side * per_min * np.arange(minutes * 4) / 4
    after = pd.DataFrame({"ts_utc": ts, "bid": bid, "ask": bid + 0.2})
    return ticks_to_1m(pd.concat([hist[["ts_utc", "bid", "ask"]], after], ignore_index=True))


def _open_bxr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: int = 1,
              exit_mode: str = "runner") -> tuple[Engine, Any, int, pd.DataFrame]:
    arm = _bxr(monkeypatch, side, arm_id="BXR15" if exit_mode == "runner" else "BOXFIX", exit_mode=exit_mode)
    eng, pb = _engine(tmp_path, [arm])
    bars = _trend(side)
    exploration.on_pass(eng, _tick(eng, pb, bars, T0))
    [pid] = list(eng.open)
    return eng, pb, pid, bars


def _tick(eng: Engine, pb: Any, bars: pd.DataFrame, close: pd.Timestamp) -> Tick:
    """The engine just after `close`: its bars through the close and a quote at the last minute's close."""
    eng.bars_1m = _at_close(bars, close).reset_index(drop=True)
    last = eng.bars_1m.iloc[-1]
    tick = Tick(ts_utc=close + pd.Timedelta(seconds=2), bid=float(last["bid_close"]), ask=float(last["ask_close"]))
    pb.on_tick(tick)
    eng._last_tick = tick
    eng._refresh_account(tick)
    eng._closed_through = close
    return tick


def _expected(eng: Engine, pid: int, bars: pd.DataFrame, close: pd.Timestamp) -> float:
    tr = eng.open[pid]
    seen = _at_close(bars, close)
    assert tr.open_price is not None
    return round(K.runner_stop_at(K.mid_path(seen, T0), K.decision_closes(seen, "15m", T0, 96), tr.open_price,
                                  tr.side, R_USD, R_USD / K.BOX_STOP_ATR), 2)


# ------------------------------------------------------------------------------------------------ (1) the runner live
@pytest.mark.parametrize("side", [1, -1])
def test_bxrs_stop_follows_the_history_walk_at_each_close_through_the_engines_stop_move(tmp_path, monkeypatch, side):
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch, side)
    initial = eng.open[pid].sl
    moved: list[float] = []
    for k in range(1, 13):
        close = T0 + pd.Timedelta(minutes=15 * k)
        _tick(eng, pb, bars, close)
        exploration.trail(eng, close)
        want = _expected(eng, pid, bars, close)
        tr = eng.open[pid]
        assert tr.open_price is not None and tr.sl is not None
        armed = side * (want - (tr.open_price - side * R_USD)) > 1e-9
        assert tr.sl == (want if armed else initial)
        [pos] = pb.positions()
        assert pos.sl == pytest.approx(tr.sl)
        moved.append(tr.sl)
    assert all(side * (b - a) >= 0 for a, b in zip(moved, moved[1:], strict=False))     # only ever tighter
    fill = eng.open[pid].open_price
    assert fill is not None and side * (moved[-1] - fill) > K.BE_USD                  # trailed past break-even
    trails = _actions(eng, "trail")
    assert trails and all(t["ok"] and t["agent"] == "explore:BXR15" for t in trails)


def test_bxrs_trail_never_loosens_a_stop_already_tighter(tmp_path, monkeypatch):
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch)
    close = T0 + pd.Timedelta(minutes=120)
    _tick(eng, pb, bars, close)
    exploration.trail(eng, close)
    tr = eng.open[pid]
    assert tr.sl is not None
    tighter = round(tr.sl + 3.0, 2)                             # 2 ATR behind the best price: not through it
    tr.sl = tighter
    pb.modify(pid, tighter, pb.positions()[0].tp)
    nxt = close + pd.Timedelta(minutes=15)
    _tick(eng, pb, bars, nxt)
    assert _expected(eng, pid, bars, nxt) < tighter
    exploration.trail(eng, nxt)
    assert eng.open[pid].sl == tighter and pb.positions()[0].sl == pytest.approx(tighter)


def test_a_restart_mid_trade_resumes_the_same_trail_from_the_persisted_stop_and_the_stored_bars(tmp_path, monkeypatch):
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch)
    for k in (1, 2, 3, 4, 5, 6):
        close = T0 + pd.Timedelta(minutes=15 * k)
        _tick(eng, pb, bars, close)
        exploration.trail(eng, close)
    before = eng.open[pid].sl
    assert before is not None
    # a restart that lost the open table and the order rows: adopted from the broker by its comment
    (tmp_path / "orders_icm-probe.json").unlink()
    (tmp_path / "open_trades_icm-probe.json").unlink(missing_ok=True)
    eng2, _ = _engine(tmp_path, [_bxr(monkeypatch)], pb=pb)
    eng2.open.clear()
    eng2._reconcile(T0 + pd.Timedelta(minutes=91))
    assert eng2.open[pid].sl == before
    for k in (7, 8, 9):                                          # only the restarted engine trails from here
        close = T0 + pd.Timedelta(minutes=15 * k)
        _tick(eng2, pb, bars, close)
        exploration.trail(eng2, close)
        assert eng2.open[pid].sl == _expected(eng2, pid, bars, close) > before
        assert pb.positions()[0].sl == pytest.approx(eng2.open[pid].sl)


def test_the_trail_runs_at_every_bar_close_beside_the_engines_own_exits(tmp_path, monkeypatch):
    eng, pb, _pid, bars = _open_bxr(tmp_path, monkeypatch)
    seen: list[pd.Timestamp] = []
    monkeypatch.setattr(exploration, "trail", lambda e, c: seen.append(c))
    close = T0 + pd.Timedelta(minutes=15)
    tick = _tick(eng, pb, bars, close)
    eng.on_bar_close(close, tick)
    assert seen == [close]


def test_only_a_runner_arms_stop_is_ever_trailed_and_no_other_box_arm_gets_twins(tmp_path, monkeypatch):
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch, exit_mode="fixed")
    initial = eng.open[pid].sl
    for k in range(1, 13):
        close = T0 + pd.Timedelta(minutes=15 * k)
        _tick(eng, pb, bars, close)
        exploration.trail(eng, close)
    assert eng.open[pid].sl == initial and pb.positions()[0].sl == pytest.approx(initial)
    assert not _actions(eng, "trail") and not _twins(tmp_path)


@pytest.mark.parametrize("reason,censored", [("engine_stop_close", False), ("trail_close", False),
                                             ("kill_switch_close", True)])
def test_a_bxr_closed_at_its_own_trailed_stop_is_not_censored(tmp_path, monkeypatch, reason, censored):
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch)
    close = T0 + pd.Timedelta(minutes=120)
    _tick(eng, pb, bars, close)
    exploration.trail(eng, close)
    tr = eng.open[pid]
    assert tr.sl != tr.initial_sl
    eng._try_close(pid, reason, close)
    eng._flush_closed()
    [rec] = [r for r in exploration.trades(str(tmp_path)) if r.arm_id == "BXR15"]
    assert rec.censored is censored


def test_a_bxr_stop_moved_tighter_is_its_own_exit_and_a_looser_moved_stop_censors(monkeypatch):
    """Quant review C4: BXR's trail only ever tightens, so its moved stop is natural ONLY when side x (sl - initial_sl)
    > 0; a stop moved looser (or for a short, moved up) censors the label."""
    bxr = _bxr(monkeypatch)

    def trade(side: int, sl: float | None) -> Any:
        return SimpleNamespace(side=side, initial_sl=100.0, sl=sl)

    assert not exploration._censored(bxr, "stop", trade(1, 101.0))               # long, tighter
    assert not exploration._censored(bxr, "trail_close", trade(-1, 99.0))        # short, tighter
    assert exploration._censored(bxr, "stop", trade(1, 99.0))                    # long, looser
    assert exploration._censored(bxr, "engine_stop_close", trade(-1, 101.0))     # short, looser
    assert exploration._censored(bxr, "stop", trade(1, None))                    # a stop removed
    assert not exploration._censored(bxr, "stop", trade(1, 100.0))               # unmoved


# ------------------------------------------------------------------------------------------------ (2) the twins
def _twins(tmp_path: Path) -> list[exploration.TwinRecord]:
    return exploration.twins(str(tmp_path))


def test_a_twin_is_charged_the_exit_half_spread_and_the_swap_of_every_rollover_it_held():
    """Quant review C4: a twin is scored like a real fill. Its entry is BXR's real fill (the entry spread is paid);
    its exit is on the mid path, so it pays the exit leg's half of the bid-ask at the exit minute (a long sells at
    the bid) and the swap of each rollover between its signal and its exit (the triple night counted three times)."""
    from goldbot.labels.triple_barrier import SwapSpec
    swap = SwapSpec(long_usd_per_lot=-50.0, short_usd_per_lot=10.0, triple_weekday=2, server_tz="Europe/Athens")
    minutes = pd.date_range("2025-03-05 21:50", periods=20, freq="1min", tz="UTC")   # across Wednesday's rollover
    b = pd.DataFrame({"ts_utc": minutes, "bid_close": 100.0, "ask_close": 100.0 + np.linspace(0.1, 0.5, 20)})
    exit_at = minutes[15] + pd.Timedelta(minutes=1)                                 # the 16th minute's close
    spread, swap_usd = exploration.twin_charges(b, T0, exit_at, 1, swap)
    assert spread == pytest.approx((b["ask_close"].iloc[15] - 100.0) / 2)
    assert swap_usd == pytest.approx(-0.5 * 3 + -0.5 * 1)    # Tuesday night and Wednesday's triple: 4 nights x -$0.50
    assert exploration.twin_charges(b, T0, T0 + pd.Timedelta(hours=1), -1, swap)[1] == 0.0      # no rollover held
    assert exploration.twin_charges(b.iloc[:0], T0, exit_at, 1, swap)[0] is None      # no exit minute: not charged


def test_a_bxr_fill_opens_its_two_virtual_twins_from_the_fill_price_and_no_order(tmp_path, monkeypatch):
    eng, pb, pid, _ = _open_bxr(tmp_path, monkeypatch)
    assert len(pb.positions()) == 1 and len(_sent(eng)) == 1
    [rec] = exploration.trades(str(tmp_path))
    assert rec.arm_id == "BXR15"
    tw = {t.arm_id: t for t in _twins(tmp_path)}
    assert set(tw) == {"BXT15", "BXF15"}
    fill = eng.open[pid].open_price
    for t in tw.values():
        assert t.entry_price == fill and t.client_order_id == rec.client_order_id and t.of_arm_id == "BXR15"
        assert t.risk_usd == R_USD and t.hold_bars == 96 and t.signal_utc == T0 and t.exit_utc is None
    assert tw["BXT15"].target_r == STRUCT_R and tw["BXF15"].target_r == K.BOX_TARGET_R


def test_a_twin_closes_at_its_own_barrier_as_the_history_walk_does_even_after_bxr_has_closed(tmp_path, monkeypatch):
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch)
    eng._try_close(pid, "kill_switch_close", T0)              # BXR gone: its twins still run to their own barriers
    eng._flush_closed()
    done: dict[str, exploration.TwinRecord] = {}
    for k in range(1, 17):
        close = T0 + pd.Timedelta(minutes=15 * k)
        _tick(eng, pb, bars, close)
        exploration.trail(eng, close)
        done = {t.arm_id: t for t in _twins(tmp_path) if t.exit_utc is not None}
    assert set(done) == {"BXT15", "BXF15"}
    path, closes = K.mid_path(bars, T0), K.decision_closes(bars, "15m", T0, 96)
    for t in done.values():
        w = K.walk_runner(path, closes, t.entry_price, t.side, t.risk_usd, t.risk_usd / K.BOX_STOP_ATR,
                          target_r=t.target_r)
        assert (t.exit_reason, t.exit_price, t.exit_utc) == (w["kind"], w["exit"], w["at"])
        assert t.exit_reason == "target" and t.gross_r == pytest.approx(t.target_r)
        assert t.exit_spread_usd == pytest.approx(0.1) and t.swap_usd == 0.0     # bid-ask 0.20, no rollover held
    assert not pb.positions() and len(exploration.trades(str(tmp_path))) == 1


def _scored_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str) -> list[exploration.TwinPair]:
    eng, pb, pid, bars = _open_bxr(tmp_path, monkeypatch)
    eng._try_close(pid, reason, T0 + pd.Timedelta(minutes=1))
    eng._flush_closed()
    for k in range(1, 17):
        _tick(eng, pb, bars, T0 + pd.Timedelta(minutes=15 * k))
        exploration.trail(eng, T0 + pd.Timedelta(minutes=15 * k))
    assert len([t for t in _twins(tmp_path) if t.exit_utc is not None]) == 2
    return exploration.twin_pairs(str(tmp_path))


def test_a_pair_whose_bxr_closed_censored_is_dropped_from_the_twin_comparison(tmp_path, monkeypatch):
    """Quant review C4: BXR - BXF and BXT - BXF compare like with like; a BXR cut short (kill switch) has no outcome
    of its own to compare, so its twins leave the read."""
    assert _scored_pairs(tmp_path, monkeypatch, "kill_switch_close") == []


def test_a_natural_pair_charges_the_twin_bxrs_own_commission_beside_its_exit_spread_and_swap(tmp_path, monkeypatch):
    """The twin pays what the real fill paid: BXR's broker commission per oz (same order, same lots), the exit half
    spread and the swap; BXR's side is its broker-net P&L per oz, both over the same 1 R."""
    pairs = _scored_pairs(tmp_path, monkeypatch, "time_exit")
    [rec] = exploration.trades(str(tmp_path))
    assert rec.censored is False and {p.exit_mode for p in pairs} == {"struct", "fixed"}
    assert rec.pnl is not None and rec.commission is not None and rec.fx_acct_per_usd
    oz = rec.lots * exploration.CONTRACT_OZ
    commission = rec.commission / rec.fx_acct_per_usd / oz
    for p in pairs:
        [tw] = [t for t in _twins(tmp_path) if t.twin_id == p.twin_id]
        assert tw.gross_r is not None and tw.exit_spread_usd is not None and tw.swap_usd is not None
        want = tw.gross_r - (tw.exit_spread_usd + commission - tw.swap_usd) / tw.risk_usd
        assert p.twin_net_r == pytest.approx(want) and p.twin_net_r < tw.gross_r
        assert p.bxr_net_r == pytest.approx(rec.pnl / rec.fx_acct_per_usd / oz / tw.risk_usd)


# ------------------------------------------------------------------------------------------------ (3) XC15's shift
def _anchor_pair(monkeypatch: pytest.MonkeyPatch) -> list[Arm]:
    for rule, side in (("clock_anchor", 1), ("clock_anchor_control", -1)):
        monkeypatch.setitem(arm_rules.RULE_FUNCS, rule, _only_at(T0, {
            "side": side, "risk_usd": 4.0, "target_r": K.NO_TARGET_R, "max_bars": 1, "stratum": "C"}))
    return [Arm(spec=SpecArm(id="KA15", rule="clock_anchor", timeframe="15m"), style="scalp"),
            Arm(spec=SpecArm(id="XC15", rule="clock_anchor_control", timeframe="15m"), style="scalp",
                control_share=0.2)]


def test_on_a_netting_host_xc15_waits_for_ka15_to_close_and_its_shifted_entry_is_marked(tmp_path, monkeypatch):
    """Arms 8.9: "If KA15 is open on a netting host, XC15 enters at the next 15m close and the shift is recorded";
    8.13: "An XC15 entry that is shifted because KA15 is open is excluded from pairing. It is still logged." The
    entry is `shift_control`'s on the same open span."""
    eng, pb = _engine(tmp_path, _anchor_pair(monkeypatch), pb=_Netting(equity=10_000, adverse_slip_points=0.0))
    bars = _trend(1)
    exploration.on_pass(eng, _tick(eng, pb, bars, T0))
    [ka] = [pid for pid, t in eng.open.items() if t.agent_id == "explore:KA15"]
    assert len(eng.open) == 1 and _actions(eng, "exploration_shifted")
    exploration.on_pass(eng, _tick(eng, pb, bars, T0 + pd.Timedelta(minutes=15)))
    assert len(eng.open) == 1                                   # KA15 still open: XC15 waits
    eng._try_close(ka, "time_exit", T0 + pd.Timedelta(minutes=30))
    eng._flush_closed()
    exploration.on_pass(eng, _tick(eng, pb, bars, T0 + pd.Timedelta(minutes=30)))
    [xc] = [r for r in exploration.trades(str(tmp_path)) if r.arm_id == "XC15"]
    want = K.shift_control(pd.DatetimeIndex([T0]), [(T0, T0 + pd.Timedelta(minutes=30))])
    assert xc.signal_utc == want["entry"].iloc[0] and xc.decision_utc == T0 and xc.shifted is True
    assert K.pair_with_control(pd.DataFrame({"decision": [T0]}), want).empty


def test_on_a_hedging_host_xc15_enters_at_its_own_decision_close_beside_ka15(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, _anchor_pair(monkeypatch))
    exploration.on_pass(eng, _tick(eng, pb, _trend(1), T0))
    recs = {r.arm_id: r for r in exploration.trades(str(tmp_path))}
    assert set(recs) == {"KA15", "XC15"} and len(pb.positions()) == 2
    assert recs["XC15"].signal_utc == T0 and recs["XC15"].shifted is False
    assert not _actions(eng, "exploration_shifted")

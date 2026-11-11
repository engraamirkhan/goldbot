"""Backlog 160: open trades age on the base clock from every bar close after any cold start.

Design rule (Engine.on_bar_close): "position management stays on the base clock". Below the 400-bar floor a close
decides nothing, but an open trade's `bars_held` still counts and its time exit fires at that close; before, a cold
start (a restart with no stored history) paused the count until the first decision frame existed, about 30 h for a
15m arm. `bars_held` is saved with the orders file, so a restart resumes the count where it stopped, never at 0."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Proposal

OWNER = 111
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")       # a Wednesday


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
                  pb, [], {}, ApprovalCenter({OWNER}))


def _open_trade(tmp_path: Path, held: int = 0, to_go: int | None = None) -> tuple[PaperBroker, int, int]:
    """A filled trade, `held` base bars into its hold (or `to_go` bars before its time barrier), saved by the engine that opened it; the engine then dies."""
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _engine(tmp_path, pb)
    prop = Proposal(proposal_id="icm-demo-160-a", account_id="icm-demo", agent_id=SPECIALISTS["session_open"]().agent_id,
                    side=1, lots=0.1, entry=2400.2, stop=2390.0, target=2420.0, p=0.62, ev_r=0.2, spread_points=20,
                    top_features=[])
    eng._execute(prop, SPECIALISTS["session_open"](), 0.1, 2390.0, 2420.0, requested=2400.2)
    [pos] = pb.positions()
    tr = eng.open[pos.position_id]
    tr.bars_held = held if to_go is None else tr.max_bars - to_go
    assert 0 <= tr.bars_held < tr.max_bars - 2
    eng._save_orders()
    return pb, pos.position_id, tr.max_bars


def _closes(eng: Engine, pb: PaperBroker, start: pd.Timestamp, n: int) -> pd.Timestamp:
    """Feed one tick a minute for `n` whole 15m bars from `start`; returns the time after the last one."""
    t = start
    for _ in range(n * 15):
        pb.on_tick(_tick(t))
        eng.on_tick(_tick(t))
        t += pd.Timedelta(minutes=1)
    return t


def test_a_cold_start_with_an_open_trade_fires_its_time_exit_at_the_bar_its_count_reaches_not_30_hours_late(tmp_path):
    pb, pid, max_bars = _open_trade(tmp_path, to_go=3)
    eng = _engine(tmp_path, pb)                                       # cold start: no stored history at all
    assert eng.open[pid].bars_held == max_bars - 3
    t = _closes(eng, pb, T0 + pd.Timedelta(hours=1), 3)              # three base bars, far below the 400-bar floor
    assert pid in eng.open                                            # the third closes at the next bar's first tick
    _closes(eng, pb, t, 1)
    assert len(eng.bars_1m) < 400
    assert pid not in eng.open and pid not in {p.position_id for p in pb.positions()}
    assert any(d.get("action") == "time_exit" and d.get("position") == pid and d["ok"] for d in eng.decisions)


def test_a_restart_mid_trade_resumes_its_bar_count_where_the_saved_engine_left_it(tmp_path):
    pb, pid, _ = _open_trade(tmp_path, held=5)
    eng = _engine(tmp_path, pb)
    assert eng.open[pid].bars_held == 5                               # the count survives the restart
    t = _closes(eng, pb, T0 + pd.Timedelta(hours=1), 2)
    _closes(eng, pb, t, 1)
    assert eng.open[pid].bars_held == 7         # 5 saved + 2 closes (the third bar closes at the next bar's tick)
    again = _engine(tmp_path, pb)                                     # a second restart: never back to 0
    assert again.open[pid].bars_held == 7


def test_bars_held_counts_once_per_base_close_across_the_floor(tmp_path):
    """No close is counted twice when the window crosses 400 bars: one increment per base close, below or above."""
    pb, pid, max_bars = _open_trade(tmp_path, held=0)
    eng = _engine(tmp_path, pb)
    eng.open[pid].max_bars = 10_000
    t = _closes(eng, pb, T0 + pd.Timedelta(hours=1), 30)             # 450 minutes: the floor is crossed on the way
    _closes(eng, pb, t, 0)
    assert len(eng.bars_1m) >= 400
    assert eng.open[pid].bars_held == 30 - 1                          # the 30th bar closes at the next bar's first tick


def test_a_hard_flat_fires_below_the_floor_at_the_first_base_close_at_or_after_its_deadline(tmp_path):
    """Review C1: the hard flat needs only the deadline and the close, never the frame; the trail still waits."""
    pb, pid, _ = _open_trade(tmp_path)
    eng = _engine(tmp_path, pb)
    tr = eng.open[pid]
    start = T0 + pd.Timedelta(hours=1)
    tr.policy, tr.timeframe = ExitPolicy(trail_atr=1.0, flat_before_min=30), "1h"
    tr.flat_at = start + pd.Timedelta(minutes=20)                    # the first base close at or after it: start+30m
    t = _closes(eng, pb, start, 2)                                    # closes start+15m (before the deadline)
    assert len(eng.bars_1m) < 400 and t == start + pd.Timedelta(minutes=30)
    assert pid in eng.open                                            # start+30m closes at the next bar's first tick
    pb.on_tick(_tick(t))
    eng.on_tick(_tick(t))
    assert pid not in eng.open and pid not in {p.position_id for p in pb.positions()}
    assert [d["action"] for d in eng.decisions if d.get("position") == pid] == ["hard_flat"]


def test_a_count_below_the_floor_is_on_disk_at_its_close(tmp_path):
    """Review C2: the below-floor close saves the orders file, so a crash right after it never loses the bar."""
    pb, pid, _ = _open_trade(tmp_path, held=5)
    eng = _engine(tmp_path, pb)
    eng.cfg = eng.cfg.model_copy(update={"state_every_s": 10_000})   # no periodic write between closes
    t = _closes(eng, pb, T0 + pd.Timedelta(hours=1), 1)
    pb.on_tick(_tick(t))
    eng.on_tick(_tick(t))                                             # this tick closes the first bar
    saved = json.loads((tmp_path / "orders_icm-demo.json").read_text())
    assert eng.open[pid].bars_held == 6 and saved["open"][str(pid)]["bars_held"] == 6

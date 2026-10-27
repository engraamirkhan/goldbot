"""Trading-safety review MEDIUM-2 (of 2c1935f): an order whose send raised keeps its pending_orders row `sending`
(LOW-C). The position it opened is adopted by reconciliation; before this fix the row stayed `sending`, so a restart
"recovered" the send again and replaced the adopted trade with a fresh one: its stop went back to the entry stop and
bars_held / scaled to zero, and the exit policy could then send a modify LOOSER than the broker's stop.

The rules now:
(a) adoption of a position whose row is `sending` marks the row filled with its position id, and saves;
(b) a restart that finds the row's position already in the open table keeps that trade and only marks the row filled;
(c) the exit policy sends a modify only when the new stop is tighter than both the engine's and the broker's stop;
(d) a `sending` row older than ~60 s is resolved in-process from positions and deals, as on a restart."""
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.engine import magic as engine_magic
from goldbot.execution.broker import OrderIntent, OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal

T0 = pd.Timestamp("2025-03-04 10:00", tz="UTC")       # a Tuesday, market open


class LostReply(PaperBroker):
    """The order reaches the broker, then the terminal raises before the result comes back. (A call with no answer, a
    bridge timeout, is no longer a raise but an ambiguous row: tests/test_order_send.py; an answered error still is.)"""

    raise_next = True

    def place_order(self, intent: OrderIntent) -> OrderResult:
        res = super().place_order(intent)
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("terminal connection lost after the fill")
        return res


class Spy(LostReply):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.modifies: list[tuple[int, float | None]] = []

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        self.modifies.append((position_id, sl))
        return super().modify(position_id, sl, tp)


def _engine(tmp_path: Any, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                               owner_user_id=111), pb, [TrendSpecialist()], {})


def _send_that_raises(eng: Engine, pb: PaperBroker) -> tuple[str, int]:
    agent = TrendSpecialist()
    prop = Proposal(proposal_id="icm-demo-raised-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_400.2, stop=2_390.0, target=2_430.0, p=0.6, ev_r=0.1, spread_points=20.0, top_features=[])
    with pytest.raises(RuntimeError):
        eng._execute(prop, agent, 0.1, 2_390.0, 2_430.0, requested=2_400.2, atr_usd=10.0)
    (pos,) = pb.positions()
    assert eng._orders[prop.proposal_id].status == "sending" and pos.position_id not in eng.open
    return prop.proposal_id, pos.position_id


def _start(tmp_path: Any) -> tuple[Engine, Spy]:
    pb = Spy(adverse_slip_points=0.0)
    pb.on_tick(Tick(ts_utc=T0, bid=2_400.0, ask=2_400.2))
    return _engine(tmp_path, pb), pb


def test_adopting_the_position_of_a_sending_row_marks_it_filled_and_saves(tmp_path):
    eng, pb = _start(tmp_path)
    cid, pid = _send_that_raises(eng, pb)
    eng._reconcile(T0)
    assert pid in eng.open
    rec = eng._orders[cid]
    assert rec.status == "filled" and rec.position_id == pid                  # (a)
    restarted = _engine(tmp_path, pb)
    assert restarted._orders[cid].status == "filled"                          # saved to disk


def test_raise_adopt_tighten_restart_keeps_the_trade_and_never_loosens_the_stop(tmp_path):
    eng, pb = _start(tmp_path)
    cid, pid = _send_that_raises(eng, pb)
    eng._reconcile(T0)
    tr = eng.open[pid]
    tr.bars_held, tr.scaled = 7, True
    # the weekend rule (or a trail) tightens the stop well above the entry stop of 2390
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(hours=2), bid=2_420.0, ask=2_420.2))
    tr.sl = 2_410.0
    pb.modify(pid, 2_410.0, tr.tp)
    eng._save_orders()
    # the row was the bug's trigger: simulate a table written before (a) existed, still `sending`
    eng._orders[cid].status, eng._orders[cid].position_id = "sending", None
    eng._save_orders()
    pb.modifies.clear()
    again = _engine(tmp_path, pb)                                             # restart
    kept = again.open[pid]
    assert (kept.bars_held, kept.scaled, kept.sl) == (7, True, 2_410.0)       # (b)
    assert again._orders[cid].status == "filled" and again._orders[cid].position_id == pid
    # the exit policy runs with a trail that would sit below the broker's stop: no modify may loosen it
    kept.policy = ExitPolicy(trail_atr=1.0)
    kept.timeframe, kept.entry, kept.atr_usd = "1h", 2_400.2, 10.0
    kept.opened_utc = T0
    kept.sl = 2_395.0                                                          # even if the engine's own copy is loose
    dec = pd.DataFrame({"visible_at": [T0 + pd.Timedelta(hours=1)], "bid_high": [2_412.0], "ask_low": [2_399.0]})
    again._manage_policies("1h", dec, T0 + pd.Timedelta(hours=1))             # trail = 2412 - 10 = 2402 < 2410
    assert all(sl is not None and sl >= 2_410.0 for _, sl in pb.modifies), pb.modifies     # (c)
    assert next(p for p in pb.positions() if p.position_id == pid).sl == 2_410.0


def test_the_policy_still_tightens_past_the_broker_stop(tmp_path):
    eng, pb = _start(tmp_path)
    cid, pid = _send_that_raises(eng, pb)
    eng._reconcile(T0)
    tr = eng.open[pid]
    tr.policy, tr.timeframe, tr.entry, tr.atr_usd, tr.opened_utc = ExitPolicy(trail_atr=1.0), "1h", 2_400.2, 10.0, T0
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(hours=1), bid=2_428.0, ask=2_428.2))   # below the 2430 target
    dec = pd.DataFrame({"visible_at": [T0 + pd.Timedelta(hours=1)], "bid_high": [2_428.0], "ask_low": [2_420.0]})
    eng._manage_policies("1h", dec, T0 + pd.Timedelta(hours=1))               # trail 2418 > broker 2390
    assert pb.modifies == [(pid, 2_418.0)]


def test_a_short_never_gets_a_higher_stop_than_the_brokers(tmp_path):
    eng, pb = _start(tmp_path)
    pb.raise_next = False
    pid = pb.place_order(OrderIntent(client_order_id="s", symbol="XAUUSD", side=-1, lots=0.1, sl=2_390.0, tp=2_300.0,
                                     magic=engine_magic.family(eng.cfg.magic_base, "trend"))).position_id
    assert pid is not None
    eng._reconcile(T0)
    tr = eng.open[pid]
    tr.policy, tr.timeframe, tr.entry, tr.atr_usd, tr.opened_utc = ExitPolicy(trail_atr=1.0), "1h", 2_400.0, 10.0, T0
    tr.sl = 2_420.0                                                            # engine copy looser than the broker's
    dec = pd.DataFrame({"visible_at": [T0 + pd.Timedelta(hours=1)], "bid_high": [2_400.0], "ask_low": [2_385.0]})
    pb.modifies.clear()
    eng._manage_policies("1h", dec, T0 + pd.Timedelta(hours=1))               # trail 2395 > broker 2390 (looser)
    assert pb.modifies == []


def test_a_sending_row_older_than_a_minute_is_resolved_in_process(tmp_path):
    eng, pb = _start(tmp_path)
    cid, pid = _send_that_raises(eng, pb)
    rec = eng._orders[cid]
    eng._reconcile(T0)                                                        # adopted (a) already marks it
    assert rec.status == "filled"
    # a row whose order never reached the broker: no position and no deal -> handed to the look-up once it is a minute
    # old (BACKLOG 72 trading-safety review: `ambiguous`, unfilled only after two clean looks 120 s apart)
    eng._orders_record("icm-demo-ghost", agent_id="x", side=1, magic=1, lots=0.1, max_bars=4, sl=1.0, tp=2.0,
                       ts=pd.Timestamp.now("UTC"))
    eng._reconcile(T0)
    assert eng._orders["icm-demo-ghost"].status == "sending"                  # too young: maybe still in flight
    eng._orders["icm-demo-ghost"].ts_utc = pd.Timestamp.now("UTC") - pd.Timedelta(seconds=90)
    eng._reconcile(T0)
    assert eng._orders["icm-demo-ghost"].status == "ambiguous"                # (d), then the look-up decides
    assert "icm-demo-ghost" in eng.sent_ids                                    # never re-sent


def test_a_stale_sending_row_with_a_position_is_adopted_with_its_entry_terms(tmp_path):
    eng, pb = _start(tmp_path)
    cid, pid = _send_that_raises(eng, pb)
    eng._orders[cid].ts_utc = pd.Timestamp.now("UTC") - pd.Timedelta(seconds=90)
    eng._resolve_stale_sending({p.position_id: p for p in pb.positions()})
    assert pid in eng.open and eng.open[pid].initial_sl == 2_390.0 and eng.open[pid].client_order_id == cid
    assert eng._orders[cid].status == "filled" and eng._orders[cid].position_id == pid

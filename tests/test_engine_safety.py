"""Engine safety rules from the design's Order lifecycle, Reconciliation and Risk management sections: restart-safe
order ids, reconciliation of stops, weekend/rollover windows, the combined exposure cap, the supervisor's combined
size-down, feature-version checks, stale data and the 12% re-arm conditions."""
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.base import schema_skew
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Proposal
from goldbot.telegram.bus import ApprovalBus

OWNER = 111
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")       # a Wednesday


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _engine(tmp_path: Path, pb: PaperBroker | None = None, **cfg: Any) -> tuple[Engine, PaperBroker]:
    if pb is None:
        pb = PaperBroker(equity=10_000)
        pb.on_tick(_tick(T0))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER, **cfg),
                 pb, [], {}, ApprovalCenter({OWNER}))
    return eng, pb


def _prop(pid: str) -> Proposal:
    return Proposal(proposal_id=pid, account_id="icm-demo", agent_id=SPECIALISTS["session_open"]().agent_id, side=1,
                    lots=0.1, entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])


class _CrashAfterSend(PaperBroker):
    """The order reaches the broker and fills, then the process dies before the result is recorded."""
    def place_order(self, intent: OrderIntent) -> OrderResult:
        super().place_order(intent)
        raise RuntimeError("terminal connection lost")


# ---------------------------------------------------------------------------------------------- 1. restart safety
def test_sent_ids_and_open_trades_survive_a_restart_and_are_never_re_sent(tmp_path):
    eng, pb = _engine(tmp_path)
    agent = SPECIALISTS["session_open"]()
    eng._execute(_prop("icm-demo-1-a"), agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    [pos] = pb.positions()
    saved = json.loads((tmp_path / "orders_icm-demo.json").read_text())
    assert saved["sent"]["icm-demo-1-a"]["status"] == "filled" and not list(tmp_path.glob("*.tmp"))
    again, _ = _engine(tmp_path, pb)                                   # restart against the same broker
    assert "icm-demo-1-a" in again.sent_ids
    assert again.open[pos.position_id].agent_id == agent.agent_id      # adopted as its own trade, not an orphan
    again._execute(_prop("icm-demo-1-a"), agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    assert len(pb.positions()) == 1                                    # the same id is never sent twice
    assert any(d.get("action") == "duplicate_suppressed" for d in again.decisions)


def test_a_restart_after_an_unconfirmed_send_adopts_the_fill_from_the_broker(tmp_path):
    pb = _CrashAfterSend(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    agent = SPECIALISTS["session_open"]()
    try:
        eng._execute(_prop("icm-demo-2-a"), agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    except RuntimeError:
        pass
    saved = json.loads((tmp_path / "orders_icm-demo.json").read_text())
    assert saved["sent"]["icm-demo-2-a"]["status"] == "sending"        # written before order_send
    [pos] = pb.positions()
    again, _ = _engine(tmp_path, pb)
    assert again.open[pos.position_id].agent_id == agent.agent_id
    assert again._orders["icm-demo-2-a"].status == "filled"
    assert any(d.get("action") == "reconcile_adopt_sent" for d in again.decisions)


def test_a_restart_after_a_rollback_loads_an_orders_file_a_newer_release_wrote(tmp_path):
    """ADR 0010 (rollback = restart, X7): fields a newer engine added to a sent order, an open trade or a queued
    closed-trade record are ignored. The ids, the open trade and the queued record survive, so the older engine
    starts, manages the exit and never re-sends."""
    eng, pb = _engine(tmp_path)
    agent = SPECIALISTS["session_open"]()
    eng._execute(_prop("icm-demo-1-a"), agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    [pos] = pb.positions()
    path = tmp_path / "orders_icm-demo.json"
    saved = json.loads(path.read_text())
    new = {"future_field": {"from": "a newer release"}}
    saved["sent"]["icm-demo-1-a"].update(new)
    trade = {**saved["open"][str(pos.position_id)], **new}
    saved["open"][str(pos.position_id)] = trade
    saved["closing"] = [{"position_id": 999, "trade": trade, "reason": "stop", "at": T0.isoformat(), **new}]
    path.write_text(json.dumps(saved))
    again, _ = _engine(tmp_path, pb)
    assert "icm-demo-1-a" in again.sent_ids and again._orders["icm-demo-1-a"].status == "filled"
    assert again.open[pos.position_id].agent_id == agent.agent_id
    assert again._closes == [] and 999 in {t.position_id for t in load_closed_trades(tmp_path)[0]}   # written on start
    again._execute(_prop("icm-demo-1-a"), agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    assert len(pb.positions()) == 1                                    # never re-sent


def test_a_restart_warns_about_an_orders_file_table_this_release_does_not_know(tmp_path, caplog):
    """ADR 0010: a top-level table a newer release added to the orders file (it could hold client ids or broker
    orders) is not read by this release, so the start says so instead of dropping it silently."""
    eng, pb = _engine(tmp_path)
    eng._execute(_prop("icm-demo-1-a"), SPECIALISTS["session_open"](), 0.1, 2396.0, 2406.0, requested=2400.2)
    path = tmp_path / "orders_icm-demo.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "brackets": {"x": 1}}))
    caplog.set_level(logging.WARNING, logger="goldbot.base")
    again, _ = _engine(tmp_path, pb)
    assert "icm-demo-1-a" in again.sent_ids
    assert [r for r in caplog.records if "brackets" in r.getMessage()]
    assert schema_skew()[("orders_<account>.json", "orders file", ("brackets",))] >= 1


def test_a_restart_archives_an_expired_proposal_a_newer_release_wrote(tmp_path):
    """ADR 0010: an expired proposal with a field this release does not know is still archived on start, so the
    dashboard and Telegram stop listing it."""
    bus = ApprovalBus(tmp_path)
    old = _prop("icm-demo-9-a").model_copy(update={"created": time.time() - 600})
    (bus.pending_dir / "icm-demo-9-a.json").write_text(json.dumps({**json.loads(old.model_dump_json()), "future": 1}))
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
           pb, [], {}, ApprovalCenter({OWNER}, bus=bus))
    assert not (bus.pending_dir / "icm-demo-9-a.json").exists() and (bus.done_dir / "icm-demo-9-a.json").exists()


def test_a_restart_after_an_unfilled_send_marks_it_unfilled_and_keeps_the_id(tmp_path):
    eng, pb = _engine(tmp_path)
    eng._orders_record("icm-demo-3-a", agent_id="x", side=1, magic=260150, lots=0.1, max_bars=4,
                       sl=2396.0, tp=2406.0, ts=pd.Timestamp.now("UTC"))   # crashed before order_send returned
    again, _ = _engine(tmp_path, pb)
    assert again._orders["icm-demo-3-a"].status == "unfilled" and "icm-demo-3-a" in again.sent_ids
    assert again.open == {}


# ---------------------------------------------------------------------------------------------- 2. reconciliation
def _strip(pb: PaperBroker, pid: int, sl: float | None = None, tp: float | None = None) -> None:
    pos = pb._positions[pid]
    pos.sl, pos.tp = sl, tp


def test_reconciliation_protects_orphans_and_reinstates_missing_or_loosened_stops(tmp_path):
    eng, pb = _engine(tmp_path)
    eng._last_atr = 4.0
    agent = SPECIALISTS["session_open"]()
    eng._execute(_prop("icm-demo-4-a"), agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    [mine] = [p.position_id for p in pb.positions()]
    orphan = pb.place_order(OrderIntent(client_order_id="o", symbol="XAUUSD", side=-1, lots=0.1, sl=1.0, tp=1.0,
                                        magic=260150, comment="o")).position_id
    manual = pb.place_order(OrderIntent(client_order_id="m", symbol="XAUUSD", side=1, lots=0.1, sl=1.0, tp=1.0,
                                        magic=0, comment="m")).position_id
    assert orphan is not None and manual is not None
    _strip(pb, mine)                                   # stop and target lost at the broker
    _strip(pb, orphan)
    _strip(pb, manual)
    eng._reconcile(T0)
    pos = {p.position_id: p for p in pb.positions()}
    assert (pos[mine].sl, pos[mine].tp) == (2396.0, 2406.0)                 # reinstated from the order record
    assert pos[orphan].sl == round(pos[orphan].open_price + 1.5 * 4.0, 2)   # orphan short: 1.5 ATR stop above entry
    assert eng.open[orphan].agent_id == "orphan"
    assert pos[manual].sl is None and manual not in eng.open                # unknown magic: left alone ...
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert st["foreign_positions"] == [manual]                              # ... but listed
    _strip(pb, mine, sl=2380.0, tp=2406.0)             # loosened stop
    eng._reconcile(T0)
    assert pb._positions[mine].sl == 2396.0
    acts = [d["action"] for d in eng.decisions]
    assert acts.count("reinstate_stops") == 2 and "adopt_orphan" in acts and "orphan_stop" in acts


def test_reconciliation_runs_on_start_and_every_30_seconds_of_tick_time(tmp_path):
    eng, pb = _engine(tmp_path)
    eng._last_atr = 4.0
    eng.on_tick(_tick(T0 + pd.Timedelta(seconds=1)))                        # startup pass
    pid = pb.place_order(OrderIntent(client_order_id="o", symbol="XAUUSD", side=1, lots=0.1, sl=1.0, tp=1.0, magic=260150,
                                     comment="o")).position_id
    assert pid is not None
    _strip(pb, pid)
    eng.on_tick(_tick(T0 + pd.Timedelta(seconds=20)))
    assert pb._positions[pid].sl is None
    eng.on_tick(_tick(T0 + pd.Timedelta(seconds=32)))                       # 31 s after the last pass
    assert pb._positions[pid].sl == round(pb._positions[pid].open_price - 6.0, 2)


# ---------------------------------------------------------------------------------------------- 3. weekend and rollover
def test_no_entries_within_five_minutes_of_server_midnight(tmp_path):
    eng, pb = _engine(tmp_path)                        # server clock Europe/Athens: UTC+2 in early March
    for utc, blocked in (("2025-03-05 21:54", False), ("2025-03-05 21:56", True), ("2025-03-05 22:04", True),
                         ("2025-03-05 22:06", False), ("2025-03-05 10:00", False)):
        eng._refresh_account(_tick(pd.Timestamp(utc, tz="UTC")))
        assert eng.state.in_rollover is blocked, utc
        assert ("rollover" in eng.gate.check(_intent(), eng.state).reasons) is blocked


def test_friday_2130_server_closes_losers_tightens_winners_and_blocks_entries(tmp_path):
    fri = pd.Timestamp("2025-03-07 19:00", tz="UTC")    # Friday 21:00 server
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(fri, 2400.0))
    eng, _ = _engine(tmp_path, pb)
    win = pb.place_order(OrderIntent(client_order_id="w", symbol="XAUUSD", side=1, lots=0.1, sl=2390.0, tp=2450.0,
                                     magic=260150, comment="w")).position_id
    lose = pb.place_order(OrderIntent(client_order_id="l", symbol="XAUUSD", side=-1, lots=0.1, sl=2450.0, tp=2350.0,
                                      magic=260150, comment="l")).position_id
    manual = pb.place_order(OrderIntent(client_order_id="m", symbol="XAUUSD", side=-1, lots=0.1, sl=2450.0, tp=2350.0,
                                        magic=0, comment="m")).position_id
    pb.on_tick(_tick(fri + pd.Timedelta(minutes=29), 2410.0))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert len(pb.positions()) == 3 and not eng.state.weekend
    pb.on_tick(_tick(fri + pd.Timedelta(minutes=31), 2410.0))           # 21:31 server
    eng._refresh_account(pb.last_tick("XAUUSD"))
    left = {p.position_id: p for p in pb.positions()}
    assert lose not in left and win in left and manual in left            # only this engine's losers are closed
    assert left[win].sl == round(left[win].open_price + 0.5 * (2410.0 - left[win].open_price), 2)
    assert eng.state.weekend and "weekend" in eng.gate.check(_intent(), eng.state).reasons
    first_sl = left[win].sl
    pb.on_tick(_tick(fri + pd.Timedelta(minutes=40), 2420.0))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert pb._positions[win].sl == first_sl                               # applied once per Friday
    eng._refresh_account(_tick(pd.Timestamp("2025-03-10 08:00", tz="UTC")))  # Monday
    assert not eng.state.weekend


def _intent() -> Any:
    from goldbot.risk import Intent
    return Intent(agent_id="session_open-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                  multiplier=1.0, price=2400.2)


# ---------------------------------------------------------------------------------------------- 4/5. combined exposure and size-down
def _engine_file(tmp_path: Path, account: str, *, lots: float, equity: float = 10_000.0, px: float = 2400.0) -> None:
    import time
    (tmp_path / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "ts": time.time(), "equity": equity, "day_start_equity": equity, "week_start_equity": equity,
        "balance_closed_hwm": equity, "open_lots": lots, "open_notional": lots * 100 * px,
        "currency": "USD"}))       # the paper engine's currency: mixed currencies halt (BACKLOG 28)


def _state(**kw: Any) -> Any:
    from goldbot.risk import AccountState
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                                open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=20)
    return AccountState(**(base | kw))


def test_supervisor_publishes_the_combined_exposure_of_both_accounts(tmp_path):
    from goldbot.risk.supervisor import Supervisor
    _engine_file(tmp_path, "icm-demo", lots=0.5)
    _engine_file(tmp_path, "vantage-demo", lots=1.2, equity=20_000)
    st = Supervisor(tmp_path).evaluate()
    assert st["combined_open_lots"] == 1.7 and st["combined_open_notional"] == 1.7 * 100 * 2400
    assert st["exposure"]["vantage-demo"] == {"lots": 1.2, "notional": 1.2 * 100 * 2400, "equity": 20_000}


def test_the_gate_blocks_entries_that_would_breach_the_combined_exposure_cap():
    from goldbot.risk import RiskGate
    gate = RiskGate()
    rich = dict(equity=1_000_000, balance_closed_hwm=1_000_000, day_start_equity=1_000_000, week_start_equity=1_000_000)
    big = _intent()
    first = gate.check(big, _state(**rich))
    assert first.allowed and first.lots == 2.0                                          # volume_max
    near = _state(**rich, other_lots=3.0 - first.lots + 0.01, other_notional=0.0, other_equity=0.0)
    assert "combined_exposure_cap" in gate.check(big, near).reasons                      # 3 lots across accounts
    # 30% of combined equity at the 1:20 cap: $20k combined -> $120k notional
    two = _state(other_lots=0.4, other_notional=0.4 * 100 * 2400, other_equity=10_000)
    assert "combined_exposure_cap" in gate.check(_intent(), two).reasons
    assert gate.check(_intent(), _state(other_lots=0.1, other_notional=0.1 * 100 * 2400, other_equity=10_000)).allowed


def test_engine_reads_other_accounts_exposure_and_the_combined_size_down_from_the_supervisor(tmp_path):
    from goldbot.risk.supervisor import Supervisor
    eng, pb = _engine(tmp_path, halt_checks=True)
    _bar_visible_at(eng, T0)
    _engine_file(tmp_path, "vantage-demo", lots=0.4, equity=10_000)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    eng._write_state()
    Supervisor(tmp_path).evaluate()
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.other_lots == 0.4 and eng.state.other_equity == 10_000      # its own entry is not counted twice
    assert "combined_exposure_cap" in eng.gate.check(_intent(), eng.state).reasons
    # the supervisor's 8% combined drawdown flag halves the size
    _engine_file(tmp_path, "vantage-demo", lots=0.0, equity=10_000)
    assert not Supervisor(tmp_path).evaluate()["size_down"]
    eng._refresh_account(pb.last_tick("XAUUSD"))
    full = eng.gate.check(_intent(), eng.state)
    assert full.allowed and not eng.state.combined_size_down
    other = json.loads((tmp_path / "engine_vantage-demo.json").read_text())
    (tmp_path / "engine_vantage-demo.json").write_text(json.dumps({**other, "equity": 8_000, "day_start_equity": 8_000, "week_start_equity": 8_000}))   # drawdown 10%, no day loss
    assert Supervisor(tmp_path).evaluate()["size_down"]
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.combined_size_down and eng.state.stage.value == "normal"
    half = eng.gate.check(_intent(), eng.state)
    assert half.allowed and half.lots < full.lots and half.lots <= full.lots / 2 + 0.01


# ---------------------------------------------------------------------------------------------- 6. feature version
def _always_long() -> Any:
    base = type(SPECIALISTS["session_open"]())

    class AlwaysLong(base):  # type: ignore[valid-type,misc]
        def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
            return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})
    return AlwaysLong()


def _decide_once(tmp_path: Path, model: Any) -> tuple[Engine, Any, list[dict]]:
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.engine import ConstantModel  # noqa: F401  (model type under test)
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    last = pd.Timestamp(ticks["ts_utc"].iloc[-1])
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(last, float(ticks["bid"].iloc[-1])))
    agent = _always_long()
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
                 pb, [agent], {agent.family: model}, ApprovalCenter({OWNER}))
    eng.bars_1m = ticks_to_1m(ticks)
    close_ts = last.floor("15min")
    fr = eng._frame(eng.bars_1m[eng.bars_1m["visible_at"] <= close_ts], "15m", close_ts)
    assert fr is not None
    eng._refresh_account(pb.last_tick("XAUUSD"))
    return eng, fr, eng._decide("15m", fr, [agent], {agent.family: 1.0}, close_ts, pb.last_tick("XAUUSD"))


def test_a_model_never_scores_a_frame_of_another_feature_version(tmp_path):
    from goldbot.engine import ConstantModel
    from goldbot.features.registry import feature_version
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
    current = feature_version(DEFAULT_FEATURE_NAMES)
    eng, fr, out = _decide_once(tmp_path / "old", ConstantModel(p=0.9, feature_version="f-0000000000"))
    assert fr.X.attrs["feature_version"] == current                      # the merged frame keeps its version
    [d] = out
    assert d["action"] == "feature_version_mismatch" and not eng.pending
    detail = [x for x in eng.decisions if x["action"] == "feature_version_mismatch"][0]
    assert detail["model_feature_version"] == "f-0000000000" and detail["frame_feature_version"] == current
    _, _, ok = _decide_once(tmp_path / "same", ConstantModel(p=0.9, feature_version=current))
    assert ok and ok[0]["action"] != "feature_version_mismatch"


# ---------------------------------------------------------------------------------------------- 7. stale data
def _bar_visible_at(eng: Engine, visible_at: pd.Timestamp) -> None:
    eng.bars_1m = pd.DataFrame({"ts_utc": [visible_at - pd.Timedelta(minutes=1)], "visible_at": [visible_at]})


def test_entries_need_a_last_bar_less_than_one_period_old(tmp_path):
    eng, pb = _engine(tmp_path)
    eng._refresh_account(_tick(T0))
    assert eng.state.stale_bars and "stale_data" in eng.gate.check(_intent(), eng.state).reasons   # no bars at all
    _bar_visible_at(eng, T0 - pd.Timedelta(minutes=1))
    eng._refresh_account(_tick(T0 + pd.Timedelta(minutes=13)))                # 14 min old on a 15m decision clock
    assert not eng.state.stale_bars
    eng._refresh_account(_tick(T0 + pd.Timedelta(minutes=15)))                # 16 min old
    assert eng.state.stale_bars and "stale_data" in eng.gate.check(_intent(), eng.state).reasons


def test_entries_resume_only_after_sixty_seconds_of_healthy_data(tmp_path):
    from goldbot.data.quality import DQEvent
    eng, pb = _engine(tmp_path)
    _bar_visible_at(eng, T0)
    eng._dq_pending = [DQEvent(ts_utc=T0, check="bid_gt_ask", severity="error", detail="x")]
    eng._refresh_account(_tick(T0 + pd.Timedelta(seconds=5)))
    assert eng.state.dq_error
    eng._dq_pending = []                                                      # the error clears ...
    eng._refresh_account(_tick(T0 + pd.Timedelta(seconds=35)))
    assert not eng.state.dq_error and eng.state.data_recovering                # ... but 30 s is not enough
    assert "data_recovering" in eng.gate.check(_intent(), eng.state).reasons
    eng._refresh_account(_tick(T0 + pd.Timedelta(seconds=66)))                # 61 s of healthy data
    assert not eng.state.data_recovering
    assert eng.gate.check(_intent(), eng.state).allowed


# ---------------------------------------------------------------------------------------------- 8. re-arm after the 12% halt
def _shadow_record(tmp_path: Path, closed: list[tuple[str, float]]) -> None:
    from goldbot.engine.shadow import ShadowBook, ShadowTrade
    book = ShadowBook(tmp_path)
    book.track("session_open-v1", pd.Timestamp("2025-03-01", tz="UTC"))
    book.books["session_open-v1"].closed = [
        ShadowTrade(version="session_open-v1", agent_id="a", side=1, entry_ts=pd.Timestamp(ts, tz="UTC") - pd.Timedelta(hours=1),
                    entry=2400.0, stop=2390.0, target=2420.0, max_bars=4, p=0.6, exit_ts=pd.Timestamp(ts, tz="UTC"),
                    exit=2400.0 * (1 + r), barrier="target" if r > 0 else "stop", ret=r) for ts, r in closed]
    book.save(pd.Timestamp(closed[-1][0], tz="UTC") if closed else pd.Timestamp("2025-03-01", tz="UTC"))


def test_rearm_after_the_kill_switch_needs_ten_trading_days_of_positive_shadow_then_thirty_days_propose_only(tmp_path):
    from goldbot.risk.gate import Stage
    from goldbot.telegram.bus import ApprovalBus
    bus = ApprovalBus(tmp_path)
    halt_t = pd.Timestamp("2025-03-03 09:00", tz="UTC")                      # Monday
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(halt_t, 2400.0))

    def start() -> Engine:
        return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER,
                                   approval_mode="auto"), pb, [], {}, ApprovalCenter({OWNER}, bus=bus))
    eng = start()
    eng._refresh_account(pb.last_tick("XAUUSD"))
    pb.place_order(OrderIntent(client_order_id="big", symbol="XAUUSD", side=1, lots=1.0, sl=2300.0, tp=2500.0, magic=260150,
                               comment="big"))
    pb.on_tick(_tick(halt_t + pd.Timedelta(minutes=1), 2387.0))              # 13% drawdown
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.stage == Stage.HALTED and eng.cfg.approval_mode == "propose"

    def rearm_at(ts: str) -> dict:
        bus.owner_rearm(by="dashboard:o@x.io")
        eng.on_tick(_tick(pd.Timestamp(ts, tz="UTC"), 2387.0))
        return eng.decisions[-1]

    d = rearm_at("2025-03-04 09:00")                                         # the next day: too early
    assert eng.state.stage == Stage.HALTED and d["action"] == "rearm_refused"
    assert "10 trading days" in d["reason"] and "1 so far" in d["reason"]
    _shadow_record(tmp_path, [("2025-03-05 10:00", 0.004), ("2025-03-12 10:00", -0.006)])
    d = rearm_at("2025-03-17 09:00")                                         # 10 trading days, shadow negative
    assert eng.state.stage == Stage.HALTED and d["action"] == "rearm_refused" and "positive" in d["reason"]
    _shadow_record(tmp_path, [("2025-03-02 10:00", 0.05),                    # before the halt: does not count
                              ("2025-03-05 10:00", 0.004), ("2025-03-12 10:00", 0.002)])
    eng = start()                                                            # the halt and its start survive a restart
    d = rearm_at("2025-03-17 09:05")
    assert eng.state.stage == Stage.NORMAL and d["action"] == "rearm"
    # then 30 days propose-and-approve: auto is not honoured, across a restart too
    eng = start()
    eng._refresh_account(_tick(pd.Timestamp("2025-04-10 09:00", tz="UTC"), 2387.0))
    assert eng.cfg.approval_mode == "propose"
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert pd.Timestamp(st["propose_only_until"]) == pd.Timestamp("2025-04-16 09:05", tz="UTC")
    eng = start()
    eng._refresh_account(_tick(pd.Timestamp("2025-04-17 09:00", tz="UTC"), 2387.0))
    assert eng.cfg.approval_mode == "auto"                                    # the lock has run out; mode as configured


# ---------------------------------------------------------------------------------------------- BACKLOG 66
CEILING_MSG = "bridge_notional_ceiling: notional 7500.00 USD > 6 x equity 1000.00 (0 lots open + 0.03 on XAUUSD)"
UNREADABLE_MSG = "bridge_notional_ceiling: unreadable:account (RuntimeError)"
CEILING_WHY = "(open gold would be worth more than 6× equity)"
UNREADABLE_WHY = "(it couldn't read the account figures it checks)"
TELEGRAM_CEILING = ("🛑 NOT PLACED: approved, but the MT5 box's safety limit refused it (open gold would be worth more "
                    "than 6× equity). Nothing was sent.")
TELEGRAM_UNREADABLE = ("🛑 NOT PLACED: approved, but the MT5 box couldn't read the account figures it checks, so it "
                       "refused to be safe. Nothing was sent.")


class _BridgeRefuses(PaperBroker):
    """The bridge's notional ceiling refuses the first `refusals` orders (a definite reject, 10006); `message` is what
    it says. Later orders fill as on paper. `calls` counts every order that reached the broker."""

    def __init__(self, refusals: int = 1, message: str = CEILING_MSG, **kw: Any) -> None:
        super().__init__(**kw)
        self.refusals, self.message, self.calls = refusals, message, 0

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.calls += 1
        if self.calls <= self.refusals:
            return OrderResult(ok=False, retcode=10006, order_id=None, position_id=None, filled_lots=0.0, price=None,
                               message=self.message)
        return super().place_order(intent)


def _notices(tmp_path: Path) -> list[str]:
    f = tmp_path / "agent_runs.jsonl"
    return [json.loads(x)["detail"] for x in f.read_text().splitlines()] if f.exists() else []


def test_a_bridge_ceiling_refusal_reaches_the_journal_with_its_message(tmp_path):
    pb = _BridgeRefuses(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    res = eng._execute(_prop("icm-demo-66-a"), SPECIALISTS["session_open"](), 0.03, 2396.0, 2406.0, requested=2400.2)
    assert res is not None and res.retcode == 10006 and not pb.positions()
    [order] = [d for d in eng.decisions if d.get("action") == "order"]
    assert order["retcode"] == 10006 and order["ok"] is False and order["message"] == CEILING_MSG
    assert eng._orders["icm-demo-66-a"].status == "rejected"


def test_a_bridge_ceiling_refusal_alerts_and_pauses_auto(tmp_path):
    from goldbot.risk import RiskLimits
    from tests.test_auto_same_close import BASE, _actions, _run, _two
    pb = _BridgeRefuses(equity=10_000, adverse_slip_points=0.0)
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**BASE), pb=pb,
                       center=ApprovalCenter({111}, bus=ApprovalBus(tmp_path)))
    # the refused entry is journalled as a definite reject, and no further auto entry is sent at this bar close
    assert _actions(dec) == ["auto_rejected:10006", "auto_paused:bridge_refused"], _actions(dec)
    assert pb.calls == 1 and not pb.positions()
    assert eng._auto_paused is not None and eng._auto_paused[1] == "bridge_refused"
    [notice] = _notices(tmp_path)                                   # relayed to the owner on Telegram, in plain words
    assert notice.startswith("Order not placed: the MT5 box's safety limit refused LONG ")
    assert f"lots XAUUSD on icm-demo {CEILING_WHY}. Nothing was sent; open trades keep their stops. Automatic entries " \
           "pause until the next bar. If this repeats, the trade sizing and the MT5 box's limit disagree: ask Claude " \
           "to check." in notice, notice
    assert "bridge_notional_ceiling" not in notice and "icm-demo-" not in notice


@pytest.mark.parametrize(("message", "why"), [(CEILING_MSG, CEILING_WHY), (UNREADABLE_MSG, UNREADABLE_WHY)])
def test_the_owner_notice_names_the_trade_in_plain_words_never_the_raw_refusal_or_the_proposal_id(tmp_path, message,
                                                                                                   why):
    pb = _BridgeRefuses(message=message, equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    eng.center.bus = ApprovalBus(tmp_path)
    eng.cfg.account_id = "icm-demo-51234567"                         # a config name that embeds a login
    pid = "icm-demo-51234567-66-c"
    eng._execute(_prop(pid).model_copy(update={"side": -1}), SPECIALISTS["session_open"](), 0.03, 2404.0, 2394.0,
                 requested=2400.0)
    [notice] = _notices(tmp_path)
    assert notice == (f"Order not placed: the MT5 box's safety limit refused SHORT 0.03 lots XAUUSD on icm-demo-…67 "
                      f"{why}. Nothing was sent; open trades keep their stops. If this repeats, the trade sizing and "
                      f"the MT5 box's limit disagree: ask Claude to check.")
    assert "bridge_notional_ceiling" not in notice and pid not in notice and "51234567" not in notice
    [journal] = [d for d in eng.decisions if d.get("action") == "bridge_refused"]
    assert journal["message"] == message and journal["proposal"] == pid       # the raw text stays in the journal


@pytest.mark.parametrize(("code", "text"), [("bridge_notional_ceiling", TELEGRAM_CEILING),
                                            ("bridge_notional_ceiling_unreadable", TELEGRAM_UNREADABLE)])
def test_the_telegram_card_words_each_bridge_refusal_without_its_code(code, text):
    from goldbot.telegram.bot import outcome_text
    assert outcome_text(f"GATE_REFUSED: {code}") == text


def test_a_real_broker_reject_with_the_same_retcode_neither_alerts_nor_pauses_auto(tmp_path):
    from goldbot.risk import RiskLimits
    from tests.test_auto_same_close import BASE, _actions, _run, _two
    pb = _BridgeRefuses(message="Request rejected", equity=10_000, adverse_slip_points=0.0)
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**BASE), pb=pb,
                       center=ApprovalCenter({111}, bus=ApprovalBus(tmp_path)))
    assert _actions(dec) == ["auto_rejected:10006", "executed:auto"], _actions(dec)
    assert _notices(tmp_path) == []


@pytest.mark.parametrize(("message", "code", "text"), [
    (CEILING_MSG, "bridge_notional_ceiling", TELEGRAM_CEILING),
    (UNREADABLE_MSG, "bridge_notional_ceiling_unreadable", TELEGRAM_UNREADABLE),
])
def test_a_bridge_ceiling_refusal_at_approval_shows_refused_by_the_bridge(tmp_path, message, code, text):
    from goldbot.telegram.bot import outcome_text
    from goldbot.telegram.outbox import Outbox
    from tests.test_approved_card_cap import _approve, _card
    from tests.test_engine_clock import WED, _intent
    from tests.test_engine_clock import _engine as _clock_engine
    from tests.test_engine_clock import _tick as _clock_tick
    pb = _BridgeRefuses(message=message, equity=10_000)
    pb.on_tick(_clock_tick(WED))
    eng, _, _ = _clock_engine(tmp_path, pb=pb)
    bus = eng.center.bus = ApprovalBus(tmp_path)
    eng._refresh_account(_clock_tick(WED))
    prop = _card(eng, _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0)))
    bus.publish(prop)
    outbox = Outbox(tmp_path, bus)
    outbox.mark_sent(prop.proposal_id, [(1, 2)])
    _approve(eng, prop)
    assert pb.calls == 1 and not pb.positions()
    archived = bus.get(prop.proposal_id)
    assert archived is not None and archived.gate_refusal == [code]
    [(pid, outcome, _)] = outbox.finished()
    assert pid == prop.proposal_id and outcome_text(outcome) == text
    [notice] = _notices(tmp_path)
    assert notice.startswith("Order not placed: the MT5 box's safety limit refused LONG ") and "Automatic" not in notice
    assert any(d.get("action") == "bridge_refused" for d in eng.decisions)


def test_a_bridge_refused_id_is_never_re_sent_after_a_restart(tmp_path):
    pb = _BridgeRefuses(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    agent = SPECIALISTS["session_open"]()
    eng._execute(_prop("icm-demo-66-b"), agent, 0.03, 2396.0, 2406.0, requested=2400.2)
    again, _ = _engine(tmp_path, pb)                                   # restart against the same terminal
    assert "icm-demo-66-b" in again.sent_ids and again._orders["icm-demo-66-b"].status == "rejected"
    assert again._execute(_prop("icm-demo-66-b"), agent, 0.03, 2396.0, 2406.0, requested=2400.2) is None
    assert pb.calls == 1 and not pb.positions()                        # the refused id never reaches the bridge again


# ------------------------------------------------------------------- order not placed (docs/ux/order-not-placed.md)
class _BrokerRefuses(_BridgeRefuses):
    """The first order comes back with `retcode` and `message` (not a fill); later ones fill as on paper."""

    def __init__(self, retcode: int, message: str = "Request rejected", **kw: Any) -> None:
        super().__init__(message=message, **kw)
        self.retcode = retcode

    def place_order(self, intent: OrderIntent) -> OrderResult:
        res = super().place_order(intent)
        return res.model_copy(update={"retcode": self.retcode}) if self.calls == 1 else res


def _approved_through(tmp_path: Path, pb: PaperBroker, **cfg: Any) -> tuple[Any, Proposal, list[str]]:
    """Publish one card, approve it on the engine, and return (engine, the bus's copy of the card, the outcomes the
    Telegram outbox reports for it)."""
    from goldbot.telegram.outbox import Outbox
    from tests.test_approved_card_cap import _approve, _card
    from tests.test_engine_clock import WED, _intent
    from tests.test_engine_clock import _engine as _clock_engine
    from tests.test_engine_clock import _tick as _clock_tick
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": "raw"}))   # a demo terminal's class
    pb.on_tick(_clock_tick(WED))
    eng, _, _ = _clock_engine(tmp_path, pb=pb, **cfg)
    bus = eng.center.bus = ApprovalBus(tmp_path)
    eng._refresh_account(_clock_tick(WED))
    prop = _card(eng, _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0)))
    bus.publish(prop)
    outbox = Outbox(tmp_path, bus)
    outbox.mark_sent(prop.proposal_id, [(1, 2)])
    _approve(eng, prop)
    stored = bus.get(prop.proposal_id)
    assert stored is not None
    return eng, stored, [outcome for pid, outcome, _ in outbox.finished() if pid == prop.proposal_id]


def test_an_approved_order_the_broker_refuses_for_margin_is_archived_not_placed_with_one_notice(tmp_path):
    from goldbot.telegram.bot import outcome_text
    pb = _BrokerRefuses(10019, equity=10_000)
    eng, archived, [outcome] = _approved_through(tmp_path, pb)
    assert pb.calls == 1 and not pb.positions()
    assert archived.gate_refusal == ["broker_refused:10019"]
    assert outcome_text(outcome) == ("🛑 NOT PLACED: approved, but the broker refused it: there wasn't enough free "
                                     "margin. Nothing was sent.")
    lots = [d for d in eng.decisions if d.get("action") == "order"][0]["lots"] or archived.lots
    assert _notices(tmp_path) == [
        f"Order not placed: the broker refused LONG {lots:g} lots XAUUSD on icm-demo: there wasn't enough free margin. "
        f"Nothing was sent; open trades keep their stops. Check free margin on the Overview before approving the next "
        f"trade."]
    [order] = [d for d in eng.decisions if d.get("action") == "order"]
    assert order["retcode"] == 10019 and order["message"] == "Request rejected"     # the journal keeps both


def test_a_bridge_stale_refusal_at_approval_is_coded_stale_never_as_a_broker_10006(tmp_path):
    pb = _BrokerRefuses(10006, message="bridge_stale_order: 11.2 s old", equity=10_000)
    _, archived, _ = _approved_through(tmp_path, pb)
    assert archived.gate_refusal == ["bridge_stale_order"]
    [notice] = _notices(tmp_path)
    assert notice.startswith("Order not placed: the MT5 box refused LONG ") and "bridge_" not in notice


def test_a_broker_10027_on_a_demo_terminal_means_algo_trading_off_and_on_paper_a_duplicate(tmp_path):
    _, demo, _ = _approved_through(tmp_path / "demo", _BrokerRefuses(10027, equity=10_000), mode="demo")
    _, paper, _ = _approved_through(tmp_path / "paper", _BrokerRefuses(10027, equity=10_000), mode="paper")
    assert demo.gate_refusal == ["broker_refused:10027"]
    assert paper.gate_refusal == ["broker_refused:10027:paper"]
    [demo_notice] = _notices(tmp_path / "demo")
    [paper_notice] = _notices(tmp_path / "paper")
    assert demo_notice.endswith("Turn on Algo Trading in the MT5 terminal.")
    assert paper_notice.endswith("Nothing to do: the first send stands.") and "Nothing was sent" not in paper_notice


@pytest.mark.parametrize("retcode", [10008, 10010, 10012, -1])
def test_an_ambiguous_result_at_approval_is_not_marked_not_placed_and_sends_no_notice(tmp_path, retcode):
    pb = _BrokerRefuses(retcode, message="no answer", equity=10_000)
    _, archived, outcomes = _approved_through(tmp_path, pb)
    # BACKLOG 72: archived NOT CONFIRMED (tests/test_order_not_confirmed.py), never NOT PLACED: the order may exist
    assert archived.gate_refusal == ["not_confirmed"] and outcomes == ["GATE_REFUSED: not_confirmed"]
    assert _notices(tmp_path) == []


def test_a_fill_at_approval_is_not_marked_not_placed_and_sends_no_notice(tmp_path):
    pb = PaperBroker(equity=10_000)
    _, archived, outcomes = _approved_through(tmp_path, pb)
    assert pb.positions() and not archived.gate_refusal and not any(o.startswith("GATE_REFUSED") for o in outcomes)
    assert _notices(tmp_path) == []


def test_a_ceiling_refusal_at_approval_still_sends_exactly_its_own_notice(tmp_path):
    pb = _BridgeRefuses(equity=10_000)
    _, archived, _ = _approved_through(tmp_path, pb)
    assert archived.gate_refusal == ["bridge_notional_ceiling"]
    [notice] = _notices(tmp_path)
    assert notice.startswith("Order not placed: the MT5 box's safety limit refused LONG ")

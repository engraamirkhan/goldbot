"""Engine safety rules from the design's Order lifecycle, Reconciliation and Risk management sections: restart-safe
order ids, reconciliation of stops, weekend/rollover windows, the combined exposure cap, the supervisor's combined
size-down, feature-version checks, stale data and the 12% re-arm conditions."""
import json
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Proposal

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
        "balance_closed_hwm": equity, "open_lots": lots, "open_notional": lots * 100 * px}))


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

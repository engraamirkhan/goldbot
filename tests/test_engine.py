import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.research.promotion import PerfStats
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter

pytestmark = pytest.mark.integration


def _run(approval_mode: str, tmp_path: Path, days: int = 5, data_root: str | None = None, shadow: bool = False,
         live_shares: dict[str, float] | None = None):
    ticks = synthetic_ticks("2025-03-03", f"2025-03-{3 + days:02d}", ticks_per_minute=1, seed=5)
    pb = PaperBroker(equity=10_000)
    center = ApprovalCenter({111})
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)  # permissive for the test
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode=approval_mode, state_dir=str(tmp_path), owner_user_id=111,
                              data_root=data_root, shadow_host=shadow),
                 pb, [spec], {"session_open": ConstantModel(p=0.65)}, center,
                 shadow_models={"session_open-test-v1": (spec.agent_id, ConstantModel(p=0.65))} if shadow else None,
                 live_shares=live_shares)
    decisions = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        decisions += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
        # approve everything that gets proposed, like an owner tapping Approve
        if approval_mode == "propose":
            for pid in list(center.pending):
                center.decide(pid, 111, True)
    eng.flush_ticks()
    return eng, pb, center, decisions


def test_engine_auto_mode_places_orders_and_writes_state(tmp_path):
    eng, pb, center, decisions = _run("auto", tmp_path, shadow=True)
    executed = [d for d in decisions if d["action"] == "executed:auto"]
    assert executed, "expected at least one auto-executed trade on synthetic data"
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    assert (deals["type"] == "entry").sum() >= 1
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert st["account"] == "icm-demo" and st["equity"] > 0
    # idempotency: no proposal id was sent twice
    ids = [d["proposal"] for d in decisions if d.get("proposal")]
    assert len(ids) == len(set(ids))
    # the shadow book paper-traded the same signals without orders; the risk gate can only make live take fewer
    book = eng.shadow.books["session_open-test-v1"]
    shadow_entries = len(book.open) + len(book.closed)
    assert shadow_entries >= len(executed) >= 1 and book.closed
    st = PerfStats.model_validate_json((tmp_path / "shadow_session_open-test-v1.json").read_text())
    assert st.n_trades == len(book.closed)
    assert len(pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))) == len(deals)   # shadow placed no orders


def test_engine_propose_mode_waits_for_approval(tmp_path):
    eng, pb, center, decisions = _run("propose", tmp_path, data_root=str(tmp_path / "data"))
    proposed = [d for d in decisions if d["action"] == "proposed"]
    assert proposed
    orders = [d for d in eng.decisions if d.get("action") == "order"]
    assert orders and all(o["ok"] for o in orders)
    assert center.veto_value()["approved"] == len(orders) or center.veto_value()["approved"] >= len(orders)

    # ticks and fills are logged for the nightly cost job
    store = Store(tmp_path / "data")
    logged = store.read("ticks", source="icm-demo")
    n_ticks = len(synthetic_ticks("2025-03-03", "2025-03-08", ticks_per_minute=1, seed=5))
    assert len(logged) == n_ticks
    fills = store.read("fills", source="icm-demo")
    orders = [d for d in eng.decisions if d.get("action") == "order" and d["ok"]]
    # every decision is journaled for the agents and reviews (proposals, orders, below-threshold scores ...)
    eng.flush_journal()                      # decisions after the last bar close (approval-time orders)
    journal = store.read("decisions", source="icm-demo")
    assert len(journal) == len(eng.decisions) and {"proposed", "order"} <= set(journal["action"])
    assert len(fills) == len(orders) >= 1
    assert ((fills["side"] * (fills["filled"] - fills["requested"])) >= 0).all()   # paper fills never improve on the quote


def test_shadow_only_member_never_reaches_the_broker(tmp_path):
    # population member without a live share: the shadow book trades it, the broker sees nothing
    eng, pb, center, decisions = _run("auto", tmp_path, shadow=True, live_shares={})
    assert not [d for d in decisions if str(d["action"]).startswith("executed")]
    assert pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC")).empty and not center.pending
    book = eng.shadow.books["session_open-test-v1"]
    assert book.closed or book.open
    assert {t.agent_id for t in book.closed + book.open} == set(eng.agents)   # trades carry the member's agent_id


def test_hourly_agent_decides_only_on_hour_closes(tmp_path):
    # a 1h trend agent next to the 15m session-open agent: it is scored only when a 1h bar completes, on 1h
    # features with 4h/1d context (the columns its research used), and its time barrier is counted in 1h bars
    from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
    ticks = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=1, seed=8)
    live_from = pd.Timestamp("2025-03-12", tz="UTC")
    trend = SPECIALISTS["trend"](adx_min=0.0, pullback_atr=5.0)
    session = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(ticks[ticks["ts_utc"] < live_from]), source="dukascopy")
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              shadow_host=True, data_root=str(tmp_path / "data")),
                 PaperBroker(equity=10_000), [trend, session],
                 {"trend": ConstantModel(p=0.65), "session_open": ConstantModel(p=0.65)},
                 shadow_models={"trend-v1": (trend.agent_id, ConstantModel(p=0.65))})
    n = eng.warm_start(live_from)                                       # restart: history from the store, then ticks
    assert n > 9 * 23 * 60 * 0.6 and list(eng.bars_1m.columns) == BAR_COLUMNS
    decisions = []
    live = ticks[ticks["ts_utc"] >= live_from]
    for ts, bid, ask in zip(live["ts_utc"], live["bid"].to_numpy(float), live["ask"].to_numpy(float)):
        decisions += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    hourly = [d for d in decisions if d["agent"] == trend.agent_id]
    assert hourly and all(pd.Timestamp(d["ts"]).minute == 0 for d in hourly)
    assert any(pd.Timestamp(d["ts"]).minute != 0 for d in decisions if d["agent"] == session.agent_id)
    assert eng._base_bars(trend) == trend.label_spec.max_bars * 4
    assert eng.shadow is not None
    book = eng.shadow.books["trend-v1"]
    trades = book.open + book.closed
    assert trades and {t.timeframe for t in trades} == {"1h"}
    assert all(t.bars_held <= t.max_bars + 1 for t in trades)


def test_cached_context_matches_a_fresh_build(tmp_path):
    # the engine reuses context features until a new context bar completes; across 1h, 4h and the 13:30 NY daily
    # settlement every frame must equal one built from scratch
    from goldbot.data.resample import ticks_to_1m
    b1 = ticks_to_1m(synthetic_ticks("2025-03-03", "2025-03-12", ticks_per_minute=1, seed=4))
    spec = SPECIALISTS["session_open"]()

    def engine() -> Engine:
        e = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(equity=10_000),
                   [spec], {})
        e.bars_1m = b1
        return e

    cached = engine()
    for close in pd.date_range("2025-03-11 16:00", "2025-03-11 19:00", freq="15min", tz="UTC"):
        complete = b1[b1["visible_at"] <= close]
        a, b = cached._frame(complete, "15m", close), engine()._frame(complete, "15m", close)
        assert a is not None and b is not None
        pd.testing.assert_frame_equal(a.X, b.X)
    assert set(cached._ctx_cache) == {"1h", "4h", "1d"}

"""The trade readers (api/activity.py read_trades) behind the Home trades summary: shadow trades, the accounts'
positions and cost-probe orders, each opened and closed. Read-only; accounts by config name with digits cut. The Home
live feed (GET /api/activity) that first used them was retired on 2026-11-02."""
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import activity
from goldbot.api.app import create_app
from goldbot.api.schema import ActivityEvent
from goldbot.db import Database
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_api_views import _login
from tests.test_phase_gates import write_trades

LOGIN = "51234567"                      # stands for an MT5 login embedded in a config name
ACC = f"icm-demo-{LOGIN}"
CUT = "icm-demo-…67"
T0 = pd.Timestamp("2026-10-19 08:00", tz="UTC")
NOW = T0 + pd.Timedelta(hours=6)


@pytest.fixture(autouse=True)
def _account_enabled(enabled_accounts):
    """ACC's engine file is read only while ACC is an enabled account (goldbot.ops.accounts.engine_state_files)."""
    enabled_accounts(ACC)


def _shadow(state: Path) -> None:
    """One taken 1h long that hits its target, one taken trade still open, one counterfactual (not taken)."""
    book = ShadowBook(state, max_open_per_agent=3)
    book.track("box-v1", T0)
    t = book.open_trade(version="box-v1", agent_id="box_break_close-g0", side=1, bar_ts=T0, entry=2400.0, atr_usd=4.0,
                        target_atr=1.5, stop_atr=1.0, max_bars=8, p=0.7, timeframe="1h", threshold=0.5)
    assert t is not None
    book.on_bar(pd.Series({"ts_utc": T0 + pd.Timedelta(hours=1), "bid_low": 2399.0, "bid_high": 2410.0,
                           "ask_high": 2410.2, "ask_low": 2399.2, "bid_close": 2405.0, "ask_close": 2405.2}),
                timeframe="1h")
    assert book.open_trade(version="box-v1", agent_id="box_break_close-g0", side=-1, bar_ts=T0 + pd.Timedelta(hours=2),
                           entry=2405.0, atr_usd=4.0, target_atr=1.5, stop_atr=1.0, max_bars=8, p=0.6, timeframe="1h",
                           threshold=0.5) is not None
    assert book.open_trade(version="box-v1", agent_id="box_break_close-g0", side=1, bar_ts=T0 + pd.Timedelta(hours=3),
                           entry=2405.0, atr_usd=4.0, target_atr=1.5, stop_atr=1.0, max_bars=8, p=0.3, timeframe="1h",
                           threshold=0.5, taken=False) is not None
    book.save(NOW)


def _closed_trade() -> ClosedTrade:
    return ClosedTrade(account_id=ACC, broker="icmarkets", mode="demo", exit_utc=T0 + pd.Timedelta(hours=4), ret=0.002,
                       pnl=12.5, equity_before=1000.0, position_id=11, agent_id="mfi-g0", side=-1, lots=0.05,
                       entry_utc=T0 + pd.Timedelta(hours=3, minutes=30), entry_price=2410.0, exit_price=2405.2,
                       exit_reason="target", r=1.4, pnl_currency="GBP", timeframe="15m")


def _open_position(state: Path) -> None:
    trade = {"position_id": 12, "agent_id": "tsmom-g0", "side": 1, "lots": 0.03,
             "entry_bar_ts": (T0 + pd.Timedelta(hours=4)).isoformat(), "max_bars": 8, "timeframe": "4h",
             "open_price": 2401.5, "opened_utc": (T0 + pd.Timedelta(hours=4, minutes=1)).isoformat()}
    probe = {**trade, "position_id": 13, "agent_id": "cost_probe:mfi-g0"}          # the probe ledger reports it
    (state / f"orders_{ACC}.json").write_text(json.dumps({"sent": {}, "open": {"12": trade, "13": probe}}))
    (state / f"engine_{ACC}.json").write_text(json.dumps({"account": ACC, "mode": "demo", "currency": "GBP"}))


def _probe(state: Path) -> None:
    db = Database.open(state, "aux")
    with db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-1-a", account_id=ACC, agent_id="mfi-g0", version="mfi-v2", side=1, lots=0.01,
            sent_utc=T0 + pd.Timedelta(hours=1, minutes=10), fill_price=2400.4, position_id=21, stop_loss=4.0,
            exit_utc=T0 + pd.Timedelta(hours=2, minutes=10), exit_price=2398.4, exit_reason="stop", pnl=-2.0))


def _everything(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    _shadow(state)
    write_trades(state, [_closed_trade()])
    _open_position(state)
    _probe(state)
    return state


def _events(state: Path) -> tuple[list[ActivityEvent], list[str]]:
    """The taken trades' events from every source, and the sources that could not be read."""
    trades, unreadable = activity.read_trades(state)
    return [e for t in trades if t.taken for e in t.events], list(unreadable)


def test_the_readers_merge_every_source(tmp_path):
    events, unreadable = _events(_everything(tmp_path))
    assert unreadable == []
    assert sorted(e.kind for e in events) == sorted(
        ["position_opened", "position_closed", "position_opened", "shadow_opened", "probe_closed", "shadow_closed",
         "probe_opened", "shadow_opened"])


def test_a_closed_shadow_trade_says_agent_timeframe_side_prices_reason_and_r(tmp_path):
    events, _ = _events(_everything(tmp_path))
    (c,) = [e for e in events if e.kind == "shadow_closed"]
    assert (c.agent_id, c.version, c.timeframe, c.side, c.entry, c.exit, c.reason) == \
        ("box_break_close-g0", "box-v1", "1h", "long", 2400.0, 2406.0, "target")
    assert c.r == pytest.approx(1.5) and c.ret == pytest.approx(6.0 / 2400.0)
    assert c.stop is not None and c.target is not None and c.entry is not None and c.stop < c.entry < c.target   # owner: SL and TP shown
    # opened at the signal bar's close and closed at the exit bar's close (the book stamps bars by their open time)
    opened = sorted(pd.Timestamp(e.ts) for e in events if e.kind == "shadow_opened")
    assert opened[0] == T0 + pd.Timedelta(hours=1)
    assert pd.Timestamp(c.ts) == T0 + pd.Timedelta(hours=2)


def test_a_counterfactual_shadow_candidate_is_read_but_marked_not_taken(tmp_path):
    state = _everything(tmp_path)
    trades, _ = activity.read_trades(state)
    (cf,) = [t for t in trades if not t.taken]
    assert cf.source == "shadow" and cf.opened is not None and cf.opened.p is not None and cf.opened.p < 0.5
    events, _ = _events(state)
    assert sum(e.kind == "shadow_opened" for e in events) == 2


def test_positions_and_probes_carry_the_account_cut_mode_money_and_r(tmp_path):
    events, _ = _events(_everything(tmp_path))
    (closed,) = [e for e in events if e.kind == "position_closed"]
    assert (closed.account, closed.mode, closed.agent_id, closed.side, closed.timeframe, closed.reason) == \
        (CUT, "demo", "mfi-g0", "short", "15m", "target")
    assert (closed.pnl, closed.currency, closed.r, closed.entry, closed.exit) == (12.5, "GBP", 1.4, 2410.0, 2405.2)
    opened = {e.agent_id for e in events if e.kind == "position_opened"}
    assert opened == {"mfi-g0", "tsmom-g0"}                     # the probe's open position is not a position event
    (pc,) = [e for e in events if e.kind == "probe_closed"]
    assert (pc.agent_id, pc.version, pc.mode, pc.reason, pc.pnl, pc.currency) == \
        ("mfi-g0", "mfi-v2", "demo", "stop", -2.0, "GBP")
    assert pc.r == pytest.approx(-0.5)                          # P&L over the loss at the stop
    assert all(LOGIN not in e.model_dump_json() for e in events)


def test_an_empty_state_reads_no_trades(tmp_path):
    assert _events(tmp_path) == ([], [])


def test_an_unreadable_source_is_named_and_the_others_still_read(tmp_path):
    state = _everything(tmp_path)
    (state / "shadow_book.json").write_text("{torn")
    events, unreadable = _events(state)
    assert unreadable == ["shadow"]
    assert any(e.kind == "position_closed" for e in events)
    assert not any(e.kind.startswith("shadow") for e in events)


def test_the_retired_activity_route_is_gone(tmp_path):
    """Owner 2026-11-02: the Home live feed is retired; its route answers 404 like any unknown path."""
    app = create_app(tmp_path / "state", web_dist=tmp_path / "nodist", data_root=tmp_path / "data",
                     owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)
    assert c.get("/api/activity", headers=h["viewer"]).status_code == 404


def test_an_open_exploration_position_is_not_a_position_event(tmp_path):
    """Trading-safety L3 (exploration review 2026-10-25): an arm's position is learning, never a real trade."""
    state = _everything(tmp_path)
    raw = json.loads((state / f"orders_{ACC}.json").read_text())
    raw["open"]["14"] = {**raw["open"]["12"], "position_id": 14, "agent_id": "explore:S0"}
    (state / f"orders_{ACC}.json").write_text(json.dumps(raw))
    events, _ = _events(state)
    assert "explore:S0" not in {e.agent_id for e in events if e.kind == "position_opened"}

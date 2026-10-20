"""Home live feed (owner request 2026-10-19): GET /api/activity merges what the system did into one time-ordered list,
newest first: shadow trades opened and closed, the accounts' positions opened and closed, cost-probe orders opened and
closed, and entry refusals from the decision journal. Read-only; accounts by config name with digits cut."""
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import activity
from goldbot.api.app import create_app
from goldbot.data.store import Store
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


def _journal(root: Path) -> None:
    rows = [
        {"ts_utc": T0 + pd.Timedelta(hours=5), "account_id": ACC, "agent_id": "cost_probe:mfi-g0",
         "action": "cost_probe_refused:probe_loss_unknown", "p": None, "mult": None, "proposal_id": None,
         "detail": json.dumps({"version": "mfi-v2"})},
        {"ts_utc": T0 + pd.Timedelta(hours=5, minutes=1), "account_id": ACC, "agent_id": "tsmom-g0",
         "action": "gate:market_closed", "p": 0.66, "mult": 1.0, "proposal_id": None, "detail": "{}"},
        {"ts_utc": T0 + pd.Timedelta(hours=5, minutes=2), "account_id": ACC, "agent_id": "tsmom-g0",
         "action": "below_threshold", "p": 0.31, "mult": 1.0, "proposal_id": None, "detail": "{}"},
    ]
    df = pd.DataFrame(rows)
    Store(root).append("decisions", df, source=ACC, dedupe=False)


def _everything(tmp_path: Path) -> tuple[Path, Path]:
    state, root = tmp_path / "state", tmp_path / "data"
    state.mkdir()
    _shadow(state)
    write_trades(state, [_closed_trade()])
    _open_position(state)
    _probe(state)
    _journal(root)
    return state, root


def test_the_feed_merges_every_source_newest_first(tmp_path):
    state, root = _everything(tmp_path)
    v = activity.activity_view(state, Store(root), NOW)
    assert v.unreadable == []
    kinds = [e.kind for e in v.events]
    assert kinds == ["refused", "refused", "position_opened", "position_closed", "position_opened", "shadow_opened",
                     "probe_closed", "shadow_closed", "probe_opened", "shadow_opened"]
    assert [e.ts for e in v.events] == sorted((e.ts for e in v.events), reverse=True)


def test_a_closed_shadow_trade_says_agent_timeframe_side_prices_reason_and_r(tmp_path):
    state, root = _everything(tmp_path)
    v = activity.activity_view(state, Store(root), NOW)
    (c,) = [e for e in v.events if e.kind == "shadow_closed"]
    assert (c.agent_id, c.version, c.timeframe, c.side, c.entry, c.exit, c.reason) == \
        ("box_break_close-g0", "box-v1", "1h", "long", 2400.0, 2406.0, "target")
    assert c.r == pytest.approx(1.5) and c.ret == pytest.approx(6.0 / 2400.0)
    assert c.stop is not None and c.target is not None and c.entry is not None and c.stop < c.entry < c.target   # owner: SL and TP shown
    # opened at the signal bar's close and closed at the exit bar's close (the book stamps bars by their open time)
    opened = [e for e in v.events if e.kind == "shadow_opened"]
    assert pd.Timestamp(opened[-1].ts) == T0 + pd.Timedelta(hours=1)
    assert pd.Timestamp(c.ts) == T0 + pd.Timedelta(hours=2)


def test_a_counterfactual_shadow_candidate_is_not_activity(tmp_path):
    state, root = _everything(tmp_path)
    v = activity.activity_view(state, Store(root), NOW)
    assert all(e.p is None or e.p >= 0.5 for e in v.events if e.kind.startswith("shadow"))
    assert sum(e.kind == "shadow_opened" for e in v.events) == 2


def test_positions_and_probes_carry_the_account_cut_mode_money_and_r(tmp_path):
    state, root = _everything(tmp_path)
    v = activity.activity_view(state, Store(root), NOW)
    (closed,) = [e for e in v.events if e.kind == "position_closed"]
    assert (closed.account, closed.mode, closed.agent_id, closed.side, closed.timeframe, closed.reason) == \
        (CUT, "demo", "mfi-g0", "short", "15m", "target")
    assert (closed.pnl, closed.currency, closed.r, closed.entry, closed.exit) == (12.5, "GBP", 1.4, 2410.0, 2405.2)
    opened = {e.agent_id for e in v.events if e.kind == "position_opened"}
    assert opened == {"mfi-g0", "tsmom-g0"}                     # the probe's open position is not a position event
    (pc,) = [e for e in v.events if e.kind == "probe_closed"]
    assert (pc.agent_id, pc.version, pc.mode, pc.reason, pc.pnl, pc.currency) == \
        ("mfi-g0", "mfi-v2", "demo", "stop", -2.0, "GBP")
    assert pc.r == pytest.approx(-0.5)                          # P&L over the loss at the stop
    assert LOGIN not in v.model_dump_json()


def test_refusals_come_from_the_journal_and_scores_below_the_threshold_do_not(tmp_path):
    state, root = _everything(tmp_path)
    v = activity.activity_view(state, Store(root), NOW)
    refused = [e for e in v.events if e.kind == "refused"]
    assert [(e.agent_id, e.reason, e.version) for e in refused] == [
        ("tsmom-g0", "gate:market_closed", None), ("cost_probe:mfi-g0", "cost_probe_refused:probe_loss_unknown", "mfi-v2")]
    assert all(e.account == CUT for e in refused)


def test_the_limit_keeps_the_newest_events(tmp_path):
    state, root = _everything(tmp_path)
    v = activity.activity_view(state, Store(root), NOW, limit=3)
    assert [e.kind for e in v.events] == ["refused", "refused", "position_opened"]


def test_an_empty_state_is_an_empty_feed(tmp_path):
    v = activity.activity_view(tmp_path, Store(tmp_path / "data"), NOW)
    assert v.events == [] and v.unreadable == []


def test_an_unreadable_source_is_named_and_the_others_still_show(tmp_path):
    state, root = _everything(tmp_path)
    (state / "shadow_book.json").write_text("{torn")
    v = activity.activity_view(state, Store(root), NOW)
    assert v.unreadable == ["shadow"]
    assert any(e.kind == "position_closed" for e in v.events)
    assert not any(e.kind.startswith("shadow") for e in v.events)


def test_the_route_needs_a_sign_in_and_serves_a_viewer_without_the_login(tmp_path):
    state, root = _everything(tmp_path)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=root, owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)
    assert c.get("/api/activity").status_code == 401
    r = c.get("/api/activity?limit=4", headers=h["viewer"])
    assert r.status_code == 200
    body = r.json()
    assert len(body["events"]) == 4 and body["events"][0]["kind"] == "refused"
    assert LOGIN not in r.text
    assert c.get("/api/activity?limit=0", headers=h["viewer"]).status_code == 422

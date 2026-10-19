"""Home redesign (docs/ux/home-redesign-2026-10-19.md, Decision 2026-10-19): the three read-only views behind the
status card stack. N1 `GET /api/positions/open` (stop, target, floating P&L and R per open position, probes tagged),
N2 `GET /api/strategies/watch` (each strategy agent's state in words, "unknown" when it cannot be known) and
`GET /api/today` (day P&L after costs split closed / open, today's costs, the loss meter naming the limit that binds
first, stale after 60 s). Viewer and above; accounts by config name with digits cut."""
import json
import time
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from goldbot.api import home_cards as hc
from goldbot.api.app import create_app
from goldbot.db import Database
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_api_views import _login

LOGIN = "51234567"
ACC = f"icm-demo-{LOGIN}"
CUT = "icm-demo-…67"
NOW = pd.Timestamp("2026-10-19 10:00", tz="UTC")


def _engine(state: Path, **over: object) -> None:
    e = {"account": ACC, "mode": "demo", "currency": "GBP", "usd_to_account": 0.8, "ts": NOW.timestamp(),
         "price": 2410.0, "lot_terms": {"contract_oz": 100.0, "volume_min": 0.01}, "equity": 1012.0, "balance": 1010.0,
         "day_start_equity": 1000.0, "daily_loss_limit": 50.0, "daily_loss_remaining": 44.0, "market_closed": False}
    e.update(over)
    (state / f"engine_{ACC}.json").write_text(json.dumps(e))


def _orders(state: Path) -> None:
    trade = {"position_id": 12, "agent_id": "box_break_close-g0-1h", "side": 1, "lots": 0.05,
             "entry_bar_ts": (NOW - pd.Timedelta(hours=2)).isoformat(), "max_bars": 8, "timeframe": "1h",
             "open_price": 2400.0, "sl": 2396.0, "tp": 2420.0, "initial_sl": 2395.0,
             "opened_utc": (NOW - pd.Timedelta(hours=1)).isoformat()}
    probe = {**trade, "position_id": 13, "agent_id": "cost_probe:mfi-g0", "side": -1, "lots": 0.01,
             "open_price": 2412.0, "sl": 2416.0, "tp": 2404.0, "initial_sl": 2416.0, "timeframe": "15m"}
    (state / f"orders_{ACC}.json").write_text(json.dumps({"sent": {}, "open": {"12": trade, "13": probe}}))


# ------------------------------------------------------------------------------------------------ N1 open positions
def test_an_open_position_carries_stop_target_floating_money_and_r(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    v = hc.open_positions(tmp_path, NOW)
    p = next(x for x in v.positions if x.position_id == 12)
    assert (p.account, p.mode, p.probe, p.side) == (CUT, "demo", False, "long")
    assert (p.entry, p.stop, p.target, p.timeframe) == (2400.0, 2396.0, 2420.0, "1h")
    # long 0.05 lot x 100 oz x (2410 - 2400) USD x 0.8 GBP/USD = £40 at the mid, before costs
    assert p.floating_pnl == 40.0 and p.currency == "GBP"
    assert p.r_now == 2.0                                   # 10 USD over the initial risk 2400 - 2395
    assert p.price == 2410.0 and not p.stale


def test_a_cost_probe_is_listed_and_tagged_as_a_probe(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    p = next(x for x in hc.open_positions(tmp_path, NOW).positions if x.position_id == 13)
    assert p.probe and p.side == "short"
    assert p.floating_pnl == 1.6                            # short: (2412 - 2410) x 0.01 x 100 x 0.8
    assert p.r_now == 0.5


def test_floating_money_is_unknown_without_a_conversion_and_old_prices_are_stale(tmp_path):
    _engine(tmp_path, usd_to_account=None, ts=(NOW - pd.Timedelta(seconds=90)).timestamp())
    _orders(tmp_path)
    p = next(x for x in hc.open_positions(tmp_path, NOW).positions if x.position_id == 12)
    assert p.floating_pnl is None and p.r_now == 2.0 and p.stale


def test_an_unreadable_orders_file_is_named_and_nothing_is_invented(tmp_path):
    _engine(tmp_path)
    (tmp_path / f"orders_{ACC}.json").write_text("{torn")
    v = hc.open_positions(tmp_path, NOW)
    assert v.positions == [] and v.unreadable == [CUT]


# ------------------------------------------------------------------------------------------------ N2 strategy watch
def _book(state: Path) -> None:
    book = ShadowBook(state, max_open_per_agent=3)
    book.track("box-v1", NOW - pd.Timedelta(days=1))
    assert book.open_trade(version="box-v1", agent_id="box_break_close-g0-1h", side=1,
                           bar_ts=NOW - pd.Timedelta(hours=2), entry=2400.0, atr_usd=4.0, target_atr=3.0,
                           stop_atr=1.5, max_bars=48, p=1.0, timeframe="1h", threshold=0.5) is not None
    # a 15m candidate whose bar closed this bar: a signal, below the threshold (not taken)
    assert book.open_trade(version="box-v1", agent_id="box_break_close-g0-15m", side=-1,
                           bar_ts=NOW - pd.Timedelta(minutes=30), entry=2405.0, atr_usd=2.0, target_atr=3.0,
                           stop_atr=1.5, max_bars=96, p=0.3, timeframe="15m", threshold=0.5, taken=False) is not None
    book.save(NOW)


def test_each_strategy_agent_has_a_state_in_words(tmp_path):
    _engine(tmp_path)
    _book(tmp_path)
    (tmp_path / "agents.json").write_text(json.dumps([
        {"agent_id": "box_break_close-g0-4h", "family": "box_break_close", "status": "shadow"},
        {"agent_id": "old-g0", "family": "old", "status": "retired"}]))
    v = hc.strategy_watch(tmp_path, NOW)
    by = {a.agent_id: a for a in v.agents}
    assert by["box_break_close-g0-1h"].state == "in_trade"
    assert "long from 2,400.00" in by["box_break_close-g0-1h"].text
    sig = by["box_break_close-g0-15m"]
    assert sig.state == "signal" and sig.signal_utc == NOW - pd.Timedelta(minutes=15)
    assert "below its threshold" in sig.text
    # no record at all: said plainly, never a guessed box
    assert by["box_break_close-g0-4h"].state == "unknown" and by["box_break_close-g0-4h"].box_high is None
    assert by["box_break_close-g0-4h"].timeframe == "4h"
    assert "old-g0" not in by                               # retired agents are not watched


def test_a_closed_market_makes_a_quiet_agent_idle(tmp_path):
    _engine(tmp_path, market_closed=True)
    _book(tmp_path)
    by = {a.agent_id: a for a in hc.strategy_watch(tmp_path, NOW + pd.Timedelta(hours=3)).agents}
    assert by["box_break_close-g0-15m"].state == "idle"
    assert by["box_break_close-g0-15m"].text == "idle: market closed"
    assert by["box_break_close-g0-1h"].state == "in_trade"  # an open trade is still in a trade


def test_an_unreadable_shadow_book_makes_every_state_unknown(tmp_path):
    _engine(tmp_path)
    (tmp_path / "agents.json").write_text(json.dumps([{"agent_id": "box_break_close-g0-1h", "status": "shadow"}]))
    (tmp_path / "shadow_book.json").write_text("{torn")
    v = hc.strategy_watch(tmp_path, NOW)
    assert v.unreadable == ["shadow"]
    assert [a.state for a in v.agents] == ["unknown"]


# ------------------------------------------------------------------------------------------------ today's money
def _today_records(state: Path) -> None:
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=ACC, broker="icmarkets", mode="demo", exit_utc=NOW - pd.Timedelta(hours=1), ret=0.001, pnl=6.0,
        equity_before=1000.0, position_id=31, agent_id="mfi-g0", commission=0.7, swap=-0.3, pnl_basis="net"))
    gates_phase.append_closed_trade(state, ClosedTrade(       # yesterday's opportunity day: not today
        account_id=ACC, broker="icmarkets", mode="demo", exit_utc=NOW - pd.Timedelta(hours=14), ret=0.001, pnl=9.0,
        equity_before=1000.0, position_id=30, commission=0.7, swap=0.0, pnl_basis="net"))
    db = Database.open(state, "aux")
    with db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-1", account_id=ACC, agent_id="mfi-g0", side=1, lots=0.01, spread_usd=0.2,
            sent_utc=NOW - pd.Timedelta(hours=3), fill_price=2400.0, position_id=21, stop_loss=4.0,
            exit_utc=NOW - pd.Timedelta(hours=2), exit_price=2398.0, exit_reason="stop", pnl=-2.0, commission=0.1,
            swap=0.0))


def test_today_splits_the_day_into_closed_and_open_after_costs(tmp_path):
    _engine(tmp_path)
    _today_records(tmp_path)
    a = hc.today_money(tmp_path, NOW, probe_terms={ACC: hc.ProbeTerms(budget_share=0.4, max_loss=4.0)}).accounts[0]
    assert (a.account, a.mode, a.currency) == (CUT, "demo", "GBP")
    assert a.day_pnl == 12.0                                # equity 1012 - day start 1000
    assert a.open_pnl == 2.0                                # equity - balance: the broker's floating
    assert a.closed_pnl == 4.0 and a.closed_n == 2          # 6.0 + (-2.0), net of costs; yesterday's trade left out
    assert a.costs.commission == 0.8 and a.costs.swap == -0.3
    assert a.costs.spread == 0.16                           # the probe's 0.2 USD x 0.01 lot x 100 oz x 0.8
    assert not a.costs.spread_complete                      # the position record keeps no spread in money
    assert not a.stale


def test_the_loss_meter_names_the_probe_stop_when_it_binds_first(tmp_path):
    _engine(tmp_path, daily_loss_remaining=44.0)            # £6 used of £50
    m = hc.today_money(tmp_path, NOW, probe_terms={ACC: hc.ProbeTerms(budget_share=0.4, max_loss=4.0)}
                       ).accounts[0].loss_meter
    assert m is not None and m.used == 6.0
    assert [(c.key, c.limit) for c in m.limits] == [("probe_stop", 16.0), ("daily_limit", 50.0)]
    assert m.binding == "probe_stop"                        # £10 left before new probes stop, £44 before all entries


def test_without_probes_the_daily_limit_binds(tmp_path):
    _engine(tmp_path)
    m = hc.today_money(tmp_path, NOW, probe_terms={}).accounts[0].loss_meter
    assert m is not None and m.binding == "daily_limit" and [c.key for c in m.limits] == ["daily_limit"]


def test_money_older_than_a_minute_is_stale(tmp_path):
    _engine(tmp_path, ts=(NOW - pd.Timedelta(seconds=61)).timestamp())
    a = hc.today_money(tmp_path, NOW, probe_terms={}).accounts[0]
    assert a.stale and a.age_s == 61.0


# ------------------------------------------------------------------------------------------------ the routes
def test_the_routes_need_a_sign_in_serve_a_viewer_and_never_show_the_login(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _engine(state, ts=time.time())
    _orders(state)
    _book(state)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)
    for path in ("/api/positions/open", "/api/strategies/watch", "/api/today"):
        assert c.get(path).status_code == 401
        r = c.get(path, headers=h["viewer"])
        assert r.status_code == 200, path
        assert LOGIN not in r.text
    assert len(c.get("/api/positions/open", headers=h["viewer"]).json()["positions"]) == 2

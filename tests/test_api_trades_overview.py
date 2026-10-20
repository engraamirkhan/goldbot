"""Home trades summary (owner request 2026-10-19: "a summary of all types of trades"): GET /api/trades/overview counts
every kind of trade the activity feed reads (shadow taken and all, demo and live positions, cost probes) for today,
the last 7 days and all time: open, closed, wins, losses, win rate, R, net P&L by currency, best and worst, exit
reasons, and the same by strategy family and by timeframe. Read-only; built from the activity feed's readers."""
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import trades_overview as ov
from goldbot.api.app import create_app
from goldbot.api.schema import TradesOverview, TradeStats
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_api_activity import ACC, LOGIN, NOW, _everything
from tests.test_api_views import _login

LIVE = "icm-live"


@pytest.fixture(autouse=True)
def _account_enabled(enabled_accounts):
    """ACC's engine file is read only while ACC is an enabled account (goldbot.ops.accounts.engine_state_files)."""
    enabled_accounts(ACC)


def _more_trades(state: Path) -> None:
    """A net demo loss 3 days back (in 7 days, not today) and a net live win 20 days back (all time only)."""
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=ACC, broker="icmarkets", mode="demo", exit_utc=NOW - pd.Timedelta(days=3), ret=-0.004, pnl=-9.0,
        equity_before=1000.0, position_id=31, agent_id="tsmom-g0", side=1, lots=0.03, exit_reason="stop", r=-1.0,
        pnl_currency="GBP", pnl_basis="net", timeframe="4h"))
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=LIVE, broker="icmarkets", mode="live", exit_utc=NOW - pd.Timedelta(days=20), ret=0.008, pnl=20.0,
        equity_before=1000.0, position_id=41, agent_id="box_break_close-g1-ab12", side=1, lots=0.01,
        exit_reason="time_exit", r=2.0, pnl_currency="GBP", pnl_basis="net", timeframe="1h"))


def _overview(tmp_path: Path) -> TradesOverview:
    state, _ = _everything(tmp_path)
    _more_trades(state)
    return ov.trades_overview(state, NOW)


def _stats(v: TradesOverview, period: str, kind: str) -> TradeStats:
    (p,) = [p for p in v.periods if p.period == period]
    (t,) = [t for t in p.types if t.type == kind]
    return t.stats


def test_every_period_lists_every_trade_type_in_order(tmp_path):
    v = _overview(tmp_path)
    assert v.unreadable == []
    assert [p.period for p in v.periods] == ["today", "7d", "all"]
    for p in v.periods:
        assert [t.type for t in p.types] == ["shadow_taken", "shadow_all", "demo", "live", "probe"]


def test_periods_start_at_the_opportunity_day_boundary(tmp_path):
    v = _overview(tmp_path)
    since = {p.period: None if p.since is None else pd.Timestamp(p.since) for p in v.periods}
    assert since == {"today": pd.Timestamp("2026-10-18 22:00", tz="UTC"),
                     "7d": pd.Timestamp("2026-10-12 22:00", tz="UTC"), "all": None}


def test_shadow_taken_counts_the_open_and_the_closed_trade_with_its_r(tmp_path):
    s = _stats(_overview(tmp_path), "today", "shadow_taken")
    assert (s.open, s.closed, s.wins, s.losses, s.win_rate, s.n_r) == (1, 1, 1, 0, 1.0, 1)
    assert s.total_r == pytest.approx(1.5) and s.avg_r == pytest.approx(1.5)
    assert s.net_pnl == {}                                   # a shadow trade has no money
    assert s.exits.target == 1 and s.best is not None and s.best.agent_id == "box_break_close-g0"


def test_shadow_all_adds_the_counterfactual_candidates(tmp_path):
    s = _stats(_overview(tmp_path), "today", "shadow_all")
    assert (s.open, s.closed) == (2, 1)


def test_a_demo_pnl_not_recorded_as_net_is_never_summed_as_net(tmp_path):
    s = _stats(_overview(tmp_path), "today", "demo")
    assert (s.open, s.closed, s.wins, s.losses) == (1, 1, 1, 0)
    assert s.total_r == pytest.approx(1.4)
    assert s.net_pnl == {}                                   # the activity fixture's trade records no pnl basis


def test_seven_days_take_in_the_older_net_demo_loss(tmp_path):
    s = _stats(_overview(tmp_path), "7d", "demo")
    assert (s.open, s.closed, s.wins, s.losses, s.win_rate) == (1, 2, 1, 1, 0.5)
    assert s.total_r == pytest.approx(0.4) and s.avg_r == pytest.approx(0.2)
    assert s.net_pnl == {"GBP": -9.0}
    assert s.best is not None and s.best.r == pytest.approx(1.4) and s.best.agent_id == "mfi-g0"
    assert s.worst is not None and s.worst.r == pytest.approx(-1.0) and s.worst.pnl == -9.0
    assert (s.exits.target, s.exits.stop, s.exits.time, s.exits.other) == (1, 1, 0, 0)


def test_live_trades_older_than_seven_days_show_only_in_all_time(tmp_path):
    v = _overview(tmp_path)
    assert _stats(v, "7d", "live").closed == 0
    assert _stats(v, "7d", "live").win_rate is None and _stats(v, "7d", "live").total_r is None
    s = _stats(v, "all", "live")
    assert (s.closed, s.wins, s.net_pnl, s.exits.time) == (1, 1, {"GBP": 20.0}, 1)


def test_cost_probes_carry_net_pnl_and_r_and_an_unknown_timeframe(tmp_path):
    v = _overview(tmp_path)
    (p,) = [p for p in v.periods if p.period == "today"]
    (probe,) = [t for t in p.types if t.type == "probe"]
    s = probe.stats
    assert (s.open, s.closed, s.losses, s.net_pnl, s.exits.stop) == (0, 1, 1, {"GBP": -2.0}, 1)
    assert s.total_r == pytest.approx(-0.5)
    assert list(probe.by_timeframe) == ["unknown"] and list(probe.by_family) == ["mfi"]


def test_breakdowns_by_family_and_timeframe_include_open_trades(tmp_path):
    v = _overview(tmp_path)
    (p,) = [p for p in v.periods if p.period == "7d"]
    (demo,) = [t for t in p.types if t.type == "demo"]
    assert set(demo.by_family) == {"mfi", "tsmom"}
    assert (demo.by_family["tsmom"].open, demo.by_family["tsmom"].closed) == (1, 1)
    assert set(demo.by_timeframe) == {"15m", "4h"}
    assert demo.by_timeframe["15m"].wins == 1
    (p,) = [p for p in v.periods if p.period == "all"]
    (live,) = [t for t in p.types if t.type == "live"]
    assert list(live.by_family) == ["box_break_close"]       # generation and hash cut


def test_an_empty_state_is_all_zero(tmp_path):
    v = ov.trades_overview(tmp_path, NOW)
    assert v.unreadable == []
    for p in v.periods:
        for t in p.types:
            assert (t.stats.open, t.stats.closed, t.stats.best, t.by_family) == (0, 0, None, {})


def test_an_unreadable_source_is_named_and_the_others_still_count(tmp_path):
    state, _ = _everything(tmp_path)
    (state / "shadow_book.json").write_text("{torn")
    v = ov.trades_overview(state, NOW)
    assert v.unreadable == ["shadow"]
    assert _stats(v, "today", "shadow_taken").closed == 0
    assert _stats(v, "today", "demo").closed == 1


def test_the_route_needs_a_sign_in_and_serves_a_viewer_without_the_login(tmp_path):
    state, root = _everything(tmp_path)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=root, owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)
    assert c.get("/api/trades/overview").status_code == 401
    r = c.get("/api/trades/overview", headers=h["viewer"])
    assert r.status_code == 200
    body = r.json()
    assert [p["period"] for p in body["periods"]] == ["today", "7d", "all"]
    assert LOGIN not in r.text


def test_a_net_record_without_its_currency_counts_in_the_account_currency(tmp_path):
    state, _ = _everything(tmp_path)                         # the engine state says the account is in GBP
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=ACC, broker="icmarkets", mode="demo", exit_utc=NOW - pd.Timedelta(hours=1), ret=0.001, pnl=3.0,
        equity_before=1000.0, position_id=51, agent_id="mfi-g0", commission=0.5, swap=0.0, timeframe="15m"))
    assert _stats(ov.trades_overview(state, NOW), "today", "demo").net_pnl == {"GBP": 3.0}

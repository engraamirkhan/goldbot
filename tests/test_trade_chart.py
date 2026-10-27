"""Item 133 (docs/design/trades-ux-2026-10-27.md §3.3): `GET /api/trades/{trade_id}/bars`. One trade on the stored
bars of its decision timeframe, 30 bars before the entry to 5 after the exit (now while open), with its entry, its
stop at entry and each accepted move, its target and its exit. The broker's own minutes first, the reference feed
otherwise; none stored is said in words with the levels still given. Per-trade data is never blinded (ADR 0050)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import trade_chart as tc
from goldbot.api import trades_ledger as tl
from goldbot.api.app import create_app
from goldbot.data.resample import BAR_COLUMNS
from goldbot.data.store import Store
from tests.test_api_views import _login
from tests.test_trades_ledger import NOW, SCALP, _arm, _catalog, _engine

M = pd.Timedelta(minutes=1)
SENT = pd.Timestamp("2026-10-27 10:02:30", tz="UTC")


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP)


def _minutes(store: Store, table: str, source: str, lo: pd.Timestamp, hi: pd.Timestamp, base: float = 2400.0) -> None:
    ts = pd.date_range(lo, hi, freq="1min", inclusive="left", tz="UTC")
    px = [base + (i % 7) * 0.1 for i in range(len(ts))]
    df = pd.DataFrame({"ts_utc": ts, "visible_at": ts + M})
    for f, d in (("open", 0.0), ("high", 0.3), ("low", -0.3), ("close", 0.1)):
        df[f"bid_{f}"] = [p + d for p in px]
        df[f"ask_{f}"] = [p + d + 0.2 for p in px]
    df["tick_count"], df["spread_mean"], df["spread_max"] = 10.0, 0.2, 0.3
    store.append(table, df[BAR_COLUMNS], source=source)


def _trail(store: Store, pid: int, at: pd.Timestamp, sl: float, ok: bool = True) -> None:
    store.append("decisions", pd.DataFrame([{
        "ts_utc": at, "account_id": SCALP, "agent_id": "explore:S1A", "action": "trail", "p": None, "mult": None,
        "proposal_id": None, "detail": json.dumps({"position": pid, "sl": sl, "ok": ok, "retcode": 10009})}]),
        source=SCALP, dedupe=False)


def _row(state: Path, **kw: Any) -> Any:
    _engine(state, SCALP)
    _arm(state, "t1", "S1A", sent=SENT, exit_after=pd.Timedelta(minutes=37), pid=901, **kw)
    led = tl.build_ledger(state, NOW, cat=_catalog(state))
    return next(r for r in led.rows if r.trade_id.endswith(":t1"))


def test_a_closed_trade_gets_its_timeframes_bars_from_30_before_the_entry_to_5_after_the_exit(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, SENT - 4 * pd.Timedelta(hours=1), SENT + pd.Timedelta(hours=2))
    row = _row(tmp_path)
    view = tc.trade_bars(row, store, NOW)
    assert view.timeframe == "5m" and view.feed == "broker" and view.missing is None
    first, last = pd.Timestamp(view.bars[0].t), pd.Timestamp(view.bars[-1].t)
    assert first == pd.Timestamp("2026-10-27 10:00", tz="UTC") - 30 * 5 * M
    assert last == pd.Timestamp("2026-10-27 10:35", tz="UTC") + 5 * 5 * M        # exit 10:39:30 is in the 10:35 bar
    assert all(b.l <= min(b.o, b.c) and b.h >= max(b.o, b.c) for b in view.bars)
    assert (view.entry, view.stop, view.target, view.exit) == (row.entry, row.stop, row.target, row.exit)
    assert view.entry_utc == row.opened_utc and view.exit_utc == row.exit_utc and view.side == "long"


def test_each_accepted_stop_move_of_its_position_is_listed_in_time_order(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, SENT - pd.Timedelta(hours=3), SENT + pd.Timedelta(hours=1))
    _trail(store, 901, SENT + 20 * M, 2400.1)
    _trail(store, 901, SENT + 10 * M, 2398.5)
    _trail(store, 901, SENT + 15 * M, 2399.0, ok=False)                          # refused by the broker
    _trail(store, 902, SENT + 12 * M, 2399.9)                                    # another position
    view = tc.trade_bars(_row(tmp_path), store, NOW)
    assert [(m.price, pd.Timestamp(m.at)) for m in view.stop_moves] == [(2398.5, SENT + 10 * M), (2400.1, SENT + 20 * M)]


def test_without_the_brokers_minutes_the_reference_feed_is_used_and_named(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m", "dukascopy", SENT - pd.Timedelta(hours=3), SENT + pd.Timedelta(hours=1))
    view = tc.trade_bars(_row(tmp_path), store, NOW)
    assert view.feed == "reference" and view.bars


def test_no_stored_bars_says_so_in_words_and_still_gives_the_levels(tmp_path):
    view = tc.trade_bars(_row(tmp_path), Store(tmp_path / "data"), NOW)
    assert view.bars == [] and view.feed is None and view.missing == "Bars for this period aren't stored yet"
    assert view.entry is not None and view.stop is not None and view.target is not None


def test_an_open_trade_runs_to_now_and_never_past_it(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, NOW - pd.Timedelta(hours=4), NOW + pd.Timedelta(hours=1))   # a bad clock
    _engine(tmp_path, SCALP)
    _arm(tmp_path, "o1", "S1A", sent=NOW - 22 * M, exit_after=None)
    row = next(r for r in tl.build_ledger(tmp_path, NOW, cat=_catalog(tmp_path)).rows if r.status == "open")
    view = tc.trade_bars(row, store, NOW)
    assert pd.Timestamp(view.bars[-1].t) < NOW and view.exit is None and view.exit_utc is None


def test_a_hold_longer_than_the_bar_cap_steps_up_to_a_coarser_timeframe(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, SENT - pd.Timedelta(days=3), SENT + pd.Timedelta(days=3))
    _engine(tmp_path, SCALP)
    _arm(tmp_path, "long1", "S0", tf="1m", sent=SENT, exit_after=pd.Timedelta(hours=10))
    row = next(r for r in tl.build_ledger(tmp_path, NOW, cat=_catalog(tmp_path)).rows if r.trade_id.endswith(":long1"))
    view = tc.trade_bars(row, store, NOW)
    assert view.timeframe == "5m" and len(view.bars) <= tc.MAX_BARS


def test_the_bars_route_needs_a_sign_in_and_shows_an_unread_arms_trade_unblinded(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _engine(state, SCALP)
    _arm(state, "t1", "S1A", sent=SENT, exit_after=pd.Timedelta(minutes=37), pid=901, pnl=4.56)
    _minutes(Store(tmp_path / "data"), "bars_1m_broker", SCALP, SENT - pd.Timedelta(hours=3), SENT + pd.Timedelta(hours=1))
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    tid = "exploration:icm-probe:t1"
    assert c.get(f"/api/trades/{tid}/bars").status_code == 401
    h = _login(c, app)["viewer"]
    body = c.get(f"/api/trades/{tid}/bars", headers=h).json()
    assert body["trade_id"] == tid and body["bars"] and body["exit"] is not None
    assert c.get(f"/api/trades/{tid}", headers=h).json()["pnl"] == 4.56            # the detail is not shadowed
    assert c.get("/api/trades/nope:x:1/bars", headers=h).status_code == 404

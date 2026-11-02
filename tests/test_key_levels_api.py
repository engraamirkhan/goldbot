"""`GET /api/key-levels`: each account's current key zones (engine_<account>.json `key_zones`, written by
goldbot/engine/key_zones.py) with the recent bars of the chart's timeframe, for the dashboard's key-level chart.
Read-only display: nothing here writes, sizes or orders."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import key_levels_view as kv
from goldbot.api.app import create_app
from goldbot.data.store import Store
from tests.test_api_views import _login
from tests.test_trade_chart import _minutes
from tests.test_trades_ledger import NOW, SCALP, _engine

ZONE = {"price": 2412.5, "lo": 2411.0, "hi": 2414.0, "strength": 9.5, "strength_pct": 88.0, "tfs": ["1h", "1d"],
        "touches": 3, "role": "resistance", "flipped": False}
ZONES = {"as_of": (NOW - pd.Timedelta(minutes=5)).isoformat(), "price": 2405.0, "atr_1d": 30.0, "history_days": 62.0,
         "above": [ZONE, {**ZONE, "price": 2430.0, "lo": 2429.0, "hi": 2431.0, "strength_pct": 40.0, "tfs": ["round"]}],
         "below": [{**ZONE, "price": 2390.0, "lo": 2389.0, "hi": 2391.0, "role": "support", "flipped": True}]}


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP)


def _state(tmp_path: Path, **over: Any) -> Path:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    _engine(state, SCALP, **over)
    return state


def test_an_accounts_zones_come_with_the_recent_bars_of_the_chosen_timeframe(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, NOW - pd.Timedelta(days=6), NOW)
    view = kv.key_levels_view([json.loads((_state(tmp_path, key_zones=ZONES) / f"engine_{SCALP}.json").read_text())],
                              store, "1h", NOW)
    assert view.account_id == SCALP and view.timeframe == "1h" and view.feed == "broker"
    assert len(view.bars) == kv.N_BARS and pd.Timestamp(view.bars[-1].t) < NOW
    (acc,) = view.accounts
    assert [z.price for z in acc.above] == [2412.5, 2430.0] and acc.below[0].role == "support" and acc.below[0].flipped
    assert acc.above[0].tfs == ["1h", "1d"] and acc.above[0].strength_pct == 88.0 and acc.history_days == 62.0


def test_an_engine_without_zones_or_with_a_malformed_book_is_left_out_never_a_failure(tmp_path):
    engines: list[dict[str, Any]] = [{"account": SCALP, "key_zones": None}, {"account": "b", "key_zones": {"above": "bad"}},
               {"account": "c", "key_zones": ZONES}]
    view = kv.key_levels_view(engines, Store(tmp_path / "data"), "15m", NOW)
    assert [a.account_id for a in view.accounts] == ["c"] and view.account_id == "c"
    assert view.bars == [] and view.feed is None


def test_the_route_needs_a_sign_in_and_refuses_an_unknown_timeframe(tmp_path):
    state = _state(tmp_path, key_zones=ZONES)
    _minutes(Store(tmp_path / "data"), "bars_1m_broker", SCALP, NOW - pd.Timedelta(days=2), NOW)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    assert c.get("/api/key-levels").status_code == 401
    h = _login(c, app)["viewer"]
    body = c.get("/api/key-levels?tf=15m", headers=h).json()
    assert body["accounts"][0]["above"][0]["price"] == 2412.5 and body["timeframe"] == "15m"
    assert c.get("/api/key-levels?tf=3m", headers=h).status_code == 422


def test_zones_built_over_30_minutes_ago_are_marked_stale(tmp_path):
    def acc(age_min: int) -> Any:
        kz = {**ZONES, "as_of": (NOW - pd.Timedelta(minutes=age_min)).isoformat(), "pct_ready": True, "zones_total": 31}
        view = kv.key_levels_view([{"account": SCALP, "key_zones": kz}], None, "1h", NOW)
        return view.accounts[0]
    assert not acc(29).stale and acc(31).stale
    assert acc(5).zones_total == 31 and acc(5).pct_ready

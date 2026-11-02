"""Charts of a non-gold instrument (multi-instrument demo, lane L4): the trade chart and the key-level chart read the
instrument's own bars in its own digits, never the account's gold and never the gold reference feed.

Design rules: "store bars_1m_live keyed (source=account, symbol=store_symbol), never pooled with XAUUSD" (plan);
"Key levels: InstrumentPicker ..., ?sym= in URL, fmtPrice axis, round zones hidden off gold, empty 'No levels yet for
X'" (UI spec 2026-11-02)."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from goldbot.api import key_levels_view as kv
from goldbot.api import trade_chart
from goldbot.data.resample import BAR_COLUMNS
from goldbot.data.store import Store
from tests.test_key_levels_api import ZONE, ZONES
from tests.test_trade_chart import _minutes
from tests.test_trades_ledger import NOW, SCALP


def _fx_minutes(store: Store, table: str, source: str, lo: pd.Timestamp, hi: pd.Timestamp) -> None:
    ts = pd.date_range(lo, hi, freq="1min", inclusive="left", tz="UTC")
    df = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for f, d in (("open", 0.0), ("high", 0.00031), ("low", -0.00029), ("close", 0.00012)):
        df[f"bid_{f}"] = 1.08452 + d
        df[f"ask_{f}"] = 1.08462 + d
    df["tick_count"], df["spread_mean"], df["spread_max"] = 10.0, 0.0001, 0.0002
    store.append(table, df[BAR_COLUMNS], source=source, symbol="EURUSD")


def test_recent_bars_of_another_instrument_are_its_own_in_its_digits(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, NOW - pd.Timedelta(days=1), NOW)               # the account's gold
    _fx_minutes(store, "bars_1m_broker", SCALP, NOW - pd.Timedelta(days=1), NOW)
    gold, _ = trade_chart.recent_bars(store, SCALP, "1h", 5, NOW)
    fx, feed = trade_chart.recent_bars(store, SCALP, "1h", 5, NOW, symbol="EURUSD")
    assert gold[-1].o > 2000 and feed == "broker"
    assert fx and all(1.0 < b.l < b.h < 1.1 for b in fx) and fx[-1].o == 1.08452    # five digits, never 1.08


def test_another_instrument_never_falls_back_to_the_gold_reference_feed(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m", trade_chart.REFERENCE, NOW - pd.Timedelta(days=1), NOW)
    assert trade_chart.recent_bars(store, SCALP, "1h", 5, NOW, symbol="US500") == ([], None)
    assert trade_chart.recent_bars(store, SCALP, "1h", 5, NOW)[1] == "reference"


def _engine(**kw: Any) -> dict[str, Any]:
    return {"account": SCALP, **kw}


def test_key_levels_of_another_instrument_come_from_its_own_book_and_say_their_symbol(tmp_path):
    us500 = {**ZONES, "price": 5000.0, "above": [{**ZONE, "price": 5010.0, "lo": 5008.0, "hi": 5012.0}],
             "below": [{**ZONE, "price": 4990.0, "lo": 4988.0, "hi": 4992.0, "tfs": ["round"], "role": "support"}]}
    e = _engine(key_zones=ZONES, key_zones_by_symbol={"US500": us500})
    gold = kv.key_levels_view([e], None, "1h", NOW)
    assert gold.symbol == "XAUUSD" and gold.accounts[0].symbol == "XAUUSD" and gold.accounts[0].above[0].price == 2412.5
    view = kv.key_levels_view([e], None, "1h", NOW, symbol="US500")
    (acc,) = view.accounts
    assert view.symbol == "US500" and acc.symbol == "US500" and acc.above[0].price == 5010.0
    assert acc.below == []                                       # a round-number zone is gold's: hidden off gold
    assert kv.key_levels_view([_engine(key_zones=ZONES)], None, "1h", NOW, symbol="US500").accounts == []


def test_the_route_takes_a_symbol(tmp_path):
    from fastapi.testclient import TestClient

    from goldbot.api.app import create_app
    from tests.test_api_views import _login
    state = tmp_path / "state"
    state.mkdir()
    (state / f"engine_{SCALP}.json").write_text(json.dumps(_engine(key_zones=ZONES, ts=NOW.timestamp())))
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)
    v = c.get("/api/key-levels?tf=1h&symbol=US500", headers=h["viewer"]).json()
    assert v["symbol"] == "US500" and v["accounts"] == []

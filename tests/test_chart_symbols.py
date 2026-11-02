"""The trade chart of a non-gold instrument (multi-instrument demo, lane L4): its own bars in its own digits, never
the account's gold and never the gold reference feed.

Design rule: "store bars_1m_live keyed (source=account, symbol=store_symbol), never pooled with XAUUSD" (plan). (The
key-level chart this file also covered was removed by the owner's correction of 2026-11-02: levels and indicators are
for the system to learn from, not for charts to display.)"""
from __future__ import annotations

import pandas as pd

from goldbot.api import trade_chart
from goldbot.data.resample import BAR_COLUMNS
from goldbot.data.store import Store
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


LO, HI = NOW - pd.Timedelta(hours=6), NOW


def test_another_instruments_bars_are_its_own_in_its_digits(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m_broker", SCALP, NOW - pd.Timedelta(days=1), NOW)               # the account's gold
    _fx_minutes(store, "bars_1m_broker", SCALP, NOW - pd.Timedelta(days=1), NOW)
    gold, _ = trade_chart._bars(store, SCALP, "1h", LO, HI)
    fx, feed = trade_chart._bars(store, SCALP, "1h", LO, HI, "EURUSD")
    assert gold[-1].o > 2000 and feed == "broker"
    assert fx and all(1.0 < b.l < b.h < 1.1 for b in fx) and fx[-1].o == 1.08452    # five digits, never 1.08


def test_another_instrument_never_falls_back_to_the_gold_reference_feed(tmp_path):
    store = Store(tmp_path / "data")
    _minutes(store, "bars_1m", trade_chart.REFERENCE, NOW - pd.Timedelta(days=1), NOW)
    assert trade_chart._bars(store, SCALP, "1h", LO, HI, "US500") == ([], None)
    assert trade_chart._bars(store, SCALP, "1h", LO, HI)[1] == "reference"

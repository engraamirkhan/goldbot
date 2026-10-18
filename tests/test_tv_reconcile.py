"""TradingView cross-feed (goldbot/data/tv_reconcile.py): TV closes are a second feed against Dukascopy/broker M1, never
a primary bar source. A bar whose close is more than 2 points (strictly) from the reference is flagged `dq` and its
plots are NaN."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.data.crossfeed import CLOSE_TOL_POINTS, POINT_DEFAULT
from goldbot.data.quality import DQEvent
from goldbot.data.resample import BAR_COLUMNS
from goldbot.data.store import Store
from goldbot.data.tv_reconcile import (
    PULL_ORIGINS,
    apply_dq,
    compare_closes,
    confirm_spikes_with_tv,
    tv_other_feed,
)

T0 = pd.Timestamp("2026-10-13 10:00", tz="UTC")     # a Tuesday, London session
END = T0 + pd.Timedelta(minutes=30)
TOL = CLOSE_TOL_POINTS * POINT_DEFAULT              # 0.02


def _m1(closes: list[float]) -> pd.DataFrame:
    ts = pd.date_range(T0, periods=len(closes), freq="1min", tz="UTC")
    d = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(seconds=60)})
    for f in ("open", "high", "low", "close"):
        d[f"bid_{f}"] = closes
        d[f"ask_{f}"] = closes
    d["tick_count"], d["spread_mean"], d["spread_max"] = 1.0, 0.0, 0.0
    return d[BAR_COLUMNS]


def _long(closes: list[float], tf: str = "15", origin: str = "pull_live") -> pd.DataFrame:
    step = pd.Timedelta(minutes=int(tf))
    rows = []
    for i, c in enumerate(closes):
        bar = T0 + step * i
        for plot, v in (("close", c), ("rsi", 55.0 + i)):
            rows.append({"ts_utc": bar, "bar_time": bar, "tf": tf, "plot": plot, "value": v, "origin": origin,
                         "available_utc": bar + step, "indicator": "ind", "version": "1", "intrabar": False})
    return pd.DataFrame(rows)


def _flat_ref() -> pd.DataFrame:
    return _m1([2000.0] * 30)          # two 15m bars, both closing 2000.00


def test_close_diff_over_2_points_flags_and_nans_plot():
    long = _long([2000.03, 2000.0])                      # bar 1 is 3 points off
    cmp = compare_closes(long, _flat_ref(), tf="15", tolerance=TOL, start=T0, end=END)
    assert cmp["dq"].tolist() == [True, False]
    out = apply_dq(long, cmp)
    first = out[out["bar_time"] == T0]
    assert first["value"].isna().all() and (first["dq"] == "tv_close_diff").all()      # every plot of the bar
    second = out[out["bar_time"] == T0 + pd.Timedelta(minutes=15)]
    assert second["value"].notna().all() and (second["dq"] == "").all()


def test_exactly_2_points_is_kept():
    long = _long([2000.02, 2000.0])                      # exactly 2 points off
    cmp = compare_closes(long, _flat_ref(), tf="15", tolerance=TOL, start=T0, end=END)
    assert cmp["dq"].tolist() == [False, False]
    assert apply_dq(long, cmp)["value"].notna().all()


def test_only_pulled_rows_are_compared_webhook_rows_are_left_alone():
    long = pd.concat([_long([2000.5, 2000.0], origin="pull_history"), _long([2000.5, 2000.0], origin="webhook")])
    assert set(PULL_ORIGINS) == {"pull_live", "pull_history"}
    out = apply_dq(long, compare_closes(long, _flat_ref(), tf="15", tolerance=TOL, start=T0, end=END))
    assert out[out["origin"] == "webhook"]["value"].notna().all()
    assert out[(out["origin"] == "pull_history") & (out["bar_time"] == T0)]["value"].isna().all()


def test_bar_without_reference_is_not_flagged():
    cmp = compare_closes(_long([2000.5, 2000.0]), _m1([2000.0] * 15), tf="15", tolerance=TOL, start=T0, end=END)
    assert len(cmp) == 1 and bool(cmp["dq"].iloc[0])      # only bar 1 has a reference; bar 2 is not judged


def test_tv_feed_confirms_a_spike_the_other_feed_missed():
    ts = pd.date_range(T0, periods=3, freq="1min", tz="UTC")
    bars = pd.DataFrame({"ts_utc": ts, "bid_close": [2000.0, 2020.0, 2020.0], "ask_close": [2000.0, 2020.0, 2020.0],
                         "dq_flag": ["", "warning:spike", ""]})
    ev = [DQEvent(ts_utc=ts[1], check="spike", severity="warning", detail="x")]
    flat = bars.assign(bid_close=2000.0, ask_close=2000.0)
    seen = _long([2000.0, 2020.0, 2020.0], tf="1")
    _, kept = confirm_spikes_with_tv(bars, ev, flat, tv_other_feed(seen, tf="1"), tf_seconds=60)
    assert kept == []                                     # TV saw the move: a real market move, not a feed fault
    _, still = confirm_spikes_with_tv(bars, ev, flat, tv_other_feed(seen.iloc[:0], tf="1"), tf_seconds=60)
    assert len(still) == 1                                # no feed shows it: stays a warning


def test_job_rewrites_flagged_rows_in_the_store(tmp_path: Path):
    from goldbot.ops.jobs import tv_reconcile
    store = Store(tmp_path / "data")
    store.append("bars_1m", _flat_ref(), source="dukascopy")
    store.append("tv_indicators", _long([2000.5, 2000.0]), source="ind@1")
    ctx: Any = SimpleNamespace(store=store, state_dir=tmp_path, accounts=[])
    out = tv_reconcile(ctx, T0 + pd.Timedelta(hours=2))
    assert out["flagged"] == 1
    got = store.read("tv_indicators", source="ind@1")
    assert got[got["bar_time"] == T0]["value"].isna().all()
    assert got[got["bar_time"] != T0]["value"].notna().all()
    assert tv_reconcile(ctx, T0 + pd.Timedelta(hours=2))["flagged"] == 0       # idempotent
    assert store.read("tv_indicators", source="ind@1").query("bar_time == @T0")["value"].isna().all()

"""TF-2h (data-sufficiency item, 2026-11-26): 2h is a research cell timeframe (self_retrain.CELL_TFS, the sufficiency
contract, dl-v1 H_BY_TF) with no bar rule until now: tf_seconds refused it, so fs-v1 and dl-v1 skipped every 2h cell.
The rule is the intraday one: floor on the UTC clock, visible at the bar's natural close. 2h stays out of the
`Timeframe` / `DecisionTimeframe` literals, so no settings file, arm or live decision can name it (research only)."""
from __future__ import annotations

import typing

import numpy as np
import pandas as pd
import pytest

from goldbot.config import TF_SECONDS, DecisionTimeframe, Timeframe, tf_seconds
from goldbot.data.resample import IncrementalResampler, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import epoch_ns
from goldbot.features.price_action import decision_tf
from goldbot.research.dense_labels import H_BY_TF
from goldbot.research.self_retrain import CELL_TFS


@pytest.fixture(scope="module")
def m1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2026-10-05 22:00", "2026-10-08 21:00", ticks_per_minute=3, seed=3))


def test_every_cell_timeframe_has_a_bar_rule():
    assert tf_seconds("2h") == 7200
    assert set(CELL_TFS) <= set(TF_SECONDS) and set(H_BY_TF) <= set(TF_SECONDS)
    assert list(TF_SECONDS.values()) == sorted(TF_SECONDS.values())


def test_2h_stays_research_only():
    assert "2h" not in typing.get_args(Timeframe)
    assert "2h" not in typing.get_args(DecisionTimeframe)


def test_2h_bars_floor_on_even_utc_hours_and_aggregate_their_minutes(m1):
    out = resample_bars(m1, "2h")
    ts = pd.DatetimeIndex(out["ts_utc"])
    assert (epoch_ns(ts) % (7200 * 10**9) == 0).all()
    assert (out["visible_at"] == out["ts_utc"] + pd.Timedelta(hours=2)).all()
    key = pd.DatetimeIndex(m1["ts_utc"]).floor("2h")
    g = m1.groupby(key)
    for side in ("bid", "ask"):
        np.testing.assert_allclose(out[f"{side}_open"], g[f"{side}_open"].first().to_numpy())
        np.testing.assert_allclose(out[f"{side}_high"], g[f"{side}_high"].max().to_numpy())
        np.testing.assert_allclose(out[f"{side}_low"], g[f"{side}_low"].min().to_numpy())
        np.testing.assert_allclose(out[f"{side}_close"], g[f"{side}_close"].last().to_numpy())
    np.testing.assert_allclose(out["tick_count"], g["tick_count"].sum().to_numpy())


def test_2h_is_two_1h_bars(m1):
    h1, h2 = resample_bars(m1, "1h"), resample_bars(m1, "2h")
    from_1h = resample_bars(h1.assign(visible_at=h1["ts_utc"] + pd.Timedelta(minutes=1)), "2h")
    cols = ["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"]
    assert (from_1h["ts_utc"] == h2["ts_utc"]).all()
    np.testing.assert_allclose(from_1h[cols], h2[cols])


def test_a_late_minute_makes_only_its_2h_bar_late(m1):
    late = m1.copy()
    i = 700
    late.loc[i, "visible_at"] = pd.Timestamp("2026-10-09 15:00", tz="UTC")
    a, b = resample_bars(m1, "2h"), resample_bars(late, "2h")
    changed = a["visible_at"] != b["visible_at"]
    assert changed.sum() == 1
    assert b.loc[changed, "ts_utc"].iloc[0] == pd.Timestamp(m1.loc[i, "ts_utc"]).floor("2h")


def test_incremental_2h_matches_batch(m1):
    inc = IncrementalResampler("2h")
    inc(m1.iloc[: len(m1) // 2])
    got = inc(m1)
    pd.testing.assert_frame_equal(got.reset_index(drop=True), resample_bars(m1, "2h"))


def test_price_action_names_2h_spaced_bars(m1):
    assert decision_tf(pd.DatetimeIndex(resample_bars(m1, "2h")["ts_utc"])) == "2h"


def test_every_served_column_has_a_2h_lookback():
    """Quant verdict on #485: column_lookback(c, "2h") resolves for every SR_FEATURES column (d1_ columns read their
    base column's 1d lookback in daily bars), so a 2h read passes the ADR 0104 4 tier check instead of failing closed.
    X6 (PR #484) adds its own 2h bounds with r = 2 when it lands."""
    from goldbot.features.registry import column_lookback
    from goldbot.research.self_retrain import FEATURES as SR_FEATURES
    for c in SR_FEATURES:
        if c.startswith("d1_"):
            assert column_lookback(c[3:], "1d") >= 1
        else:
            assert column_lookback(c, "2h") >= 1, c

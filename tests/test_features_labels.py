import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import FEATURES, build_features, families
from goldbot.features.mtf import merge_higher_tf
from goldbot.features.technical import atr
from goldbot.labels import BarrierSpec, triple_barrier, uniqueness_weights
from goldbot.specialists import SPECIALISTS, AgentIdentity


@pytest.fixture(scope="module")
def frames():
    ticks = synthetic_ticks("2025-03-03", "2025-04-12", ticks_per_minute=2, seed=3)
    b1 = ticks_to_1m(ticks)
    b15, b1h, b1d = resample_bars(b1, "15m"), resample_bars(b1, "1h"), resample_bars(b1, "1d")
    return b15, b1h, b1d


def test_registry_has_all_families():
    fam = families()
    for f in ("volatility", "trend", "mean_reversion", "breakout", "microstructure", "structure", "calendar", "macro"):
        assert f in fam, f


def test_build_features_runs_and_is_versioned(frames):
    b15, _, _ = frames
    m = mid(b15)
    names = [n for n in FEATURES if n not in ("macro", "calendar_events")]
    X = build_features(m, names)
    assert len(X) == len(m)
    assert X.attrs["feature_version"].startswith("f-")
    for col in ("dist_res_atr", "dist_sup_atr", "gap_atr", "ribbon_state", "mfi12", "adx14", "bb_z_20", "session_id"):
        assert col in X.columns, col
    # features are finite once warmed up
    tail = X.iloc[-200:].drop(columns=["ts_utc"])
    assert tail.isna().mean().max() < 0.5


def test_support_resistance_uses_only_confirmed_swings(frames):
    """A level must not exist before its swing is confirmed (lag bars later)."""
    b15, _, _ = frames
    m = mid(b15).reset_index(drop=True)
    X = build_features(m, ["support_resistance"], ctx={"swing_lag": 5})
    # the first level can appear no earlier than bar 2*lag (need a full centered window + confirmation)
    hits = np.flatnonzero(X["levels_within_1atr"].gt(0).to_numpy())
    first = int(hits[0]) if len(hits) else None
    assert first is None or first >= 10


def test_mtf_merge_has_no_lookahead(frames):
    b15, b1h, _ = frames
    m15, m1h = mid(b15), mid(b1h)
    f1h = build_features(m1h, ["atr", "trend_strength"])
    merged = merge_higher_tf(m15[["ts_utc", "close"]], f1h, b1h, "h1")
    # pick a 15m bar at HH:15: the 1h bar opening at HH:00 is NOT visible yet; the one at HH-1:00 is
    row = merged[pd.DatetimeIndex(merged["ts_utc"]).minute == 15].iloc[50]
    t = row["ts_utc"]
    visible_until = t  # visible_at <= t
    # the latest 1h bar with visible_at <= t must open at least 1h before t
    latest = b1h[pd.to_datetime(b1h["visible_at"]) <= visible_until].iloc[-1]
    assert latest["ts_utc"] <= t - pd.Timedelta(hours=1)
    assert np.isclose(row["h1_atr14"], f1h.loc[latest.name, "atr14"], equal_nan=True)


def test_triple_barrier_charges_spread_and_respects_order(frames):
    b15, _, _ = frames
    b = b15.reset_index(drop=True)
    m = mid(b)
    a = atr(m, 14)
    sig = pd.DataFrame({"idx": np.arange(50, len(b) - 40, 37), "side": np.where(np.arange(50, len(b) - 40, 37) % 2 == 0, 1, -1)})
    spec = BarrierSpec(target_atr=1.5, stop_atr=1.0, max_bars=16)
    lab = triple_barrier(b, sig, spec, a)
    assert set(lab["label"].unique()) <= {-1, 0, 1}
    assert (lab["t_exit"] > lab["idx"]).all()
    assert (lab["bars_held"] <= 17).all()
    # longs enter at ask, shorts at bid
    longs = lab[lab["side"] == 1]
    assert np.allclose(longs["entry"].to_numpy(), b.loc[longs["idx"], "ask_close"].to_numpy())
    w = uniqueness_weights(lab, len(b))
    assert len(w) == len(lab) and np.isclose(w.mean(), 1.0)


def test_session_open_specialist_produces_candidates_at_open_times(frames):
    b15, _, _ = frames
    m = mid(b15).reset_index(drop=True)
    X = build_features(m, ["atr"])
    sp = SPECIALISTS["session_open"]()
    c = sp.candidates(m, X)
    assert len(c) > 0
    local_ldn = pd.DatetimeIndex(c["ts_utc"]).tz_convert("Europe/London")
    local_ny = pd.DatetimeIndex(c["ts_utc"]).tz_convert("America/New_York")
    ok = ((local_ldn.hour == 8) & (local_ldn.minute == 15)) | ((local_ny.hour == 8) & (local_ny.minute == 45))
    assert ok.all()


def test_agent_identity_clone_must_differ():
    ident = AgentIdentity(family="session_open", config={"target_atr": 1.5})
    child = ident.mutate({"target_atr": 1.75})
    assert child.parent_id == ident.agent_id and child.generation == 1 and child.agent_id != ident.agent_id
    with pytest.raises(ValueError):
        ident.mutate({"target_atr": 1.5})

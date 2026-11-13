"""Order-flow proxy features (R139, plan item 8): quote-update-weighted return scores, signed runs and spread dynamics
from Dukascopy 1m bars; every value joins the decision bars through `asof_join` on the 1m bar's `visible_at`, never
on a later minute. No fill column (quant review 2026-11-13: pooled across accounts, endogenous, a date marker).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data import orderflow as of
from goldbot.features import orderflow as of_feat
from goldbot.features import registry

T0 = pd.Timestamp("2026-03-02 00:00", tz="UTC")


def _m1(n: int, *, step: float = 0.0, seed: int = 1, start: pd.Timestamp = T0) -> pd.DataFrame:
    """Store-schema 1m bars (bid/ask), a random walk plus `step` per minute."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=n, freq="1min")
    mid = 2000.0 + np.cumsum(rng.normal(step, 0.1, n))
    spread = 0.20 + 0.02 * rng.random(n)
    df = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for c in ("open", "high", "low", "close"):
        df[f"bid_{c}"] = mid - spread / 2
        df[f"ask_{c}"] = mid + spread / 2
    df["tick_count"] = 50.0 + 10 * rng.random(n)
    df["spread_mean"] = spread
    df["spread_max"] = spread * 1.5
    return df


def _bars(start: pd.Timestamp, n: int, tf: str = "1h") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=tf)
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(tf), "open": 2000.0, "high": 2001.0,
                         "low": 1999.0, "close": 2000.0, "spread": 0.2, "tick_count": 100.0})


def test_rising_minutes_give_positive_imbalance_falling_negative():
    up = of.orderflow_frame(_m1(600, step=0.08))
    down = of.orderflow_frame(_m1(600, step=-0.08))
    assert up["of_vw_retz_60m"].iloc[-1] > 0.3
    assert down["of_vw_retz_60m"].iloc[-1] < -0.3
    assert (up["available_utc"] == _m1(600)["visible_at"]).all()


def test_imbalance_is_bounded():
    f = of.orderflow_frame(_m1(2000, seed=3))
    for c in ("of_vw_retz_15m", "of_vw_retz_60m"):
        v = f[c].dropna()
        assert ((v >= -1) & (v <= 1)).all()


def test_signed_run_follows_the_current_run_of_same_direction_minutes():
    df = _m1(300, seed=4)
    mid = 2000.0 + np.r_[np.zeros(280), np.arange(1, 21) * 0.5]       # flat, then 20 rising minutes
    for c in ("open", "high", "low", "close"):
        df[f"bid_{c}"], df[f"ask_{c}"] = mid - 0.1, mid + 0.1
    f = of.orderflow_frame(df)
    assert f["of_vw_run"].iloc[-1] > 0
    assert f["of_vw_run"].iloc[-1] > f["of_vw_run"].iloc[-10]


def test_spread_widening_shows_in_spread_dynamics():
    df = _m1(3000, seed=5)
    df.loc[df.index[-15:], "spread_mean"] = 1.0
    f = of.orderflow_frame(df)
    assert f["of_spread_rel_15m"].iloc[-1] > 2.0
    assert f["of_spread_chg_60m"].iloc[-1] > 1.0
    assert f["of_spread_rel_15m"].iloc[-200] == pytest.approx(1.0, abs=0.2)


def test_minutes_without_volume_are_missing_not_quiet():
    df = _m1(300, seed=6)
    df.loc[df.index[-20:], "tick_count"] = 0.0
    f = of.orderflow_frame(df)
    assert np.isnan(f["of_vw_retz_15m"].iloc[-1])
    assert np.isnan(f["of_tick_rate_15m"].iloc[-1])


def test_mid_schema_input_is_accepted():
    df = _m1(200, seed=7)
    mid = pd.DataFrame({"ts_utc": df["ts_utc"], "visible_at": df["visible_at"],
                        "close": (df["bid_close"] + df["ask_close"]) / 2, "spread": df["spread_mean"],
                        "tick_count": df["tick_count"]})
    a, b = of.orderflow_frame(df), of.orderflow_frame(mid)
    pd.testing.assert_series_equal(a["of_vw_retz_60m"], b["of_vw_retz_60m"])


def test_no_fill_column_and_fills_in_ctx_change_nothing():
    """Quant review 2026-11-13: fill slippage pooled every account and symbol, is endogenous to our own trading and
    is NaN before October 2026 (a date marker), so it is not a column."""
    assert not any("fill" in c or "slip" in c for c in of_feat.COLUMNS)
    assert not hasattr(of, "fill_slippage_frame")
    name, feats, signed = _enable()
    try:
        m1, bars = _m1(180, seed=11), _bars(T0, 3)
        fills = pd.DataFrame({"ts_utc": [T0], "side": [1], "requested": [2000.0], "filled": [2000.5]})
        a = registry.build_features(bars, [name], ctx={"bars_1m": m1})
        b = registry.build_features(bars, [name], ctx={"bars_1m": m1, "fills": fills})
        pd.testing.assert_frame_equal(a, b)
    finally:
        _restore(feats, signed)


def test_names_say_what_the_columns_are_and_the_screen_note_sets_the_bar():
    """Dukascopy's tick_count is the bid side's quote updates, not signed trades: the columns are quote-update-weighted
    return scores, and a survivor must beat the plain return and momentum features."""
    assert {"of_vw_retz_15m", "of_vw_retz_60m", "of_vw_run"} <= set(of_feat.COLUMNS)
    assert not any(c.startswith("of_imb") or c == "of_run" for c in of_feat.COLUMNS)
    note = of_feat.SCREEN_NOTE
    assert "quote updates" in note and "momentum" in note and "return" in note


def _enable():
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    return of_feat.enable(), feats, signed


def _restore(feats: dict, signed: dict) -> None:
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def test_feature_is_truncation_invariant_a_later_minute_changes_no_earlier_bar():
    """Seed 1m bars after the cut with an extreme move: every decision bar closed by the cut is bit-identical."""
    name, feats, signed = _enable()
    try:
        m1 = _m1(48 * 60, seed=8)
        bars = _bars(T0, 48)
        cut = bars["visible_at"].iloc[23]
        a = registry.build_features(bars, [name], ctx={"bars_1m": m1[m1["visible_at"] <= cut]})
        shock = m1.copy()
        later = shock["visible_at"] > cut
        shock.loc[later, ["bid_close", "ask_close"]] += np.arange(later.sum())[:, None] * 0.5
        shock.loc[later, "spread_mean"] = 2.0
        b = registry.build_features(bars, [name], ctx={"bars_1m": shock})
        early = bars["visible_at"] <= cut
        pd.testing.assert_frame_equal(a[early], b[early])
        assert b.loc[~early, "of_vw_retz_60m"].notna().any()
    finally:
        _restore(feats, signed)


def test_the_bar_sees_its_own_last_minute_but_no_later_one():
    name, feats, signed = _enable()
    try:
        m1 = _m1(180, seed=9)
        bars = _bars(T0, 3)
        x = registry.build_features(bars, [name], ctx={"bars_1m": m1})
        f = of.orderflow_frame(m1).set_index("available_utc")
        assert x["of_vw_retz_60m"].iloc[1] == pytest.approx(f.loc[bars["visible_at"].iloc[1], "of_vw_retz_60m"])
    finally:
        _restore(feats, signed)


def test_a_1m_data_gap_reads_nan_not_the_stale_value():
    name, feats, signed = _enable()
    try:
        m1 = _m1(120, seed=10)
        bars = _bars(T0, 6)
        x = registry.build_features(bars, [name], ctx={"bars_1m": m1})
        assert x["of_vw_retz_60m"].iloc[1] == x["of_vw_retz_60m"].iloc[1]
        assert x[list(of_feat.COLUMNS)].iloc[2:].isna().all().all()
    finally:
        _restore(feats, signed)


def test_no_inputs_give_the_fixed_column_set_all_nan_and_mfe_mae_are_not_built():
    name, feats, signed = _enable()
    try:
        x = registry.build_features(_bars(T0, 4), [name])
        assert list(x.columns[1:]) == list(of_feat.COLUMNS)
        assert x[list(of_feat.COLUMNS)].isna().all().all()
        assert not any("mfe" in c or "mae" in c for c in of_feat.COLUMNS)
    finally:
        _restore(feats, signed)


@pytest.mark.integration
def test_research_frame_passes_the_pipelines_lookahead_check():
    """The pipeline's own leakage check (history and the 1m table cut at 70% by available_utc)."""
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import build_decision_frame, lookahead_check
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        name = of_feat.enable()
        m1 = ticks_to_1m(synthetic_ticks("2025-03-03", "2025-03-29", ticks_per_minute=2, seed=7))
        bars = resample_bars(m1, "1h").reset_index(drop=True)
        ctx = {"bars_1m": of.ctx_frame(m1)}
        out = lookahead_check(bars, None, feature_names=[name], ctx=ctx)
        assert out["lookahead_columns"] == [] and out["columns_checked"] == len(of_feat.COLUMNS)
        _, full = build_decision_frame(bars, None, [name], ctx)
        assert full["of_vw_retz_60m"].notna().mean() > 0.8
    finally:
        _restore(feats, signed)


def test_research_pass_adds_orderflow_only_when_given():
    import importlib.util
    from pathlib import Path

    from goldbot.research.pipeline import research_feature_names
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        spec = importlib.util.spec_from_file_location(
            "research_pass", Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        m1 = _m1(120)
        ctx, names = mod.feature_ctx(None, None, orderflow_bars=m1)
        assert ctx is not None and names == [*research_feature_names(ctx), of_feat.NAME]
        assert (ctx["bars_1m"]["available_utc"] == m1["visible_at"]).all()
    finally:
        _restore(feats, signed)


def test_opt_in_and_not_in_the_default_set():
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES
    assert of_feat.NAME in OPT_IN_FEATURES and of_feat.NAME not in DEFAULT_FEATURE_NAMES

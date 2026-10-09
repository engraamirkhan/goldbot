"""Primary-signal screen (P4), pooled meta-model inputs and frames (P5), and the expanding walk-forward window."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.research.model import MAX_FEATURES
from goldbot.research.pipeline import (
    FAMILY_PREFIX,
    POOLED_FEATURES,
    build_decision_frame,
    pool_frames,
    pool_identity,
    pooled_inputs,
    pooled_members,
    prepare,
    run_pool,
)
from goldbot.research.registry import TrialRegistry, quarter_of
from goldbot.research.screen import MIN_EVENTS, MIN_T, screen, screen_lines, screen_verdict
from goldbot.research.walkforward import WINDOWS, splits_for, window_for
from goldbot.specialists import SPECIALISTS


def _rule(n: int, mean_r: float, t: float) -> dict:
    return {"gross": {"n": n, "mean_r": mean_r, "t_stat": t}, "net": {"n": n, "mean_r": mean_r - 0.2, "t_stat": t - 3}}


def test_screen_passes_only_a_positive_significant_rule_on_enough_events():
    assert screen_verdict(_rule(MIN_EVENTS, 0.05, MIN_T))["passed"] is True
    assert screen_verdict(_rule(MIN_EVENTS - 1, 0.05, 3.0))["passed"] is False          # too few events
    assert screen_verdict(_rule(5000, 0.05, MIN_T - 0.01))["passed"] is False           # not significant
    assert screen_verdict(_rule(5000, -0.05, 3.0))["passed"] is False                   # negative edge
    assert screen_verdict({"gross": {"n": 0}, "net": {"n": 0}})["passed"] is False
    v = screen_verdict(_rule(1200, 0.08, 2.4))
    assert v["net_mean_r"] == pytest.approx(-0.12) and v["gross_t"] == 2.4             # net is reported, not decisive
    text = "\n".join(screen_lines(screen_verdict(_rule(10, -0.1, -1.0))))
    assert "screen failed, no model fitted" in text and "FAIL events" in text
    assert "(skipped with --skip-screen)" in "\n".join(screen_lines(screen_verdict(_rule(10, -0.1, -1.0)), skipped=True))


@pytest.fixture(scope="module")
def bars_1m() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2024-01-01", "2024-03-15", ticks_per_minute=1, seed=21))


def _frame(bars_1m: pd.DataFrame, tf: str):
    dec = resample_bars(bars_1m, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(bars_1m, x) for x in context_tfs(tf)}
    return dec, context, build_decision_frame(dec, context)


@pytest.mark.parametrize("tf", sorted(POOLED_FEATURES))
def test_pooled_inputs_exist_and_fit_the_cap(bars_1m, tf):
    members = pooled_members(tf)
    assert len(members) >= 3 and all(SPECIALISTS[f].timeframe == tf for f in members)
    _, _, (_, X) = _frame(bars_1m, tf)
    declared = POOLED_FEATURES[tf]
    assert len(declared) == len(set(declared)) and [c for c in declared if c not in X.columns] == []
    cols = pooled_inputs(tf, members, [c for c in X.columns if c != "ts_utc"])
    assert cols[0] == "side" and cols[1:1 + len(members)] == [f"{FAMILY_PREFIX}{f}" for f in members]
    assert len(cols) <= MAX_FEATURES
    with pytest.raises(ValueError, match="cap is 40"):
        pooled_inputs(tf, [f"fam{i}" for i in range(10)], list(declared))


def test_pool_frames_unite_the_families_in_time_order_with_indicators(bars_1m):
    dec, context, frame = _frame(bars_1m, "15m")
    specs = [SPECIALISTS["intraday_momentum"](), SPECIALISTS["mean_reversion"](band_z=1.0, rsi_low=45.0, rsi_high=55.0,
                                                                                  max_vol_tercile=2)]
    preps = [prepare(s, dec, context, extra_cost_usd=0.3, frame=frame) for s in specs]
    assert all(len(p.labels) > 10 for p in preps)
    labels, feats, gross = pool_frames(preps)
    assert len(labels) == len(feats) == sum(len(p.labels) for p in preps)
    assert pd.DatetimeIndex(labels["ts_utc"]).is_monotonic_increasing
    for fam in ("intraday_momentum", "mean_reversion"):
        ind = feats[f"{FAMILY_PREFIX}{fam}"].to_numpy()
        assert set(ind) == {0.0, 1.0} and (ind == 1.0).sum() == (labels["family"] == fam).sum()
        assert (ind[(labels["family"] == fam).to_numpy()] == 1.0).all()
    assert set(gross["family"]) == {"intraday_momentum", "mean_reversion"}
    assert {"risk", "atr_sig", "target_atr", "stop_atr"} <= set(labels.columns)
    # each member keeps its own barriers (thresholds and R use them)
    assert set(labels.loc[labels["family"] == "intraday_momentum", "stop_atr"]) == {2.0}
    # the union screen sees every member's events, and reports each member alone
    s = screen(preps)
    assert s["n"] == len(gross) and set(s["members"]) == {"intraday_momentum", "mean_reversion"}
    ident = pool_identity("15m", preps)
    assert ident.family == "pooled_15m" and set(ident.config["families"]) == {"intraday_momentum", "mean_reversion"}
    with pytest.raises(ValueError, match="must decide on 1h"):
        run_pool(preps, "1h")


def test_expanding_window_trains_on_all_history_and_session_open_uses_it():
    ts = pd.date_range("2010-01-01", "2016-01-01", freq="1D", tz="UTC")
    labels = pd.DataFrame({"ts_utc": ts, "ts_exit": ts + pd.Timedelta(hours=4)})
    rolling = splits_for(labels, "15m")
    expanding = splits_for(labels, "15m", expanding=True, test_months=6, step_months=6)
    assert all(f.train_idx.min() == 0 for f in expanding)                     # every fold starts at the first label
    assert rolling[-1].train_idx.min() > 0
    assert len(expanding[-1].train_idx) > len(rolling[-1].train_idx)
    sizes = [len(f.test_idx) for f in expanding if f.complete]
    assert min(sizes) >= 180                                                   # 6-month test folds of daily labels
    assert all((f.test_end - f.test_start).days >= 180 for f in expanding if f.complete)
    so = SPECIALISTS["session_open"]
    assert window_for(so.timeframe, **so.walkforward) == {**WINDOWS["15m"], "expanding": True, "test_months": 6,
                                                           "step_months": 6}
    assert "4h" in WINDOWS and window_for("1h") == WINDOWS["1h"]


def test_a_screened_trial_counts_in_the_budget_but_never_as_passed(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    cfg = SPECIALISTS["tsmom"]().config
    reg.record(agent_id="a", family="tsmom", config=cfg, feature_version="f", rationale="screen",
               results={"screen": screen_verdict(_rule(10, 0.1, 0.5))}, status="screened")
    assert reg.budget_used(quarter_of()) == 1 and not reg.passed_gates("tsmom", cfg)


def test_prepare_drops_the_holdout_and_keeps_the_rule_only_inputs(bars_1m):
    dec, context, frame = _frame(bars_1m, "15m")
    start = pd.Timestamp("2024-02-15", tz="UTC")
    p = prepare(SPECIALISTS["intraday_momentum"](), dec, context, extra_cost_usd=0.3,
                holdout=(start, pd.Timestamp("2024-03-01", tz="UTC")), frame=frame)
    assert pd.to_datetime(p.labels["ts_exit"], utc=True).max() < start
    assert pd.to_datetime(p.gross["ts_exit"], utc=True).max() < start
    assert np.isfinite(p.gross["risk"]).all() and (p.feats["side"].to_numpy() == p.labels["side"].to_numpy()).all()

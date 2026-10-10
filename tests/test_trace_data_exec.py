"""Traceability (docs/TRACEABILITY.md): data-quality checks on ingest, the account classifier's buckets, and the
design's specialist, walk-forward and label numbers."""
import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.quality import check_bars, stale_feed
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.execution.classifier import classify
from goldbot.research.model import MetaLabelModel
from goldbot.research.walkforward import WINDOWS
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import REASON_CODES, Proposal


@pytest.fixture(scope="module")
def bars1m() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=3))


# ------------------------------------------------------------------------------------------- quality on ingest
def test_non_monotonic_batch_is_an_error(bars1m):
    b = bars1m.copy()
    i = len(b) // 2
    b.iloc[[i, i + 1]] = b.iloc[[i + 1, i]].to_numpy()       # two bars arrive out of order
    _, events = check_bars(b)
    assert any(e.check == "non_monotonic" and e.severity == "error" for e in events)
    _, clean = check_bars(bars1m)
    assert not any(e.check == "non_monotonic" for e in clean)


def test_duplicate_timestamps_are_an_error(bars1m):
    b = pd.concat([bars1m, bars1m.tail(1)], ignore_index=True)
    flagged, events = check_bars(b)
    assert any(e.check == "duplicate_ts" and e.severity == "error" for e in events)


def test_stale_feed_is_90s_in_session_only():
    t = pd.Timestamp("2025-03-05 13:00", tz="UTC")
    assert not stale_feed(t, t + pd.Timedelta(seconds=90))
    assert stale_feed(t, t + pd.Timedelta(seconds=91))
    sat = pd.Timestamp("2025-03-08 12:00", tz="UTC")
    assert not stale_feed(sat - pd.Timedelta(hours=5), sat)


# ------------------------------------------------------------------------------------------- account classifier
def _ticks(spread: float) -> pd.DataFrame:
    ts = pd.date_range("2025-03-05 09:00", periods=3000, freq="1s", tz="UTC")
    rng = np.random.default_rng(0)
    return pd.DataFrame({"ts_utc": ts, "bid": 2400.0, "ask": 2400.0 + spread + rng.random(3000) * 0.02})


def test_tight_spread_reads_raw_only_without_deal_history():
    # design: "a median under $0.20 with floating spread and no deal history also reads Raw; anything in between is
    # Unknown" -- zero-commission deals with a tight spread fit neither Raw rule, so the account stays Unknown (paper)
    zero_comm = pd.DataFrame({"commission": [0.0, 0.0]})
    assert classify(_ticks(0.15), pd.DataFrame()).account_class == "raw"
    assert classify(_ticks(0.15), zero_comm).account_class == "unknown"
    assert classify(_ticks(0.35), zero_comm).account_class == "standard"
    assert classify(_ticks(0.24), pd.DataFrame()).account_class == "unknown"


# ------------------------------------------------------------------------------------------- design tables
@pytest.mark.parametrize("family,tf,target,stop,max_bars", [
    ("trend", "1h", 2.5, 1.25, 48),             # 48 h
    ("mean_reversion", "15m", 1.0, 1.5, 12),
    ("breakout", "1h", 2.0, 1.0, 24),           # 24 h
    ("session_open", "15m", 1.5, 1.0, 16),
])
def test_specialist_barriers_match_the_modelling_table(family, tf, target, stop, max_bars):
    s = SPECIALISTS[family]()
    ls = s.label_spec
    assert (s.timeframe, ls.target_atr, ls.stop_atr, ls.max_bars) == (tf, target, stop, max_bars)


def test_walk_forward_windows_match_design_and_settings():
    s = load_settings()
    design = {"15m": (24, 3, 3, 2, 1), "1h": (36, 6, 6, 5, 2), "4h": (48, 6, 6, 10, 4)}   # 4h: proposal P4
    assert set(s.walkforward) == set(design)             # 1d (WINDOWS["1d"], expanding) stays research-only
    for tf, (tr, te, st, purge, emb) in design.items():
        w = WINDOWS[tf]
        assert (w["train_months"], w["test_months"], w["step_months"], w["purge_days"], w["embargo_days"]) == (tr, te, st, purge, emb)
        cfg = s.walkforward[tf]  # type: ignore[index]
        assert (cfg.train_months, cfg.test_months, cfg.step_months) == (tr, te, st)
        assert (s.labels.purge_days[tf], s.labels.embargo_days[tf]) == (purge, emb)  # type: ignore[index]


def test_purge_is_at_least_every_label_horizon():
    for cls in SPECIALISTS.values():
        s = cls()
        horizon_days = s.label_spec.max_bars * {"15m": 900, "1h": 3600}[s.timeframe] / 86400
        assert WINDOWS[s.timeframe]["purge_days"] >= horizon_days, s.family


def test_live_models_are_capped_at_40_features():
    assert load_settings().features.max_live_features == 40
    X = pd.DataFrame(np.zeros((10, 41)), columns=[f"f{i}" for i in range(41)])
    with pytest.raises(ValueError, match="40 features"):
        MetaLabelModel(feature_names=list(X.columns)).fit(X, pd.Series([0, 1] * 5))


def test_approval_window_and_reason_codes():
    assert set(REASON_CODES) == {"news", "cost", "discretion", "duplicate", "other"}
    p = Proposal(proposal_id="x", account_id="a", agent_id="g", side=1, lots=0.1, entry=1, stop=0.5, target=2, p=0.6,
                 ev_r=0.1, spread_points=20, top_features=[])
    assert p.window_s == load_settings().risk.approval_window_seconds == 90

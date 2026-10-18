"""`tv_plots` feature: catalogued TradingView plots as known at each bar, point in time, capped and repaint-safe."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data import tv_catalog as cat
from goldbot.features import tv_plots as tvp
from goldbot.features.registry import FEATURES
from goldbot.research.model import MAX_FEATURES, MetaLabelModel

T0 = pd.Timestamp("2026-10-12T08:00:00Z")
M15 = pd.Timedelta(minutes=15)


def meta():
    return cat.StudyMeta(study="K-Indi", version="2.02", tf="15", inputs={"in_0": 50},
                         plots=[cat.StudyPlot(id="plot_0", type="line", title="MFI"),
                                cat.StudyPlot(id="plot_1", type="shapes", title="Buy large")])


def specs():
    n = 30
    close = np.full(n, 2600.0)
    return cat.catalog(meta(), pd.DataFrame({"close": close, "plot_0": np.linspace(20, 80, n)}))


def rows(bars, *, origin="pull_live", validated=None, lag=pd.Timedelta(seconds=5), value=None, meta_hash=None):
    """Long tv_indicators rows for bar indices `bars` (plot_0 = 10 + i, plot_1 fires on even bars)."""
    out = []
    for i in bars:
        bar = T0 + i * M15
        for plot, v in (("plot_0", 10.0 + i if value is None else value), ("plot_1", 1.0 if i % 2 == 0 else np.nan)):
            out.append({"indicator": "K-Indi", "version": "2.02", "tf": "15", "bar_time": bar,
                        "available_utc": bar + M15 + lag, "intrabar": False, "plot": plot, "value": v,
                        "origin": origin, "repaint_validated": validated,
                        "meta_hash": meta_hash or meta().meta_hash})
    return pd.DataFrame(out)


def frame(ts):
    return pd.DataFrame({"ts_utc": pd.DatetimeIndex(ts), "close": 2600.0})


def col(plot="plot_0"):
    return cat.feature_id(meta(), plot)


def test_visible_at_available_utc_not_1ns_before():
    long = rows([0])
    avail = long["available_utc"].iloc[0]
    out = tvp.f_tv_plots(frame([avail - pd.Timedelta(1, "ns"), avail]), {"tv_indicators": long, "tv_catalog": specs()})
    assert pd.isna(out[col()].iloc[0]) and out[col()].iloc[1] == 10.0
    assert out[col("plot_1")].tolist()[1] == 1.0                     # the shape fired on bar 0 (an event, 1/0)
    assert pd.isna(out[col("plot_1")].iloc[0])


def test_unverified_history_never_trains():
    ctx_specs = specs()
    t = T0 + 2 * M15 + pd.Timedelta(minutes=2)                       # bar 1 closed 2 min ago
    for origin in ("csv", "pull_history"):
        hist = rows([0, 1], origin=origin, validated=False, lag=pd.Timedelta(0))
        out = tvp.f_tv_plots(frame([t]), {"tv_indicators": hist, "tv_catalog": ctx_specs, "tv_training": True})
        assert out[col()].isna().all(), origin                         # unverified: never a value
        ok = rows([0, 1], origin=origin, validated=True, lag=pd.Timedelta(0))
        out = tvp.f_tv_plots(frame([t]), {"tv_indicators": ok, "tv_catalog": ctx_specs})
        assert out[col()].tolist() == [11.0], origin                   # verified history is used
    # an origin nobody declared is not trusted either
    odd = rows([1], origin="scraped", validated=True, lag=pd.Timedelta(0))
    assert tvp.f_tv_plots(frame([t]), {"tv_indicators": odd, "tv_catalog": ctx_specs})[col()].isna().all()
    # rows of another input set (meta hash) never feed this plot's column
    other = rows([1], meta_hash="deadbeef", lag=pd.Timedelta(0))
    assert tvp.f_tv_plots(frame([t]), {"tv_indicators": other, "tv_catalog": ctx_specs})[col()].isna().all()
    # a live row wins over verified history on the same bar
    both = pd.concat([rows([1], origin="pull_history", validated=True, value=99.0, lag=pd.Timedelta(0)), rows([1])])
    assert tvp.f_tv_plots(frame([t]), {"tv_indicators": both, "tv_catalog": ctx_specs})[col()].tolist() == [11.0]


def test_missing_bar_is_nan_not_stale():
    long = rows([0, 1, 3])                                            # bar 2 never arrived
    closes = [T0 + (i + 1) * M15 + pd.Timedelta(minutes=2) for i in range(4)]
    out = tvp.f_tv_plots(frame(closes), {"tv_indicators": long, "tv_catalog": specs()})
    assert out[col()].iloc[0] == 10.0 and out[col()].iloc[1] == 11.0
    assert pd.isna(out[col()].iloc[2]) and pd.isna(out[col("plot_1")].iloc[2])   # bar 1 is not carried forward
    assert out[col()].iloc[3] == 13.0
    # at the close itself, before the alert's few seconds of latency, the previous bar is still the latest: one bar
    # of delivery lag is allowed, two are not
    at_close = tvp.f_tv_plots(frame([T0 + 2 * M15]), {"tv_indicators": long, "tv_catalog": specs()})
    assert at_close[col()].tolist() == [10.0]
    # 1 % seeded blanking in training only: deterministic, about 1 % of bars, whole bars at once
    many = rows(range(4000))
    ts = [T0 + (i + 1) * M15 + pd.Timedelta(minutes=1) for i in range(4000)]
    train = tvp.f_tv_plots(frame(ts), {"tv_indicators": many, "tv_catalog": specs(), "tv_training": True})
    again = tvp.f_tv_plots(frame(ts), {"tv_indicators": many, "tv_catalog": specs(), "tv_training": True})
    live = tvp.f_tv_plots(frame(ts), {"tv_indicators": many, "tv_catalog": specs()})
    blanked = train[col()].isna()
    assert 0.005 < blanked.mean() < 0.02 and blanked.equals(again[col()].isna())
    assert train.loc[blanked, col("plot_1")].isna().all() and not live[col()].isna().any()


def test_more_than_8_tv_columns_refused():
    nine = cat.StudyMeta(study="K-Indi", version="2.02", tf="15",
                         plots=[cat.StudyPlot(id=f"plot_{i}", type="shapes", title="Buy") for i in range(9)])
    many = cat.catalog(nine)
    with pytest.raises(ValueError, match="8"):
        tvp.f_tv_plots(frame([T0]), {"tv_indicators": rows([0]), "tv_catalog": many})
    names = [s.feature_id for s in many] + ["side"]
    with pytest.raises(ValueError, match="8"):
        tvp.check_model_columns(names)
    with pytest.raises(ValueError, match="8"):
        MetaLabelModel(feature_names=names).fit(pd.DataFrame(), pd.Series(dtype=float))
    tvp.check_model_columns(names[1:])                                 # 8 is allowed


def test_tv_plus_others_capped_at_40():
    tv = [s.feature_id for s in specs()]
    others = [f"x{i}" for i in range(MAX_FEATURES - len(tv) - 1)] + ["side"]
    tvp.check_model_columns(tv + others)                               # exactly 40
    with pytest.raises(ValueError, match="40"):
        tvp.check_model_columns(tv + others + ["x_extra"])


def test_tv_plots_is_opt_in_and_registered_as_v1():
    assert tvp.NAME not in FEATURES
    try:
        assert tvp.enable() == "tv_plots" and FEATURES["tv_plots"].version == "1"
        assert FEATURES["tv_plots"].family == "tradingview"
        assert tvp.f_tv_plots(frame([T0]), {}).shape == (1, 0)
    finally:
        FEATURES.pop("tv_plots", None)

"""`tv_plots` feature: catalogued TradingView plots as known at each bar, point in time, capped and repaint-safe."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data import tv_catalog as cat
from goldbot.features import tradingview as tvf
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


def test_history_and_live_rows_of_the_same_bar_give_equal_values_at_the_bar_open():
    # decision rows are stamped at the bar open (= the TradingView bar's close); history is available exactly at the
    # close, live a few seconds later: both must show the same (previous) bar there, or training sees what live cannot
    opens = [T0 + (i + 1) * M15 for i in range(1, 6)]
    hist = rows(range(6), origin="pull_history", validated=True, lag=pd.Timedelta(0))
    live = rows(range(6), lag=pd.Timedelta(seconds=5))
    a = tvp.f_tv_plots(frame(opens), {"tv_indicators": hist, "tv_catalog": specs()})
    b = tvp.f_tv_plots(frame(opens), {"tv_indicators": live, "tv_catalog": specs()})
    pd.testing.assert_frame_equal(a, b)
    assert a[col()].tolist() == [10.0, 11.0, 12.0, 13.0, 14.0]


def test_a_blanked_bar_is_nan_at_the_bar_open_not_the_previous_bar():
    many = rows(range(4000), lag=pd.Timedelta(0))
    opens = [T0 + (i + 1) * M15 for i in range(4000)]
    ctx = {"tv_indicators": many, "tv_catalog": specs()}
    live = tvp.f_tv_plots(frame(opens), ctx)
    train = tvp.f_tv_plots(frame(opens), {**ctx, "tv_training": True})
    blank = train[col()].isna() & live[col()].notna()
    assert 0.005 < blank.mean() < 0.02
    assert train.loc[~blank, col()].equals(live.loc[~blank, col()])        # never another bar's value
    assert train.loc[blank, col("plot_1")].isna().all()                    # an event is NaN too, not "did not fire"


def test_rows_without_intrabar_and_digit_plot_ids_are_read():
    long = rows([0]).drop(columns=["intrabar"]).assign(plot=lambda d: d["plot"].str.removeprefix("plot_"))
    t = T0 + M15 + pd.Timedelta(minutes=2)
    out = tvp.f_tv_plots(frame([t]), {"tv_indicators": long, "tv_catalog": specs()})
    assert out[col()].tolist() == [10.0] and out[col("plot_1")].tolist() == [1.0]
    digit = cat.StudyMeta(study="K-Indi", version="2.02", tf="15", inputs={"in_0": 50},
                          plots=[cat.StudyPlot(id="1", type="shapes", title="Buy")])
    (spec,) = cat.catalog(digit)
    out = tvp.f_tv_plots(frame([t]), {"tv_indicators": long.assign(meta_hash=digit.meta_hash), "tv_catalog": [spec]})
    assert out[spec.feature_id].tolist() == [1.0]


def test_an_unknown_timeframe_is_refused():
    with pytest.raises(ValueError, match="timeframe"):
        cat.StudyMeta(study="K-Indi", version="2.02", tf="4H")
    bad = specs()[0].model_copy(update={"tf": "4H"})
    with pytest.raises(ValueError, match="timeframe"):
        tvp.f_tv_plots(frame([T0]), {"tv_indicators": rows([0]), "tv_catalog": [bad]})


def test_rows_without_this_input_set_never_feed_the_column():
    t = T0 + M15 + pd.Timedelta(minutes=2)
    for h in (None, "deadbeef"):
        long = rows([0]).assign(meta_hash=h)
        assert tvp.f_tv_plots(frame([t]), {"tv_indicators": long, "tv_catalog": specs()})[col()].isna().all(), h


def test_legacy_tradingview_feature_reads_pulled_history_as_history_not_live():
    hist = rows([0, 1], origin="pull_history", validated=False, lag=pd.Timedelta(0))
    assert tvf.wide(hist, "15").empty                                    # unverified: left out
    assert tvf.label_for(tvf.wide(hist, "15", include_unvalidated=True)) == tvf.UNVALIDATED
    both = pd.concat([rows([1], origin="pull_history", validated=True, value=99.0, lag=pd.Timedelta(0)), rows([1])])
    assert tvf.wide(both, "15")["plot_0"].tolist() == [11.0]             # the live row wins its bar
    assert tvf.wide(rows([1], origin="scraped", validated=True), "15").empty


def test_legacy_tradingview_feature_keeps_input_sets_and_versions_apart():
    t = T0 + 2 * M15 + pd.Timedelta(minutes=2)
    long = pd.concat([rows([1], meta_hash="aaaaaaaa"), rows([1], meta_hash="bbbbbbbb", value=50.0)])
    out = tvf.f_tradingview(frame([t]), {"tv_indicators": long})
    assert out["tv_k_indi_aaaaaaaa_15_plot_0"].tolist() == [11.0]
    assert out["tv_k_indi_bbbbbbbb_15_plot_0"].tolist() == [50.0]
    alerts = pd.concat([rows([1]).assign(meta_hash=None, origin=None),
                        rows([1], value=50.0).assign(meta_hash=None, origin=None, version="2.03")])
    with pytest.raises(ValueError, match="version"):
        tvf.f_tradingview(frame([t]), {"tv_indicators": alerts})

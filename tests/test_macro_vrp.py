"""H-03's explicit VRP spread (quant re-review, freeze item): `macro_vrp` = GVZ^2 - HAR-RV^2, built point-in-time inside
the opt-in `macro_drivers` feature (version 3). The default feature set and its version are unchanged."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.macro import US_BDAY, fred_available_utc, fred_frame
from goldbot.features import FEATURES, build_features
from goldbot.features.registry import feature_version
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, MACRO_FEATURE_NAMES

DOCS = Path(__file__).resolve().parents[1] / "docs" / "research"


def _bars(start: str = "2025-01-01", end: str = "2025-05-01", seed: int = 0) -> pd.DataFrame:
    ts = pd.date_range(start, end, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 2000.0 * np.exp(np.cumsum(rng.normal(0, 0.002, len(ts))))
    return pd.DataFrame({"ts_utc": ts, "open": close, "high": close, "low": close, "close": close})


def _gvz(days: pd.DatetimeIndex) -> pd.DataFrame:
    return fred_frame("GVZCLS", pd.DataFrame({"value_date": days, "value": 15.0 + np.sin(np.arange(len(days)))}),
                      pd.Timestamp("2025-07-01"))


def _har_var_ann(bars: pd.DataFrame, at: pd.Timestamp) -> float:
    """The specification, written independently: daily realised variance from the bars' log-close returns (each
    return in the UTC day of its bar), over completed days before `at`'s day; HAR = mean of the last day, the last-5
    mean and the last-22 mean; annualised x 252, in vol points squared (x 100^2)."""
    r = np.log(bars["close"]).diff()
    day = bars["ts_utc"].dt.floor("D")
    rv = (r**2).groupby(day).sum(min_count=1).dropna()
    rv = rv[rv.index < at.floor("D")]
    return float((rv.iloc[-1] + rv.iloc[-5:].mean() + rv.iloc[-22:].mean()) / 3 * 252 * 100**2)


def test_macro_drivers_version_3_adds_the_vrp_spread_and_leaves_the_default_set_alone():
    assert FEATURES["macro_drivers"].version == "3"
    assert "macro_drivers" not in DEFAULT_FEATURE_NAMES
    assert feature_version(DEFAULT_FEATURE_NAMES) == "f-079deacc61"           # the pinned default version
    days = pd.date_range("2025-01-02", periods=80, freq=US_BDAY)
    bars = _bars()
    X = build_features(bars, ["macro_drivers"], {"macro": _gvz(days)})
    at = fred_available_utc("GVZCLS", days[[50]])[0]
    i = int(np.flatnonzero((bars["ts_utc"] == at).to_numpy())[0])
    want = (15.0 + np.sin(50)) ** 2 - _har_var_ann(bars, at)
    assert np.isclose(X["macro_vrp"].iloc[i], want)
    assert np.isnan(X["macro_vrp"].iloc[0])                                  # no GVZ and no 22 days yet


def test_the_vrp_spread_is_point_in_time_on_bars_and_on_releases():
    days = pd.date_range("2025-01-02", periods=80, freq=US_BDAY)
    rel, bars = _gvz(days), _bars()
    full = build_features(bars, ["macro_drivers"], {"macro": rel})["macro_vrp"].to_numpy()
    cut = pd.Timestamp("2025-03-20 13:00", tz="UTC")
    before = (bars["ts_utc"] < cut).to_numpy()
    short = build_features(bars[before].reset_index(drop=True), ["macro_drivers"], {"macro": rel})["macro_vrp"]
    assert np.allclose(full[before], short.to_numpy(), equal_nan=True) and np.isfinite(full[before]).any()
    part = build_features(bars, ["macro_drivers"], {"macro": rel[rel["available_utc"] <= cut]})["macro_vrp"]
    assert np.allclose(full[before], part.to_numpy()[before], equal_nan=True)


def test_both_preregistrations_pin_the_new_macro_feature_version():
    v = feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES])
    for doc in ("preregistration-2027Q1.md", "preregistration-2027Q2-draft.md"):
        text = (DOCS / doc).read_text()
        assert v in text, doc
        assert "f-f999b359e2" not in re.sub(r"\(was `f-f999b359e2`[^)]*\)", "", text), doc

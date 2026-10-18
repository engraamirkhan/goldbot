"""TradingView plot catalogue: plot kind, side and a feature id that changes whenever the study's inputs do."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data import tv_catalog as cat


def meta(inputs=None, version="2.02", plots=None):
    return cat.StudyMeta.from_metainfo({
        "shortDescription": "K-Indi", "pine": {"version": version},
        "inputs": [{"id": k, "defval": v} for k, v in (inputs or {"in_0": 50, "in_1": "close"}).items()],
        "plots": plots or [
            {"id": "plot_0", "type": "line"}, {"id": "plot_1", "type": "line"},
            {"id": "plot_2", "type": "shapes"}, {"id": "plot_3", "type": "chars"}, {"id": "plot_4", "type": "arrows"},
            {"id": "plot_5", "type": "bar_colorer", "target": "plot_0"}, {"id": "plot_6", "type": "data_offset"}],
        "styles": {"plot_0": {"title": "EMA 50"}, "plot_1": {"title": "MFI"}, "plot_2": {"title": "Buy large"},
                   "plot_3": {"title": "Sell small"}, "plot_4": {"title": "Signal"}, "plot_5": {"title": "Candle"}},
    }, tf="15")


def sample(n=50):
    close = np.linspace(2600.0, 2650.0, n)
    return pd.DataFrame({"close": close, "plot_0": close * 1.01, "plot_1": np.linspace(20.0, 80.0, n)})


def test_kind_from_plot_type_and_level_band():
    specs = {s.plot_id: s for s in cat.catalog(meta(), sample())}
    assert {p: s.kind for p, s in specs.items()} == {
        "plot_0": "level", "plot_1": "oscillator", "plot_2": "event", "plot_3": "event", "plot_4": "event",
        "plot_5": "state"}                                   # data_offset carries no value: not catalogued
    # a line 6 % away from the close is not a price level
    far = sample().assign(plot_0=lambda d: d["close"] * 1.06)
    assert {s.plot_id: s.kind for s in cat.catalog(meta(), far)}["plot_0"] == "oscillator"
    # a line cannot be told apart without values: refused, never guessed
    with pytest.raises(ValueError, match="sample"):
        cat.catalog(meta())


def test_input_change_gives_new_feature_id():
    a, b = meta(), meta(inputs={"in_0": 55, "in_1": "close"})
    c = meta(version="2.03")
    ida = cat.feature_id(a, "plot_0")
    assert ida.startswith("tv_k_indi_") and ida.endswith("_15_plot_0") and len(a.meta_hash) == 8
    assert ida == cat.feature_id(meta(), "plot_0")              # deterministic
    assert ida != cat.feature_id(b, "plot_0") and ida != cat.feature_id(c, "plot_0")
    # the input order on the wire does not matter, only the values
    swapped = meta(inputs={"in_1": "close", "in_0": 50})
    assert cat.feature_id(swapped, "plot_0") == ida
    assert {s.feature_id for s in cat.catalog(a, sample())} == {cat.feature_id(a, f"plot_{i}") for i in range(6)}


def test_unknown_title_has_no_side():
    specs = {s.plot_id: s for s in cat.catalog(meta(), sample())}
    assert specs["plot_2"].side == 1 and specs["plot_2"].can_trigger
    assert specs["plot_3"].side == -1 and specs["plot_3"].can_trigger
    assert specs["plot_4"].side is None and not specs["plot_4"].can_trigger          # "Signal": feature only
    for title in ("Long/Short", "Bull vs bear", "", "Dots"):
        assert cat.side_from_title(title) is None
    assert cat.side_from_title("Bullish engulf") == 1 and cat.side_from_title("SHORT entry") == -1
    # a line never carries a side, whatever its title ("Short EMA" is a length, not a direction)
    assert specs["plot_0"].side is None and not specs["plot_1"].can_trigger

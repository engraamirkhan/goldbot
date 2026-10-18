"""Golden test: the vectorised f_order_blocks is byte-identical to the per-bar loop it replaced (values, NaN positions,
dtypes, column order). `_reference` is that loop, kept verbatim as the oracle."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import build_features
from goldbot.features.columns import Columns
from goldbot.features.technical import atr
from goldbot.features.trader import (
    _confirmed_swing_levels,
    _hlo,
    _last_known_before,
    _nearest_report,
)


def _reference(df: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    lag = int(ctx.get("swing_lag", 5))
    k = float(ctx.get("ob_displacement_atr", 1.0))
    search = int(ctx.get("ob_search", 5))
    max_age = int(ctx.get("smc_max_age", 400))
    o, h, lo, c = _hlo(df)
    a = atr(df, 14).to_numpy(dtype=float)
    n = len(df)
    hf, hp, lf, lp = _confirmed_swing_levels(df, lag)
    prev_sh, prev_sl = _last_known_before(hf, hp), _last_known_before(lf, lp)
    body = c - o
    bull_disp = (body > k * a) & (c > prev_sh)
    bear_disp = (-body > k * a) & (c < prev_sl)
    bull, bear = _nearest_report(n), _nearest_report(n)
    up: list[tuple[float, float, int, int]] = []
    dn: list[tuple[float, float, int, int]] = []
    for i in range(n):
        up = [b for b in up if c[i] >= b[0] and b[3] >= i - max_age]
        dn = [b for b in dn if c[i] <= b[1] and b[3] >= i - max_age]
        for disp, book, opposite in ((bull_disp, up, body < 0), (bear_disp, dn, body > 0)):
            if not disp[i]:
                continue
            for j in range(i - 1, max(i - 1 - search, -1), -1):
                if opposite[j]:
                    if all(b[2] != j for b in book):
                        book.append((float(lo[j]), float(h[j]), j, i))
                    break
        if up:
            b = min(up, key=lambda x: (max(c[i] - x[1], 0.0), -x[3]))
            bull["dist"][i], bull["age"][i] = (c[i] - b[1]) / a[i], i - b[3]
        if dn:
            b = min(dn, key=lambda x: (max(x[0] - c[i], 0.0), -x[3]))
            bear["dist"][i], bear["age"][i] = (b[0] - c[i]) / a[i], i - b[3]
        bull["count"][i], bear["count"][i] = len(up), len(dn)
    out = Columns(df.index)
    for side, r in (("bull", bull), ("bear", bear)):
        out[f"ob_{side}_dist_atr"] = r["dist"]
        out[f"ob_{side}_age"] = r["age"]
        out[f"ob_{side}_count"] = r["count"]
    return out.frame()


def _synthetic(tf: str, seed: int) -> pd.DataFrame:
    ticks = synthetic_ticks("2024-01-02", "2024-06-28", ticks_per_minute=1, seed=seed)
    return mid(resample_bars(ticks_to_1m(ticks), tf).reset_index(drop=True))


def _choppy(n: int, seed: int) -> pd.DataFrame:
    """Fat-tailed random walk with a zero-range bar: many displacements and many ties."""
    rng = np.random.default_rng(seed)
    c = 2000 + np.cumsum(rng.standard_t(2, n) * 1.5)
    o = np.round(np.concatenate((np.array([2000.0]), c[:-1])) + rng.normal(0, 0.3, n), 1)
    c = np.round(c, 1)
    h = np.maximum(o, c) + np.round(rng.exponential(0.5, n), 1)
    lo = np.minimum(o, c) - np.round(rng.exponential(0.5, n), 1)
    h[300] = lo[300] = o[300] = c[300] = 2000.0
    ts = pd.date_range("2024-01-02", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": 0.1, "tick_count": 10.0})


FIXTURES = {
    "real_shaped_15m": lambda: _synthetic("15m", 5),
    "real_shaped_1h": lambda: _synthetic("1h", 11),
    "choppy": lambda: _choppy(6000, 1),
    "choppy_other_seed": lambda: _choppy(6000, 2),
}
CTXS = [{}, {"swing_lag": 2, "ob_displacement_atr": 0.5, "ob_search": 8, "smc_max_age": 30},
        {"swing_lag": 3, "ob_search": 1, "smc_max_age": 1}]


@pytest.mark.parametrize("ctx", CTXS, ids=["default", "loose", "tight"])
@pytest.mark.parametrize("name", list(FIXTURES))
def test_vectorised_order_blocks_equal_the_per_bar_loop_exactly(name, ctx):
    df = FIXTURES[name]()
    got = build_features(df, ["order_blocks"], ctx=ctx)
    with np.errstate(all="ignore"):
        want = _reference(df, ctx)
    assert want[["ob_bull_count", "ob_bear_count"]].to_numpy().max() > 0     # the fixture exercises blocks
    assert_frame_equal(got[list(want.columns)].reset_index(drop=True), want.reset_index(drop=True), check_exact=True)

"""sr-v3 block X6 price-action columns (goldbot/features/price_action_x6.py). Synthetic bars only; no label is read.

Pinned:
- point in time: prefix invariance on every column at 1m, 15m, 4h and 1d (appending bars never changes a row);
- bounded memory: the value at bar T built on only the last `column_lookback(column, tf)` bars equals the full-history
  value EXACTLY, at many T, on a random walk and on a sparse-pivot trend; and the ADR 0104 truncation check
  (`column_lookback.passes`) holds at the declared lookback on its 8-seed fixture;
- the registry declares exactly the structural bound (`structural_lookback`) for every column at every tf;
- hand-worked rules (candle anatomy, structure state and break, level distance, room, break and retest);
- opt-in wiring: default feature set unchanged, not NEEDS_STATE, signed columns registered.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.features import price_action_x6 as X6
from goldbot.features.registry import COLUMN_LOOKBACKS, column_lookback
from goldbot.research import column_lookback as CL

TFS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")
FREQ = {"1m": "1min", "15m": "15min", "4h": "4h", "1d": "1D"}
FEATURE_OF = {**{c: X6.NAME_CANDLES for c in X6.CANDLE_COLUMNS}, **{c: X6.NAME_STRUCTURE for c in X6.STRUCTURE_COLUMNS},
              **{c: X6.NAME_LEVELS for c in X6.LEVEL_COLUMNS}}


def _walk(n: int, freq: str, seed: int = 3, drift: float = 0.0, sd: float = 0.6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 1500 + np.cumsum(drift + rng.normal(0, sd, n))
    o = np.r_[c[0], c[:-1]] + rng.normal(0, sd / 4, n)
    h = np.maximum(o, c) + np.abs(rng.normal(0, sd / 2, n))
    lo = np.minimum(o, c) - np.abs(rng.normal(0, sd / 2, n))
    ts = pd.date_range("2021-03-01 00:00", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c,
                         "tick_count": np.full(n, 100.0), "spread": np.full(n, 0.2)})


def _frame(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    return pd.concat([X6.candles_frame(df), X6.structure_frame(df), X6.levels_frame(df, tf)], axis=1)


# --------------------------------------------------------------------------- point in time
@pytest.mark.parametrize("tf,n", [("1m", 9000), ("15m", 3000), ("4h", 1500), ("1d", 800)])
def test_no_column_changes_when_later_bars_are_appended(tf: str, n: int) -> None:
    df = _walk(n, FREQ[tf])
    full = _frame(df, tf)
    assert list(full.columns) == list(X6.COLUMNS)
    for k in (n // 7, n // 2, n - 1):
        part = _frame(df.iloc[:k], tf)
        pd.testing.assert_frame_equal(part.reset_index(drop=True), full.iloc[:k].reset_index(drop=True),
                                      check_exact=True)


# --------------------------------------------------------------------------- bounded memory
@pytest.mark.parametrize("tf,n", [("1m", 12000), ("15m", 2500), ("4h", 900), ("1d", 900)])
@pytest.mark.parametrize("drift", [0.0, 0.25])
def test_a_tail_of_the_declared_lookback_gives_the_full_history_value(tf: str, n: int, drift: float) -> None:
    df = _walk(n, FREQ[tf], drift=drift, sd=0.6 if drift == 0 else 0.15)
    full = _frame(df, tf)
    rng = np.random.default_rng(5)
    longest = max(column_lookback(c, tf) for c in X6.COLUMNS)
    for T in rng.integers(longest + 50, n, 6):
        for c in X6.COLUMNS:
            L = column_lookback(c, tf)
            tail = _frame(df.iloc[T - L + 1:T + 1].reset_index(drop=True), tf)
            a, b = float(full[c].iloc[T]), float(tail[c].iloc[-1])
            assert (np.isnan(a) and np.isnan(b)) or a == b, f"{c}@{tf} T={T}: tail {b} != full {a}"


def test_registry_declares_the_structural_bound() -> None:
    for c in X6.COLUMNS:
        assert c in COLUMN_LOOKBACKS
        for tf in TFS:
            assert column_lookback(c, tf) == X6.structural_lookback(c, tf), (c, tf)


def _cases() -> list[tuple[str, str]]:
    return [(c, tf) for c in X6.COLUMNS for tf in TFS]


@pytest.mark.parametrize(("column", "tf"), _cases(), ids=lambda x: str(x))
def test_declared_lookback_passes_the_adr_0104_truncation_check(column: str, tf: str) -> None:
    X6.enable()
    lb = column_lookback(column, tf)
    n = max(CL.N_BARS, lb + 2_000)
    assert CL.passes(FEATURE_OF[column], column, lb, tf, n), f"{column}@{tf}: bars older than {lb} move it >= 1 %"


# --------------------------------------------------------------------------- hand-worked rules
def _bars(rows: list[tuple[float, float, float, float]], freq: str = "4h") -> pd.DataFrame:
    o, h, lo, c = (np.array(x, dtype=float) for x in zip(*rows))
    ts = pd.date_range("2024-01-01", periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "tick_count": 100.0})


def test_candle_anatomy() -> None:
    f = X6.candles_frame(_bars([(10, 12, 8, 11), (11, 11.5, 9, 9), (9, 13, 7, 12), (12, 12, 12, 12)]))
    assert f["x6_body_frac"].tolist() == [0.25, -2 / 2.5, 0.5, 0.0]
    assert f["x6_clv"].tolist() == [0.5, -1.0, (24 - 20) / 6, 0.0]
    assert np.isnan(f["x6_io_bar"].iloc[0])
    assert f["x6_io_bar"].tolist()[1:] == [-1.0, 1.0, -1.0]


def _zigzag(points: list[float], wick: float = 0.1) -> pd.DataFrame:
    c: list[float] = [points[0]] * 5
    for a, b in zip(points[:-1], points[1:]):
        c += list(np.linspace(a, b, 5)[1:])
    c_arr = np.array(c)
    o = np.r_[c_arr[0], c_arr[:-1]]
    return _bars([(oo, max(oo, cc) + wick, min(oo, cc) - wick, cc) for oo, cc in zip(o, c_arr)], freq="15min")


def test_structure_state_and_break() -> None:
    up = X6.structure_frame(_zigzag([100, 104, 102, 106, 104, 108, 106, 110, 108, 112]))
    assert up["x6_ms_state"].iloc[-1] == 1.0
    dn = X6.structure_frame(_zigzag([112, 108, 110, 106, 108, 104, 106, 102, 104, 100]))
    assert dn["x6_ms_state"].iloc[-1] == -1.0
    bos = up["x6_ms_bos"].to_numpy()
    first = int(np.flatnonzero(bos > 0)[0])
    assert bos[first] == 1.0 and bos[first + 1] == pytest.approx(1 - 1 / X6.BOS_DECAY)
    assert (dn["x6_ms_bos"].to_numpy() <= 0).all()


def test_level_distance_room_break_and_retest() -> None:
    # 4h bars: a swing high at 110 (5-bar fractal), price returns below it, breaks above, then retests it from above
    path = [100, 102, 104, 106, 108, 110, 108, 106, 104, 102, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    path += [111, 113, 112, 110.1, 112, 114]
    rows = [(c - 0.5, c + 0.5, c - 0.5, c) for c in path]
    rows[5] = (109.5, 110.5, 109.0, 110.0)                         # the pivot high is 110.5
    rows[23] = (112.0, 112.2, 110.4, 111.5)                        # retest: low into the area, close above it
    f = X6.levels_frame(_bars(rows), "4h")
    i_break = 20
    assert f["x6_lv_brk_state"].iloc[i_break] == 1.0
    assert f["x6_lv_brk_state"].iloc[23] == 2.0
    assert f["x6_lv_brk_state"].iloc[23 + X6.LV_RETEST] == 0.0 if len(f) > 23 + X6.LV_RETEST else True
    # below the level before the break: positive room up, distance > 0
    assert f["x6_lv_dist_atr"].iloc[19] > 0 and f["x6_lv_room_net_atr"].iloc[19] < X6.LV_CAP / 2
    assert 0 <= f["x6_lv_dist_atr"].min() and f["x6_lv_dist_atr"].max() <= X6.LV_CAP


def test_a_4h_level_enters_a_15m_row_only_after_its_bucket_closed() -> None:
    df = _walk(16 * 40, "15min", seed=9)
    full = X6.levels_frame(df, "15m")
    # changing the bars of the last (still open) 4h bucket never changes any earlier row
    cut = len(df) - 5
    alt = df.copy()
    alt.loc[cut:, ["open", "high", "low", "close"]] += 50.0
    again = X6.levels_frame(alt, "15m")
    pd.testing.assert_frame_equal(full.iloc[:cut], again.iloc[:cut], check_exact=True)


# --------------------------------------------------------------------------- wiring
def test_opt_in_wiring() -> None:
    from goldbot.features.registry import FEATURES, NEEDS_STATE, SIGNED, build_features
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES

    names = X6.enable()
    assert names == list(X6.NAMES) and set(names) <= set(OPT_IN_FEATURES)
    assert all(FEATURES[n].family == X6.FAMILY and NEEDS_STATE not in FEATURES[n].tags for n in names)
    assert [n for n in FEATURES if n not in OPT_IN_FEATURES] == DEFAULT_FEATURE_NAMES
    assert all(SIGNED[k] == 0.0 for k in X6.SIGNED)
    assert "x6_io_bar" not in SIGNED and "x6_lv_dist_atr" not in SIGNED
    out = build_features(_walk(400, "15min"), names)            # a plain tail is accepted: bounded memory
    assert set(X6.COLUMNS) <= set(out.columns)

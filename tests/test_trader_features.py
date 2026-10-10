"""Trader-toolkit feature families (goldbot/features/trader.py): hand-built bar sequences where the session levels,
sweep, break of structure, fair value gap and order block are known, and a truncated-history check that no value
changes when later bars are appended."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import FEATURES, build_features, families
from goldbot.features.technical import atr
from goldbot.specialists import SPECIALISTS

TRADER = ["session_zones", "market_structure", "fair_value_gaps", "order_blocks"]


def _frame(rows: list[tuple[float, float, float, float]], start: str, freq: str = "5min") -> pd.DataFrame:
    """Bars (open, high, low, close) after 20 slowly falling warm-up bars (range 1.0, so ATR14 is 1.0 and no swing
    forms in them)."""
    base = [(100.005 - 0.01 * k, 100.5 - 0.01 * k, 99.5 - 0.01 * k, 100.0 - 0.01 * k) for k in range(20)]
    o, h, lo, c = (np.array(x, dtype=float) for x in zip(*(base + rows)))
    ts = pd.date_range(start, periods=len(o), freq=freq, tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": 0.1, "tick_count": 10.0})


def _mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Price reflected about 100: highs become lows, a bullish pattern becomes the bearish one."""
    return df.assign(open=200 - df["open"], high=200 - df["low"], low=200 - df["high"], close=200 - df["close"])


B = 20   # index of the first hand-built bar


def test_trader_families_are_registered_and_no_specialist_declares_their_columns():
    fam = families()
    assert {"session_zones"} <= set(fam["session"]) and "market_structure" in fam["structure"]
    assert set(fam["smc"]) == {"fair_value_gaps", "order_blocks"}
    m = _frame([], "2024-01-10 08:00")
    cols = set(build_features(m, TRADER).columns) - {"ts_utc"}
    for name, cls in SPECIALISTS.items():
        assert not cols & set(cls.model_features), name


# --------------------------------------------------------------------------- session zones
def _session_day() -> pd.DataFrame:
    """15m bars from Tue 2024-01-09 23:00 UTC (Asia open) to Wed 23:45: flat at 100 except an Asia high of 110 at
    03:00, an Asia low of 95 at 05:00, a London high of 104 at 09:00."""
    ts = pd.date_range("2024-01-09 23:00", "2024-01-10 23:45", freq="15min", tz="UTC")
    o = np.full(len(ts), 100.0)
    h, lo, c = o + 0.5, o - 0.5, o.copy()
    at = {t: k for k, t in enumerate(ts)}
    h[at[pd.Timestamp("2024-01-10 03:00", tz="UTC")]] = 110.0
    lo[at[pd.Timestamp("2024-01-10 05:00", tz="UTC")]] = 95.0
    h[at[pd.Timestamp("2024-01-10 09:00", tz="UTC")]] = 104.0
    k = at[pd.Timestamp("2024-01-10 12:00", tz="UTC")]
    c[k], h[k] = 112.0, 112.0                                     # a London close above the Asia range
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": 0.1, "tick_count": 1.0})


def test_session_zones_report_current_and_previous_session_levels_known_at_each_bar():
    m = _session_day()
    X = build_features(m, ["session_zones"])
    a = atr(m, 14).to_numpy()
    c = m["close"].to_numpy()
    row = {t: k for k, t in enumerate(pd.DatetimeIndex(m["ts_utc"]))}

    def at(t: str) -> int:
        return row[pd.Timestamp(t, tz="UTC")]

    def dist(i: int, level: float) -> float:
        return (c[i] - level) / a[i]

    # inside Asia: the running high includes 03:00 only from its own close on
    assert X["sess_high_dist_atr"][at("2024-01-10 02:45")] == pytest.approx(dist(at("2024-01-10 02:45"), 100.5))
    assert X["sess_high_dist_atr"][at("2024-01-10 03:00")] == pytest.approx(dist(at("2024-01-10 03:00"), 110.0))
    assert X["sess_open_dist_atr"][at("2024-01-10 04:00")] == pytest.approx(0.0)
    assert np.isnan(X["prev_sess_high_dist_atr"][at("2024-01-10 06:45")])      # no completed session yet
    # London 08:00: the previous session is Asia (high 110, low 95, open 100); London's own high so far is 100.5
    i = at("2024-01-10 08:00")
    assert X["prev_sess_high_dist_atr"][i] == pytest.approx(dist(i, 110.0))
    assert X["prev_sess_low_dist_atr"][i] == pytest.approx(dist(i, 95.0))
    assert X["prev_sess_open_dist_atr"][i] == pytest.approx(dist(i, 100.0))
    assert X["asia_high_dist_atr"][i] == pytest.approx(dist(i, 110.0)) and np.isnan(X["london_high_dist_atr"][i])
    assert X["sess_high_dist_atr"][i] == pytest.approx(dist(i, 100.5)) and X["prev_sess_pos"][i] == 0
    assert X["prev_sess_pos"][at("2024-01-10 12:00")] == 1                     # closed above the Asia range
    # New York 13:00: previous session London (high 112); Asia's last high still 110
    i = at("2024-01-10 13:00")
    assert X["prev_sess_high_dist_atr"][i] == pytest.approx(dist(i, 112.0))
    assert X["london_high_dist_atr"][i] == pytest.approx(dist(i, 112.0))
    assert X["asia_high_dist_atr"][i] == pytest.approx(dist(i, 110.0))
    # 21:00-23:00 is outside every window: no current session, the previous one is New York
    i = at("2024-01-10 21:30")
    assert np.isnan(X["sess_high_dist_atr"][i]) and X["prev_sess_high_dist_atr"][i] == pytest.approx(dist(i, 100.5))
    # previous feature-day (ends 13:30 New York = 18:30 UTC in January): known only after it closes
    assert np.isnan(X["pdh_dist_atr"][at("2024-01-10 18:15")])
    i = at("2024-01-10 18:30")
    assert X["pdh_dist_atr"][i] == pytest.approx(dist(i, 112.0)) and X["pdl_dist_atr"][i] == pytest.approx(dist(i, 95.0))


def test_previous_week_levels_appear_only_once_the_week_is_over():
    ts = pd.date_range("2024-01-11", "2024-01-15 03:00", freq="15min", tz="UTC")     # Thu .. Mon
    h = np.full(len(ts), 100.5)
    h[10] = 120.0                                                                    # Thursday spike
    m = pd.DataFrame({"ts_utc": ts, "open": 100.0, "high": h, "low": 99.5, "close": 100.0, "spread": 0.1,
                      "tick_count": 1.0})
    X = build_features(m, ["session_zones"])
    a = atr(m, 14).to_numpy()
    sat = int(np.flatnonzero(ts == pd.Timestamp("2024-01-13 12:00", tz="UTC"))[0])
    mon = len(ts) - 1
    assert np.isnan(X["pwh_dist_atr"][sat])                       # same week as the spike (weeks start Sunday 00:00)
    assert X["pwh_dist_atr"][mon] == pytest.approx((100.0 - 120.0) / a[mon])
    assert X["pwl_dist_atr"][mon] == pytest.approx((100.0 - 99.5) / a[mon])


# --------------------------------------------------------------------------- market structure
STRUCTURE = [            # lag 2: swing high 101 at B confirmed at B+2; swept at B+3; broken at B+4; 101.5 broken at B+6
    (100.0, 101.0, 99.4, 100.2),
    (100.2, 100.4, 99.3, 99.6),
    (99.6, 100.3, 99.2, 99.9),
    (99.9, 101.5, 99.5, 100.5),      # trades above 101, closes back below: sweep of the swing high
    (100.5, 101.3, 100.4, 101.2),    # first close above 101: bullish break of structure
    (101.2, 101.4, 100.9, 101.1),    # confirms B+3 (101.5) as the new swing high
    (101.1, 101.8, 101.0, 101.6),    # close above 101.5: second break
]


@pytest.mark.parametrize("mirror", [False, True])
def test_sweep_and_break_of_structure_on_a_known_sequence(mirror):
    m = _frame(STRUCTURE, "2024-01-10 07:00")
    m = _mirror(m) if mirror else m
    X = build_features(m, ["market_structure"], ctx={"swing_lag": 2})
    up, dn = ("sweep_low", "sweep_high") if mirror else ("sweep_high", "sweep_low")
    sign = -1 if mirror else 1
    assert np.flatnonzero(X[up].to_numpy()).tolist() == [B + 3]
    assert X[dn].sum() == 0 and X["sweep_dir"][B + 3] == -sign
    assert np.flatnonzero(X["bos_event"].to_numpy()).tolist() == [B + 4, B + 6]
    assert set(X["bos_event"][[B + 4, B + 6]]) == {sign}
    assert (X["bos_dir"][:B + 4] == 0).all() and (X["bos_dir"][B + 4:] == sign).all()
    assert np.isnan(X["bars_since_bos"][B + 3]) and X["bars_since_bos"][B + 5] == 1 and X["bars_since_bos"][B + 6] == 0


def test_a_swing_is_broken_only_once():
    rows = [*STRUCTURE[:5], (101.2, 101.25, 100.5, 100.6), (100.6, 101.25, 100.5, 101.1)]   # re-cross 101 at B+6
    X = build_features(_frame(rows, "2024-01-10 07:00"), ["market_structure"], ctx={"swing_lag": 2})
    assert np.flatnonzero(X["bos_event"].to_numpy()).tolist() == [B + 4]


# --------------------------------------------------------------------------- fair value gaps
FVG = [
    (100.0, 100.5, 99.5, 100.4),
    (100.4, 102.5, 100.3, 102.4),    # displacement
    (102.4, 103.0, 101.0, 102.8),    # low 101.0 > high[B] 100.5: bullish gap 100.5..101.0, known at B+2
    (102.8, 102.9, 100.8, 101.5),    # trades into it: unfilled part 100.5..100.8
    (101.5, 101.6, 100.4, 100.6),    # trades through 100.5: filled
]


@pytest.mark.parametrize("mirror", [False, True])
def test_fair_value_gap_forms_on_the_third_bar_shrinks_on_partial_fill_and_is_dropped_when_filled(mirror):
    m = _frame(FVG, "2024-01-10 07:00")
    m = _mirror(m) if mirror else m
    X = build_features(m, ["fair_value_gaps"])
    a = atr(m, 14).to_numpy()
    side, other = ("bear", "bull") if mirror else ("bull", "bear")
    c = m["close"].to_numpy()
    near = (lambda i, lvl: (lvl - c[i]) / a[i]) if mirror else (lambda i, lvl: (c[i] - lvl) / a[i])
    flip = (lambda p: 200 - p) if mirror else (lambda p: p)
    assert np.isnan(X[f"fvg_{side}_dist_atr"][B + 1]) and X[f"fvg_{side}_count"][B + 1] == 0
    assert X[f"fvg_{side}_dist_atr"][B + 2] == pytest.approx(near(B + 2, flip(101.0)))
    assert X[f"fvg_{side}_size_atr"][B + 2] == pytest.approx(0.5 / a[B + 2])
    assert X[f"fvg_{side}_age"][B + 2] == 0 and X[f"fvg_{side}_count"][B + 2] == 1
    assert X[f"fvg_{side}_dist_atr"][B + 3] == pytest.approx(near(B + 3, flip(100.8)))
    assert X[f"fvg_{side}_size_atr"][B + 3] == pytest.approx(0.3 / a[B + 3])
    assert X[f"fvg_{side}_age"][B + 3] == 1
    assert np.isnan(X[f"fvg_{side}_dist_atr"][B + 4]) and X[f"fvg_{side}_count"][B + 4] == 0
    assert X[f"fvg_{other}_count"].sum() == 0


def test_fair_value_gaps_below_the_minimum_size_are_ignored():
    X = build_features(_frame(FVG, "2024-01-10 07:00"), ["fair_value_gaps"], ctx={"fvg_min_atr": 0.6})
    assert X["fvg_bull_count"].sum() == 0


# --------------------------------------------------------------------------- order blocks
OB = [                               # lag 2: swing high 101 at B, confirmed at B+2
    (99.7, 101.0, 99.6, 100.8),
    (100.8, 100.9, 99.7, 99.9),      # last bearish candle: block 99.7..100.9
    (99.9, 100.2, 99.5, 100.0),
    (100.0, 102.2, 99.95, 102.0),    # displacement: body 2.0 > ATR, closes above the swing high 101
    (102.0, 102.1, 100.5, 100.6),    # retest: close inside the block
    (100.6, 100.7, 99.5, 99.6),      # close below the block's low: invalidated
]


@pytest.mark.parametrize("mirror", [False, True])
def test_order_block_is_the_last_opposite_candle_before_a_structure_breaking_displacement(mirror):
    m = _frame(OB, "2024-01-10 07:00")
    m = _mirror(m) if mirror else m
    X = build_features(m, ["order_blocks"], ctx={"swing_lag": 2})
    a = atr(m, 14).to_numpy()
    c = m["close"].to_numpy()
    side, other = ("bear", "bull") if mirror else ("bull", "bear")
    near = (lambda i, lvl: (lvl - c[i]) / a[i]) if mirror else (lambda i, lvl: (c[i] - lvl) / a[i])
    edge = 200 - 100.9 if mirror else 100.9
    assert np.isnan(X[f"ob_{side}_dist_atr"][B + 2]) and X[f"ob_{side}_count"][B + 2] == 0
    assert X[f"ob_{side}_dist_atr"][B + 3] == pytest.approx(near(B + 3, edge))
    assert X[f"ob_{side}_age"][B + 3] == 0 and X[f"ob_{side}_count"][B + 3] == 1
    assert X[f"ob_{side}_dist_atr"][B + 4] == pytest.approx(near(B + 4, edge)) and X[f"ob_{side}_dist_atr"][B + 4] < 0
    assert X[f"ob_{side}_age"][B + 4] == 1
    assert np.isnan(X[f"ob_{side}_dist_atr"][B + 5]) and X[f"ob_{side}_count"][B + 5] == 0
    assert X[f"ob_{other}_count"].sum() == 0


def test_no_order_block_without_a_break_of_structure():
    rows = [*OB[:3], (100.0, 101.0, 99.95, 100.95)]           # body 0.95 > 0.5 ATR but closes below the swing high
    X = build_features(_frame(rows, "2024-01-10 07:00"), ["order_blocks"],
                       ctx={"swing_lag": 2, "ob_displacement_atr": 0.5})
    assert X["ob_bull_count"].sum() == 0


# --------------------------------------------------------------------------- no look-ahead
@pytest.fixture(scope="module")
def bars_15m():
    ticks = synthetic_ticks("2025-03-03", "2025-04-12", ticks_per_minute=2, seed=5)
    return resample_bars(ticks_to_1m(ticks), "15m").reset_index(drop=True)


def test_no_trader_feature_changes_when_future_bars_are_appended(bars_15m):
    m = mid(bars_15m).reset_index(drop=True)
    full = build_features(m, TRADER).drop(columns=["ts_utc"])
    assert full.shape[1] == 37
    for cut in (150, len(m) // 3, len(m) // 2, len(m) - 3):
        part = build_features(m.iloc[:cut], TRADER).drop(columns=["ts_utc"])
        pd.testing.assert_frame_equal(part, full.iloc[:cut], check_dtype=False)
    # warmed up, the always-defined columns carry no NaN (gaps and blocks are NaN only while none is active)
    warm = full.iloc[200:]
    defined = [c for c in warm.columns if not c.startswith(("fvg_", "ob_", "sess_", "pw")) or c.endswith("_count")]
    assert not warm[defined].isna().any().any()


def test_trader_features_pass_the_pipeline_lookahead_check(bars_15m):
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, lookahead_check
    assert set(TRADER) <= set(DEFAULT_FEATURE_NAMES) and all(n in FEATURES for n in TRADER)
    out = lookahead_check(bars_15m, None, feature_names=TRADER)
    assert out["lookahead_columns"] == [] and out["columns_checked"] >= 37

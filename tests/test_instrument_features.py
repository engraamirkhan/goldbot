"""Multi-instrument demo, lane L1: the gold-dollar numbers in the survey features and the approval card's usual spread
("round-number feature survey.py -> ATR-scaled or off for non-gold"; "point 0.01 spread_normal.py").

* `round_numbers` measures distance to $5/$10/$25/$50 gold handles: a dollar handle is no level on EURUSD (1.08) or
  US500, so for any instrument outside the gold group the columns are NaN (off), same names, never a fake level;
* `expected_move` divides by a round trip whose default extra (0.37 $/oz) is gold's: another instrument needs its own
  `round_trip_extra` in the context, else NaN;
* the usual spread is in the instrument's own points, and the tick record it reads is the account's gold one, so a
  non-gold symbol has no usual spread yet (null, as "unknown").
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.api import spread_normal
from goldbot.features import build_features


def _walk(n: int = 400, start: float = 2000.0, sd: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    c = start * np.exp(np.cumsum(rng.normal(0, sd, n)))
    o = np.concatenate(([start], c[:-1]))
    h, lo = np.maximum(o, c) * 1.0005, np.minimum(o, c) * 0.9995
    ts = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": 0.3, "tick_count": 10.0})


def test_round_numbers_are_unchanged_for_gold_with_or_without_the_symbol() -> None:
    df = _walk()
    plain = build_features(df, ["round_numbers"])
    gold = build_features(df, ["round_numbers"], ctx={"symbol": "XAUUSD"})
    pd.testing.assert_frame_equal(plain, gold)
    assert plain["round_10_dist_atr"].notna().any()


def test_round_numbers_keep_working_on_gold_crosses() -> None:
    assert build_features(_walk(), ["round_numbers"], ctx={"symbol": "XAUEUR"})["round_10_dist_atr"].notna().any()


def test_round_numbers_are_off_for_an_instrument_outside_the_gold_group() -> None:
    for sym in ("EURUSD", "US500", "COPPER-C"):
        x = build_features(_walk(start=1.08), ["round_numbers"], ctx={"symbol": sym})
        assert list(x.columns) == list(build_features(_walk(), ["round_numbers"]).columns)
        assert x.drop(columns="ts_utc").isna().all().all(), sym


def test_expected_move_needs_the_instruments_own_round_trip_outside_gold() -> None:
    df = _walk(start=1.08)
    assert build_features(df, ["expected_move"], ctx={"symbol": "EURUSD"}).drop(columns="ts_utc").isna().all().all()
    own = build_features(df, ["expected_move"], ctx={"symbol": "EURUSD", "round_trip_extra": 0.00009})
    assert own["em_atr_cost"].notna().any()
    gold = build_features(_walk(), ["expected_move"])
    pd.testing.assert_frame_equal(gold, build_features(_walk(), ["expected_move"], ctx={"symbol": "XAUUSD"}))


def test_the_usual_spread_has_no_value_for_a_non_gold_symbol(tmp_path: object) -> None:
    at = pd.Timestamp("2026-11-02 10:00", tz="UTC")
    assert spread_normal.normal_spread_points(str(tmp_path), "icm-probe", at, symbol="EURUSD") is None


def test_the_usual_spread_counts_in_the_symbols_points() -> None:
    assert spread_normal.point_for("XAUUSD") == 0.01 and spread_normal.point_for("XAUUSD+") == 0.01
    assert spread_normal.point_for("EURUSD") == 0.00001

"""R-a (quant ruling on PR #486, ADR 0104 4): a column declared close-only (`registry.CLOSE_ONLY_COLUMNS`) accepts
lookback months graded B. Proof per column: perturbing open, high, low, spread and tick count of every lookback bar
with the closes unchanged leaves the column identical, on the column_lookback fixture at 15m, 4h and 1d. A column
that fails cannot be declared close-only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.features.registry import CLOSE_ONLY_COLUMNS, COLUMN_LOOKBACKS
from goldbot.research import column_lookback as CL
from goldbot.research import dense_labels as D

TFS = ("15m", "4h", "1d")


def non_close_perturbed(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Every bar's open, high, low, spread and tick count replaced; closes unchanged; bars stay valid OHLC."""
    rng = np.random.default_rng(seed + 777)
    n = len(frame)
    c = frame["close"].to_numpy(float)
    amp = np.abs(rng.normal(0, 2.0, n))
    op = c + rng.normal(0, 1.0, n)
    hi = np.maximum(c, op) + 3 * amp
    lo = np.minimum(c, op) - 2 * amp
    return frame.assign(open=op, high=hi, low=lo, spread=np.abs(rng.normal(1.0, 0.3, n)),
                        tick_count=rng.integers(500, 5000, n).astype(float))


def identical(feature: str, column: str, tf: str, seed: int) -> bool:
    f = CL.fixture(seed, tf)
    a = CL.builder(feature)(f)[column].to_numpy(float)
    b = CL.builder(feature)(non_close_perturbed(f, seed))[column].to_numpy(float)
    return bool(np.array_equal(a, b, equal_nan=True))


def _cases() -> list[tuple[str, str, str, int]]:
    prod = D.column_producers()
    return [(prod[c][0], c, tf, s) for c in sorted(CLOSE_ONLY_COLUMNS) for tf in TFS for s in CL.SEEDS[:3]]


def test_every_close_only_column_is_a_declared_fs_v1_column() -> None:
    prod = D.column_producers()
    assert CLOSE_ONLY_COLUMNS <= set(COLUMN_LOOKBACKS) and CLOSE_ONLY_COLUMNS <= set(prod)


@pytest.mark.parametrize(("feature", "column", "tf", "seed"), _cases(), ids=lambda x: str(x))
def test_a_close_only_column_ignores_everything_but_the_close(feature: str, column: str, tf: str, seed: int) -> None:
    assert identical(feature, column, tf, seed), f"{column}@{tf} reads more than the close: not close-only"


@pytest.mark.parametrize("column", ["atr_ratio_14_100", "dist_vwap48_atr", "spread_atr", "donchian_pos_20"])
def test_the_check_bites_on_columns_that_read_ranges_spread_or_volume(column: str) -> None:
    feature = D.column_producers()[column][0]
    assert column not in CLOSE_ONLY_COLUMNS
    assert not all(identical(feature, column, tf, s) for tf in TFS for s in CL.SEEDS[:3])

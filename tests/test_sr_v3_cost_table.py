"""scripts/research/sr_v3_cost_table.py: the swap rate is read as-of its availability, and the table's checks bite."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("sr_v3_cost_table", ROOT / "scripts/research/sr_v3_cost_table.py")
assert _spec and _spec.loader
C = importlib.util.module_from_spec(_spec)
sys.modules["sr_v3_cost_table"] = C
_spec.loader.exec_module(C)


def test_rate_is_read_as_of_availability_not_value_date(tmp_path: Path) -> None:
    """A monthly value for 2012-01 published 2012-03-01 is unknown on 2012-02-01 (a seeded future release)."""
    (tmp_path / "daily").mkdir()
    pd.DataFrame({"value_date": ["2011-12-01", "2012-01-01"], "value": [1.0, 5.0],
                  "available_utc": pd.to_datetime(["2012-02-01", "2012-03-01"], utc=True)}).to_parquet(
        tmp_path / "daily" / f"fred_{C.RATE_SERIES['JPY']}.parquet")
    at = pd.DatetimeIndex(pd.to_datetime(["2012-01-15", "2012-02-01", "2012-02-29", "2012-03-01"], utc=True))
    r = C.rate_asof(tmp_path, "JPY", at)
    assert np.isnan(r[0])
    assert list(r[1:]) == [0.01, 0.01, 0.05]


def _tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    cells = [(i, b, s, tf) for i in (C.GOLD, *C.FX10, *C.METALS) for b in ("icm",) for s in C.SESSIONS for tf in C.TFS]
    cost = pd.DataFrame(cells, columns=["instrument", "broker", "session", "tf"]).assign(rt_bp=1.0)
    swap = pd.DataFrame({"instrument": "EURUSD", "month": ["2012-01"], "long_per_night": [-1e-5],
                         "short_per_night": [-2e-5]})
    return cost, swap


def test_checks_pass_on_a_clean_table() -> None:
    cost, swap = _tables()
    assert C.checks(cost, swap)["pass"]


def test_checks_fail_below_w2_or_into_the_holdout() -> None:
    cost, swap = _tables()
    cost.loc[0, "rt_bp"] = 0.1
    assert not C.checks(cost, swap)["pass"]
    cost, swap = _tables()
    swap.loc[0, "month"] = "2025-10"
    assert not C.checks(cost, swap)["pass"]

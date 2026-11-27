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


def _gold_cost() -> pd.DataFrame:
    return pd.DataFrame({"instrument": C.GOLD, "broker": ["icm"], "session": ["london"], "tf": ["15m"],
                         "rt_bp": [0.9], "ecr_ub95_usd": [0.36]})


def _spread() -> pd.DataFrame:
    return pd.DataFrame({"session": ["london", "london"], "month": ["2013-05", "2013-06"],
                         "ub95_usd": [0.30, 9.99],
                         "available_utc": pd.to_datetime(["2013-06-01", "2013-07-01"], utc=True)})


def test_gold_row_cost_is_charged_in_usd_at_a_2013_price() -> None:
    """quant N2: the ECR ub95 in USD at a 2013 entry (~$1,380) costs ~3x the bp it costs at ~$3,966."""
    rows = pd.DataFrame({"broker": ["icm", "icm"], "session": ["london", "london"], "tf": ["15m", "15m"],
                         "decision_utc": pd.to_datetime(["2013-06-12 09:00", "2013-06-12 09:00"], utc=True),
                         "entry": [1380.0, 3966.0]})
    rt = C.row_rt_bp(rows, _gold_cost(), _spread(), {"icm": 0.08})
    # spread leg: May's ub95 (June's is not complete at the decision) + commission, in bp of the entry
    assert np.isclose(rt[0], (0.30 + 0.08) / 1380.0 * 1e4)
    assert rt[0] > 2.5 * 0.9                                     # the $3,966 cell bp under-charges 2013 ~3x
    assert np.isclose(rt[1], max(0.9, 0.36 / 3966.0 * 1e4, 0.38 / 3966.0 * 1e4))
    assert rt[0] / rt[1] > 2.5                                   # same USD cost, ~2.9x the bp at the 2013 price


def test_gold_row_cost_never_reads_the_decision_month_spread() -> None:
    rows = pd.DataFrame({"broker": ["icm"], "session": ["london"], "tf": ["15m"],
                         "decision_utc": pd.to_datetime(["2013-06-30 23:00"], utc=True), "entry": [1380.0]})
    rt = C.row_rt_bp(rows, _gold_cost(), _spread(), {"icm": 0.08})
    assert rt[0] < 9.99 / 1380.0 * 1e4                          # June's (seeded huge) spread is not yet available


def test_metal_swap_charges_both_sides_and_never_credits(tmp_path: Path) -> None:
    (tmp_path / "daily").mkdir()
    for c, s in C.RATE_SERIES.items():
        pd.DataFrame({"value_date": ["2002-01-01"], "value": [5.0 if c == "USD" else 0.5],
                      "available_utc": pd.to_datetime(["2002-02-01"], utc=True)}).to_parquet(
            tmp_path / "daily" / f"fred_{s}.parquet")
    sw, _ = C.swap_table(tmp_path)
    g = sw[sw["instrument"] == C.GOLD]
    assert (g["long_per_night"] < 0).all()
    assert (g["short_per_night"] <= 0).all()                     # r_USD 5 % > markup: short would earn, floored at 0
    assert np.isclose(g["long_per_night"].iloc[0], -(0.05 + C.MARKUP_PA) / C.DAYS_PA)

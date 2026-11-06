"""POS-1 specs as code: every price-only spec is point in time on a synthetic panel, and the A1 universe is frozen."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research.position import pos1
from goldbot.research.position.signals import assert_point_in_time


def _panel(symbols: tuple[str, ...], n: int = 900) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2010-01-01", periods=n, name="date")
    out = {}
    for s in symbols:
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        f = pd.DataFrame({"open": c, "high": c * 1.004, "low": c * 0.996, "close": c}, index=idx)
        f["close_utc"] = (f.index + pd.Timedelta(hours=21)).tz_localize("UTC")
        out[s] = f
    return out


PRICE_ONLY = [sp for sp in pos1.SPECS if sp.id not in {"POS1-015", "POS1-016", "POS1-033", "POS1-060"}]


@pytest.mark.parametrize("spec", PRICE_ONLY, ids=lambda s: s.id)
def test_price_only_specs_are_point_in_time(spec):
    panel = _panel(tuple(spec.symbols))
    idx = panel[spec.symbols[0]].index
    assert_point_in_time(spec, panel, [idx[300], idx[600], idx[850]])


def test_month_end_is_a_calendar_rule():
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-28", "2024-02-29", "2024-03-28"])
    # 2024-03-29 (Good Friday) is a business day to the calendar rule, so 03-28 is not a month-end: a month whose last
    # business day has no bar keeps the previous decision (stated in amendment A1)
    assert pos1._month_end(idx).tolist() == [False, True, False, True, False]


def test_a1_universe_and_ids():
    assert len(pos1.U_A1) == 13 and "USDCAD" not in pos1.U_A1 and pos1.MIN_LIVE_A1 == 6
    ids = [s.id for s in pos1.SPECS]
    assert len(ids) == len(set(ids)) == 16
    assert {"POS1-026A", "POS1-027A", "POS1-057A"} <= set(ids)

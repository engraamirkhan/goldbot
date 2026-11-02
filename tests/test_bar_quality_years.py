"""scripts/bar_quality_years.py: per-year bar quality and the earliest usable pre-2010 year (bars only)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "bar_quality_years", Path(__file__).resolve().parents[1] / "scripts" / "bar_quality_years.py")
assert _spec is not None and _spec.loader is not None
bq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bq)


def _bars(year: int, keep_every: int = 1, spread: float = 0.3, flag: str = "", month: int = 1) -> pd.DataFrame:
    """Weekday minutes 00:00-22:59 UTC of one month (thinned to every `keep_every`th minute)."""
    ts = pd.date_range(f"{year}-{month:02d}-05", f"{year}-{month:02d}-27 22:59", freq="1min", tz="UTC")
    ts = ts[(ts.weekday < 5) & (ts.hour < 23)][::keep_every]
    bid = np.full(len(ts), 1000.0)
    return pd.DataFrame({"ts_utc": ts, "bid_close": bid, "ask_close": bid + spread, "spread_mean": spread,
                         "dq_flag": flag})


def test_dense_year_has_no_in_session_gaps():
    q = bq.year_quality(_bars(2012), 2012)
    assert q["gap_step_share"] == 0.0 and q["missing_min_share"] == 0.0
    assert q["min_per_day_p50"] == 1380 and abs(q["spread_bps_p50"] - 3.0) < 1e-9
    assert q["imputed_share"] == 0.0 and q["first_bar"] == "2012-01-05 00:00"


def test_thin_year_reports_gap_steps_and_missing_minutes():
    q = bq.year_quality(_bars(2004, keep_every=2), 2004)
    assert q["gap_step_share"] == 1.0 and 0.45 < q["missing_min_share"] < 0.55
    assert q["min_per_day_p50"] < bq.MIN_MEDIAN_MINUTES


def test_holdout_bars_are_never_counted():
    b = pd.concat([_bars(2025, month=9), _bars(2025, month=10)], ignore_index=True)
    assert bq.year_quality(b, 2025)["bars"] == len(_bars(2025, month=9))


def test_earliest_usable_is_the_first_year_of_an_unbroken_run_to_2009():
    rows = [bq.year_quality(_bars(2004), 2004), bq.year_quality(_bars(2005, keep_every=3), 2005)]
    rows += [bq.year_quality(_bars(y), y) for y in (2006, 2007, 2008, 2009, 2010)]
    t, earliest = bq.verdicts(rows, ref_years=range(2010, 2011))
    assert t.loc[2004, "usable"] and not t.loc[2005, "usable"] and earliest == 2006   # 2005 breaks the run
    assert "minutes/day" in t.loc[2005, "fails"]


def test_imputed_or_wide_spread_years_fail():
    rows = [bq.year_quality(_bars(2008, spread=5.0), 2008),
            bq.year_quality(_bars(2009, flag="warning:bid_imputed"), 2009), bq.year_quality(_bars(2010), 2010)]
    t, earliest = bq.verdicts(rows, ref_years=range(2010, 2011))
    assert "imputed" in t.loc[2009, "fails"] and "spread" in t.loc[2008, "fails"] and earliest is None
    assert "earliest usable pre-2010 year: none" in bq.render(t, earliest)


def test_missing_year_is_reported_not_usable():
    t, earliest = bq.verdicts([{"year": 2003, "bars": 0}, bq.year_quality(_bars(2010), 2010)],
                              ref_years=range(2010, 2011))
    assert t.loc[2003, "fails"] == "no bars" and earliest is None

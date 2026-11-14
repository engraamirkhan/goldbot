"""BACKLOG 238: a long hole in the best-ranked source of a long daily file (US500/UK100 2011-09-23..2012-01-18 and
2013-02-26..2013-05-22 in dukascopy_h1) is filled from the next-ranked free source (Yahoo index history, its source
kept), and a coverage check reports every weekday without a row that the published exchange calendar does not list as
a closure, so a hole is reported and never read as a holiday."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from goldbot.data.exchange_holidays import exchange_holidays
from goldbot.data.long_history import LONG_COLUMNS, coverage_gaps, stitch


def _seg(days: pd.DatetimeIndex, source: str, hour: int, level: float) -> pd.DataFrame:
    d = pd.DatetimeIndex(days)
    return pd.DataFrame({"date": d, "open": level, "high": level + 1, "low": level - 1, "close": level,
                         "source": source, "available_utc": (d + pd.Timedelta(hours=hour)).tz_localize("UTC")}
                        )[list(LONG_COLUMNS)]


def test_a_long_hole_in_the_upper_source_is_filled_from_the_lower_one_with_its_source_kept() -> None:
    all_days = pd.bdate_range("2011-09-01", "2012-02-29")
    hole = (all_days > pd.Timestamp("2011-09-22")) & (all_days < pd.Timestamp("2012-01-19"))
    upper = _seg(all_days[(all_days >= pd.Timestamp("2011-09-19")) & ~hole], "dukascopy_h1:USA500IDXUSD", 21, 1200.0)
    lower = _seg(all_days, "yahoo:^GSPC", 21, 1201.0)
    out, stats = stitch([upper, lower])
    got = out.set_index("date")["source"]
    assert got.loc["2011-10-03"] == "yahoo:^GSPC" and got.loc["2012-01-19"] == "dukascopy_h1:USA500IDXUSD"
    assert got.loc["2011-09-19"] == "dukascopy_h1:USA500IDXUSD"       # the upper source wins where it has a row
    assert len(out) == len(all_days)
    filled = [s for s in stats if s.cutover.startswith("hole ")]
    assert len(filled) == 1 and filled[0].rows_added == int(hole.sum())


def test_a_short_gap_is_not_filled() -> None:
    all_days = pd.bdate_range("2012-03-01", "2012-03-30")
    upper = _seg(all_days[(all_days != "2012-03-14") & (all_days != "2012-03-15")], "dukascopy_h1:X", 21, 10.0)
    lower = _seg(all_days, "yahoo:X", 21, 10.0)
    out, _ = stitch([upper, lower])
    assert len(out) == len(all_days) - 2


def test_a_hole_is_reported_and_is_not_an_exchange_holiday() -> None:
    days = pd.bdate_range("2013-01-02", "2013-06-28")
    hole = (days > pd.Timestamp("2013-02-25")) & (days < pd.Timestamp("2013-05-23"))
    df = _seg(days[~hole], "dukascopy_h1:USA500IDXUSD", 21, 1500.0)
    hol, _ = exchange_holidays("US")
    gaps = coverage_gaps(df, hol)
    epoch = date(1970, 1, 1)
    closed = set(np.asarray(hol).tolist())
    # Good Friday 2013 (29 March) is a published closure inside the hole: not counted as missing, the run goes on
    assert (date(2013, 3, 29) - epoch).days in closed
    assert gaps == [("2013-02-26", "2013-05-22", int(hole.sum()) - 1)]
    assert {(d.date() - epoch).days for d in days[hole]} & closed == {(date(2013, 3, 29) - epoch).days}

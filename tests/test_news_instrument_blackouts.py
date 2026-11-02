"""Multi-instrument demo plan (2026-11-02, lane L2): "news blackouts by instrument currencies + EIA crude/gas, WASDE".

* `instrument_blackout`: the tier-1 USD blackout gold has today (identical for a USD-only instrument), plus every
  high-impact event of the instrument's other currencies (registry `news_currencies`), plus the scheduled commodity
  reports of its correlation group.
* `commodity_report_window`: EIA's weekly petroleum status (Wednesday 10:30 New York) and natural gas storage
  (Thursday 10:30 New York) for energy; USDA's WASDE (12:00 New York) for ags. In a week with a US federal holiday EIA
  moves its releases, so the day before and the day after are blacked out too, at 10:30 and 12:00 (entries only, the
  safe direction). WASDE comes out between the 8th and the 12th: every weekday of that span is blacked out at 12:00.
"""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.data.econ_calendar import blackout_window
from goldbot.data.news import commodity_report_window, instrument_blackout


def _ev(*rows: tuple[str, str, str, str, int]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts_utc", "country", "title", "impact", "tier"])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


EVENTS = _ev(("2026-11-06 13:30", "USD", "Non-Farm Employment Change", "High", 1),
             ("2026-11-05 13:30", "USD", "Unemployment Claims", "Medium", 2),
             ("2026-11-05 12:00", "EUR", "Main Refinancing Rate", "High", 3),
             ("2026-11-05 09:30", "GBP", "Construction PMI", "Medium", 3),
             ("2026-11-04 23:30", "JPY", "BOJ Policy Rate", "high", 3))


def test_a_usd_only_instrument_blacks_out_exactly_as_gold_does_today():
    for now in pd.date_range("2026-11-04 20:00", "2026-11-06 16:00", freq="5min", tz="UTC"):
        assert instrument_blackout(EVENTS, now, ("USD",), 15, 30) == blackout_window(EVENTS, now, 15, 30), now


@pytest.mark.parametrize("currencies, now, hit", [
    (("EUR", "USD"), "2026-11-05 11:50", "Main Refinancing Rate"),
    (("USD",), "2026-11-05 11:50", None),                         # gold does not stop for the ECB
    (("GBP", "USD"), "2026-11-05 09:30", None),                   # medium impact: no blackout
    (("JPY", "USD"), "2026-11-04 23:40", "BOJ Policy Rate"),      # impact case does not matter
    (("EUR", "USD"), "2026-11-06 13:20", "Non-Farm Employment Change"),
])
def test_blackouts_by_instrument_currencies(currencies, now, hit):
    got = instrument_blackout(EVENTS, pd.Timestamp(now, tz="UTC"), currencies, 15, 30)
    assert (got or {}).get("title") == hit


def test_no_calendar_means_no_currency_blackout_but_the_reports_still_apply():
    now = pd.Timestamp("2026-11-04 15:30", tz="UTC")
    assert instrument_blackout(None, now, ("USD",), 15, 30) is None
    got = instrument_blackout(None, now, ("USD",), 15, 30, group="energy")
    assert got is not None and got["kind"] == "commodity_report"


@pytest.mark.parametrize("group, now, title", [
    ("energy", "2026-11-04 15:25", "EIA crude inventories"),       # Wednesday 10:30 EST = 15:30 UTC
    ("energy", "2026-11-04 17:00", None),
    ("energy", "2026-07-08 14:40", "EIA crude inventories"),       # summer: 10:30 EDT = 14:30 UTC
    ("energy", "2026-11-05 15:40", "EIA natural gas storage"),     # Thursday
    ("energy", "2026-09-10 14:30", "EIA crude inventories"),       # Labor Day week: moved to Thursday
    ("ags", "2026-11-10 17:00", "USDA WASDE"),                      # 12:00 EST
    ("ags", "2026-11-12 17:10", "USDA WASDE"),
    ("ags", "2026-11-20 17:00", None),
    ("ags", "2026-11-04 15:30", None),                              # EIA is not an ags report
    ("metals", "2026-11-04 15:30", None),
    ("gold", "2026-11-10 17:00", None),
])
def test_scheduled_commodity_reports(group, now, title):
    got = commodity_report_window(group, pd.Timestamp(now, tz="UTC"), 15, 30)
    assert (got or {}).get("title") == title


def test_wasde_on_a_weekend_day_of_the_span_is_not_listed():
    # 2026-08-08 and 09 are a weekend: no release, no blackout
    assert commodity_report_window("ags", pd.Timestamp("2026-08-08 16:00", tz="UTC"), 15, 30) is None

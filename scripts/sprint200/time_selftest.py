"""Self-test of scripts/sprint200/time_screen.py on synthetic bars and the rule calendars only (no market data).

Usage: uv run python scripts/sprint200/time_selftest.py"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import time_screen as T  # noqa: E402


def bars(start: str, prices: Sequence[tuple[float, float, float, float]]) -> pd.DataFrame:
    ts = pd.date_range(start, periods=len(prices), freq="1min", tz="UTC")
    d: dict[str, object] = {"ts_utc": ts}
    for side in ("bid", "ask"):
        for j, k in enumerate(("open", "high", "low", "close")):
            d[f"{side}_{k}"] = [p[j] for p in prices]
    return pd.DataFrame(d)


def main() -> None:
    # walk: long from 100, stop 1 at 99, target 1 at 101
    px = [(100, 100.5, 99.8, 100.2), (100.2, 100.4, 99.9, 100.0), (100.0, 101.2, 98.9, 100.0), (100, 100, 100, 100)]
    m = T.Market.of(bars("2016-03-02 10:00", px))
    w = T.walk(m, 0, 100.0, 1, 1.0, 1.0, int(m.ts[3]))
    assert w is not None and w[0] == "stop" and w[1] == 99.0, w            # both in minute 2: stop first
    w = T.walk(m, 0, 100.0, 1, 5.0, None, int(m.ts[3]))
    assert w is not None and w[0] == "time" and w[1] == 100.0, w           # time exit at the open of the E bar
    gap = [(100, 100, 100, 100), (97, 97.5, 96.5, 97), (97, 97, 97, 97)]
    m2 = T.Market.of(bars("2016-03-02 10:00", gap))
    w = T.walk(m2, 0, 100.0, 1, 1.0, None, int(m2.ts[2]))
    assert w is not None and w[0] == "stop" and w[1] == 97.0, w            # gap through the stop fills at the open
    w = T.walk(m2, 0, 100.0, -1, 5.0, 2.0, int(m2.ts[2]))
    assert w is not None and w[0] == "target" and w[1] == 98.0, w          # short target
    assert T.walk(m2, 0, 100.0, 1, 5.0, None, int(m2.ts[2]) + 10 * T.MIN) is None   # missing exit bar
    # P(T) and entry: P is the close of the bar before T; entry the open at T
    assert m.price(int(m.ts[2])) == 100.0 and m.entry_k(int(m.ts[2])) == 2
    assert np.isnan(m.price(int(m.ts[0]) - 20 * T.MIN))
    # calendars
    assert date(2016, 3, 28) in T.uk_bank_holidays(2016) and date(2016, 8, 29) in T.uk_bank_holidays(2016)
    assert date(2012, 6, 5) in T.uk_bank_holidays(2012) and date(2012, 5, 28) not in T.uk_bank_holidays(2012)
    assert date(2016, 12, 27) in T.uk_bank_holidays(2016) and date(2016, 12, 26) in T.uk_bank_holidays(2016)
    cs = T.calendar_sets()
    assert date(2016, 1, 29) in cs["post_fnd3"] and date(2016, 1, 28) in cs["pre_fnd5"]   # FND Fri 29 Jan 2016
    assert date(2016, 3, 30) in cs["month_last2"] and date(2016, 3, 31) in cs["month_last2"]
    assert not T.is_business_day(date(2016, 3, 25))                        # Good Friday: CME full closure
    assert date(2016, 3, 23) in cs["pre_us_hol2"] and date(2016, 3, 24) in cs["pre_us_hol2"]
    assert date(2016, 7, 4) in cs["thin"] and date(2016, 5, 2) in cs["thin"]
    # 2016-02: business days end ...23 24 25 26 29; 4th last = 24 (Wed): expiry window 22, 23, 24
    assert {date(2016, 2, 22), date(2016, 2, 23), date(2016, 2, 24)} <= cs["expiry3"]
    # percentile context uses strictly earlier values
    c = T.Ctx()
    for v in range(100):
        assert not c.pct_ok(float(v), 67, True)
    assert c.pct_ok(1000.0, 67, True) and not c.pct_ok(0.0, 67, True)
    # local clocks are DST-safe: 08:00 London is 08:00 UTC in January, 07:00 UTC in July
    assert pd.Timestamp(T.at(date(2016, 1, 4), T.LDN, 8, 0), tz="UTC").hour == 8
    assert pd.Timestamp(T.at(date(2016, 7, 4), T.LDN, 8, 0), tz="UTC").hour == 7
    print("time_selftest: ok")


if __name__ == "__main__":
    main()

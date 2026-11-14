"""BACKLOG 237: the exchange holiday calendar is refreshed yearly (this test fails once `valid_through` is less than a
year away, like holidays_cme_metals.yaml) and lists early closes, each with its own available_utc."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import exchange_holidays as xh


def test_valid_through_is_more_than_a_year_ahead() -> None:
    cal = xh.load_calendar()
    left = cal.valid_through - date.today()
    assert left.days > 365, (f"exchange_holidays.yaml is valid through {cal.valid_through}: run the yearly refresh "
                             "(scripts/build_exchange_holidays.py docstring, BACKLOG 237)")


def test_early_closes_are_listed_with_their_close_instant_and_availability() -> None:
    days, close, av = xh.early_closes("US")
    got = dict(zip(days.tolist(), zip(close.tolist(), av.tolist())))
    black_friday = (date(2026, 11, 27) - date(1970, 1, 1)).days
    assert got[black_friday] == (int(pd.Timestamp("2026-11-27 18:00", tz="UTC").value),     # 13:00 EST
                                 int(pd.Timestamp("2025-12-01", tz="UTC").value))
    uk = dict(zip(*xh.early_closes("UK")[:2]))
    assert uk[(date(2027, 12, 31) - date(1970, 1, 1)).days] == int(pd.Timestamp("2027-12-31 12:30", tz="UTC").value)
    assert len(xh.early_closes("DE")[0]) > 0


def test_an_early_close_is_not_read_before_it_is_published() -> None:
    days, _, av = xh.early_closes("US", known_by_ns=int(pd.Timestamp("2025-11-30", tz="UTC").value))
    assert (date(2026, 11, 27) - date(1970, 1, 1)).days not in set(days.tolist())
    assert (av <= int(pd.Timestamp("2025-11-30", tz="UTC").value)).all()
    # an unverified ad hoc early close is knowable only the day after
    d, _, a = xh.early_closes("US")
    k = int(np.flatnonzero(d == (date(1999, 12, 31) - date(1970, 1, 1)).days)[0])
    assert a[k] == int(pd.Timestamp("2000-01-01", tz="UTC").value)


def test_an_early_close_must_close_on_its_own_day_and_before_it_is_known(tmp_path: Path) -> None:
    text = xh.CALENDAR_PATH.read_text()
    bad = text.replace("close_utc: '2026-11-27T18:00:00Z'", "close_utc: '2026-11-28T18:00:00Z'", 1)
    assert bad != text
    p = tmp_path / "x.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="early close"):
        xh.load_calendar(p)


def test_a_calendar_without_early_closes_still_loads(tmp_path: Path) -> None:
    text = xh.CALENDAR_PATH.read_text()
    kept = [ln for ln in text.splitlines() if "close_utc" not in ln and "early_closes:" not in ln]
    p = tmp_path / "old.yaml"
    p.write_text("\n".join(kept) + "\n")
    assert len(xh.early_closes("US", path=p)[0]) == 0

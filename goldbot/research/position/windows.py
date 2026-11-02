"""The W2 window table and the holdout guard for position research.

W2 (adopted 2026-11-02): "LEARN (explore) 2010-01-01..2017-12-31; LEARN-OLD 2003-01-01..2005-12-31 (new trials only);
embargo 2006-01, not used; TEST-OLD 2006-02-01..2009-12-31, read ONCE, only for confirm-passers; CONFIRM
2018-01-01..2025-09-30, one test; HOLDOUT 2025-10-01 onward, sealed. A trade belongs to the window of its ENTRY;
warm-up may read earlier bars."

The holdout is enforced in code, not by convention: every loader cuts at `HOLDOUT_FROM_UTC` before it materialises a
row, and `assert_before_holdout` refuses any daily date or UTC stamp at or after it wherever a frame enters the
engine. It is independent of `research.holdout_from` on purpose (as `goldbot.research.arms`): a loosened setting
cannot widen a position run.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from pydantic import model_validator

from goldbot.base import FrozenRecord

HOLDOUT_FROM = pd.Timestamp("2025-10-01")                  # first daily date that is never read (naive day label)
HOLDOUT_FROM_UTC = pd.Timestamp("2025-10-01", tz="UTC")    # first instant that is never read


class HoldoutViolation(AssertionError):
    """A date or stamp at or after 2025-10-01 reached position research."""


def assert_before_holdout(times: pd.Index | pd.Series | pd.DatetimeIndex, *, what: str = "rows") -> None:
    """Raise HoldoutViolation if any date (naive day label) or stamp (tz-aware) is at or after the holdout."""
    idx = pd.DatetimeIndex(times).dropna()
    if len(idx) == 0:
        return
    bound = HOLDOUT_FROM_UTC if idx.tz is not None else HOLDOUT_FROM
    if idx.max() >= bound:
        n = int((idx >= bound).sum())
        raise HoldoutViolation(f"{n} {what} at or after {HOLDOUT_FROM.date()}: the holdout is sealed")


class Window(FrozenRecord):
    """One W2 window: daily dates [start, end], both inclusive. Construction refuses an end in the holdout."""
    name: str
    start: date
    end: date
    use: str

    @model_validator(mode="after")
    def _before_holdout(self) -> Window:
        if self.start > self.end:
            raise ValueError(f"window {self.name} starts after it ends")
        if pd.Timestamp(self.end) >= HOLDOUT_FROM:
            raise HoldoutViolation(f"window {self.name} reaches the holdout ({HOLDOUT_FROM.date()})")
        return self

    @property
    def first(self) -> pd.Timestamp:
        return pd.Timestamp(self.start)

    @property
    def last(self) -> pd.Timestamp:
        return pd.Timestamp(self.end)


WINDOWS: dict[str, Window] = {w.name: w for w in (
    Window(name="LEARN", start=date(2010, 1, 1), end=date(2017, 12, 31), use="explore"),
    Window(name="LEARN-OLD", start=date(2003, 1, 1), end=date(2005, 12, 31), use="explore (new trials only)"),
    Window(name="TEST-OLD", start=date(2006, 2, 1), end=date(2009, 12, 31), use="held-out test, read once"),
    Window(name="CONFIRM", start=date(2018, 1, 1), end=date(2025, 9, 30), use="one test"),
)}
# W3 addendum (docs/research/pos1/w3-addendum.md, ruling 2): POS-1 explore is LEARN-OLD + LEARN read as one series,
# each part flat at its own edges; t, the years rule and the W3.4 counts are taken on the concatenation
COMPOSITES: dict[str, tuple[str, ...]] = {"EXPLORE": ("LEARN-OLD", "LEARN")}
LEARN_WINDOWS = COMPOSITES["EXPLORE"]          # the W3.1 Y_learn years
EXPLORE_WINDOWS = ("LEARN", "LEARN-OLD", "EXPLORE")   # read without a frozen pre-registration; the others need one
ONCE_WINDOWS = ("TEST-OLD", "CONFIRM")         # read once per (spec, window): a frozen pre-registration and a marker


def get_window(name: str) -> Window:
    try:
        return WINDOWS[name]
    except KeyError:
        raise KeyError(f"unknown window {name!r}: one of {sorted(WINDOWS)}") from None


def get_windows(name: str) -> list[Window]:
    """A W2 window, or a composite's parts in time order."""
    return [get_window(n) for n in COMPOSITES[name]] if name in COMPOSITES else [get_window(name)]

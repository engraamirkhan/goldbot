"""The signal interface of the position engine and the point-in-time check every run makes.

A signal is a function `(symbol, panel) -> Series` over the panel's daily dates: the value on date d is decided at d's
21:00 UTC close from rows dated d or earlier, and is filled at the next 1m open (W3.2). `panel` maps every market (and
any auxiliary series such as DTB3) to its daily frame, so a cross-asset rule reads what it needs. A NaN means "no
decision today": the engine keeps the held position (a month-end rule returns NaN on the other days).

`sizing="vol"`: the value is a signal in [-1, 1] that the engine vol-scales to 10%/yr (W3.6). `sizing="unit"`: the
value is already an exposure in units of equity (Turtle units, RT-STRATEGY-045's variance rule) and is only capped.

Point in time is checked, not trusted: `assert_point_in_time` recomputes the signal on the panel cut at each of a few
dates T and refuses (LookaheadError) any value on or before T that differs from the full-panel value.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord

Panel = Mapping[str, pd.DataFrame]
SignalFn = Callable[[str, Panel], pd.Series]


class LookaheadError(AssertionError):
    """A signal's value on or before T changed when rows after T were removed."""


class PositionSpec(FrozenRecord):
    """One pre-registered position spec: the markets it trades and how its signal becomes a target exposure."""
    id: str = Field(min_length=1)
    symbols: tuple[str, ...] = Field(min_length=1)
    signal: SignalFn
    sizing: Literal["vol", "unit"] = "vol"
    band: float | None = Field(None, ge=0, lt=1)      # fraction of the target; None: research.position.band (10%)
    stop_atr: float | None = Field(None, gt=0)        # a tighter stop the rule states; None: the 3 x ATR disaster stop
    long_only: bool = False
    min_live: int = Field(1, ge=1)                    # the book is flat on days with fewer live markets
    book_target_vol: float | None = Field(None, gt=0)  # a portfolio target on a 60-day covariance (026, 057)


def truncate_panel(panel: Panel, at: pd.Timestamp) -> dict[str, pd.DataFrame]:
    return {k: v.loc[v.index <= at] for k, v in panel.items()}


def evaluate(spec: PositionSpec, panel: Panel) -> dict[str, pd.Series]:
    """The spec's signal per traded symbol, aligned to that symbol's dates (extra dates are refused upstream)."""
    out = {}
    for s in spec.symbols:
        v = spec.signal(s, panel)
        if not isinstance(v, pd.Series):
            raise TypeError(f"signal of {spec.id} for {s} returned {type(v).__name__}, not a Series")
        out[s] = v.astype(float)
    return out


def assert_point_in_time(spec: PositionSpec, panel: Panel, cuts: Iterable[pd.Timestamp]) -> None:
    full = evaluate(spec, panel)
    for cut in cuts:
        part = evaluate(spec, truncate_panel(panel, pd.Timestamp(cut)))
        for s, v in part.items():
            ref = full[s].reindex(v.index)
            a, b = v.to_numpy(dtype=float), ref.to_numpy(dtype=float)
            same = (a == b) | (np.isnan(a) & np.isnan(b)) | np.isclose(a, b, rtol=1e-12, atol=1e-12)
            if not bool(same.all()):
                first = v.index[~same][0]
                raise LookaheadError(f"signal {spec.id} for {s} on {first.date()} changes when the panel is cut at "
                                     f"{pd.Timestamp(cut).date()}: it reads later rows")


def constant(value: float) -> SignalFn:
    """The same value every day (buy-and-hold at +1: W3.2's drift benchmark)."""
    def fn(symbol: str, panel: Panel) -> pd.Series:
        return pd.Series(float(value), index=panel[symbol].index)
    return fn

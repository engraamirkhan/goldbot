"""Live computation of the structure replica (`goldbot.features.smc_replica`) on our own bars, no TradingView
(owner directive 2026-10-18: the replicated indicators are the system's own).

Research computes the replica on the whole series from its pinned start (`specialists.smc_structure.SERIES_START`,
prereg B4a). The engine computes features on a window (`EngineConfig.feature_window`, 800 decision bars). Two parts of
the replica depend on where the series starts:

* the FVG auto threshold, a running mean of |body %| from the first bar. A window continues it exactly with the sum
  and count of the bars before it (`fair_value_gaps(prior_sum=, prior_n=)`); `FvgAnchor` carries them;
* the leg-pivot and break state. It is re-established by the window's own pivots: on Dukascopy 15m 2010-2025 the
  structure events and the swing trend on the last bar of 2,000- and 5,000-bar windows matched the full series on
  every sampled bar (prereg B4b lane notes), but a pivot older than the window that is still unbroken is not seen,
  so it is not guaranteed for every window length: a live start compares the window's last-bar events with the
  store series' before trusting it (not wired yet: HANDOFF).

`FvgAnchor.from_bars` is built once from the store's bars since the series start and `advance`d as bars close;
`ctx_for(window)` gives the `ctx` entry (`smc_fvg_prior`) that `replica_frame` and
`SmcStructureSpecialist.candidates_in_context` read. Nothing here reads a bar after the window's last close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import utc_index
from goldbot.features import smc_replica as smc

PRIOR_KEY = "smc_fvg_prior"


def _bodies(bars: pd.DataFrame) -> np.ndarray:
    return smc.body_abs_pct(bars["open"].to_numpy(dtype=float), bars["close"].to_numpy(dtype=float))


class FvgAnchor(FrozenRecord):
    """The FVG threshold's running state: sum of |body %| and number of the bars of the series (from
    `series_start_utc`) that open before `through_utc`."""

    series_start_utc: UtcTimestamp
    through_utc: UtcTimestamp
    abs_body_pct_sum: float = Field(ge=0)
    n_bars: int = Field(ge=0)

    @classmethod
    def from_bars(cls, bars: pd.DataFrame, through: pd.Timestamp) -> FvgAnchor:
        """From mid bars that start the series (open, close, ts_utc): the bars opening before `through`."""
        ts = utc_index(bars["ts_utc"])
        keep = np.asarray(ts < through)
        return cls(series_start_utc=ts[0], through_utc=through,
                   abs_body_pct_sum=float(np.nansum(_bodies(bars)[keep])), n_bars=int(keep.sum()))

    def advance(self, bars: pd.DataFrame, through: pd.Timestamp) -> FvgAnchor:
        """The anchor moved to `through`, counting the bars of `bars` that open in [through_utc, through). `bars` must
        hold every bar of that span (the series' own bars); a later `through` only."""
        if through < self.through_utc:
            raise ValueError("an anchor only moves forward")
        ts = utc_index(bars["ts_utc"])
        if len(ts) == 0 or ts[0] > self.through_utc:
            raise ValueError(f"the bars start {ts[0] if len(ts) else 'nowhere'}, after the anchor "
                             f"{self.through_utc}: the bars in between are missing")
        keep = np.asarray((ts >= self.through_utc) & (ts < through))
        return self.model_copy(update={"through_utc": through,
                                       "abs_body_pct_sum": self.abs_body_pct_sum + float(np.nansum(_bodies(bars)[keep])),
                                       "n_bars": self.n_bars + int(keep.sum())})

    def prior_for(self, window: pd.DataFrame) -> tuple[float, int]:
        """(sum, n) over the series' bars before the window's first bar. The window must start at or before
        `through_utc` (its bars before it are taken off the anchor) and be the series' own bars, without gaps."""
        ts = utc_index(window["ts_utc"])
        if len(ts) == 0 or ts[0] > self.through_utc:
            raise ValueError(f"the window starts after the anchor ({self.through_utc}): advance the anchor first")
        before = np.asarray(ts < self.through_utc)
        k = int(before.sum())
        if k > self.n_bars:
            raise ValueError("the window holds more bars before the anchor than the anchor counted")
        return self.abs_body_pct_sum - float(np.nansum(_bodies(window)[before])), self.n_bars - k

    def ctx_for(self, window: pd.DataFrame) -> dict[str, tuple[float, int]]:
        return {PRIOR_KEY: self.prior_for(window)}

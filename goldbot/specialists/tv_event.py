"""TradingView event specialist (plan docs/plans/tradingview-strategies.md, lane C): one catalogued event plot with a
known side (goldbot/data/tv_catalog.py: `PlotSpec.can_trigger`, e.g. a "Buy large" triangle) is the primary signal.

DESIGN promotion table: "TradingView indicators: filter features only; promoted to a trigger specialist only if
ablation shows > 1% log-loss gain in two consecutive retrains and webhook delivery is >= 99% on time over 90 days". So
this family is shadow only (`requires_tv`): the engine never sends one of its entries to the broker (`Engine._decide`)
until that rule is met and recorded; its 15m/1h configurations are recorded forward in the shadow book for the
performance-analyst, and 4h/1d are searched pre-holdout under TV_GRID_V1 (research.tv_grid).

* Signal. On the decision bar (open `t`, close `c`), the plot's TradingView bar with `bar_time == t` counts only when it
  arrived (`available_utc`) no later than `c + max_lag` (settings `tv.max_lag_s`, 60 s = tv_plots.MAX_LAG) and no
  later than the decision itself; the join is `asof_join` on `available_utc`. Anything else (late by 1 s, not yet
  arrived, another bar, a store outage) is NaN and makes no candidate: staleness fails closed. The mark on the bar
  enters on the plot's side. Research decides as if at `c + max_lag`, the latest the live rule accepts, and its labels
  enter at the open of the first 1m bar at/after it (`entry_delay_s`, `labels.triple_barrier_delayed`): nothing
  between the close and the entry reaches a label. The engine never delays the close: the shadow book re-checks the
  agent until `c + max_lag` and enters at the price when the value is seen (engine/tv_context.py, `TvShadowWatch`).
* Bars. The TradingView bar must open with our decision bar; research refuses a trial whose bars do not line up
  (research.tv_grid `bar_match`, `tv.min_bar_match`): our 4h/1d grids are not TradingView's session grid.
* Which rows. Those `tv_plots` may read (`tv_plots.eligible_rows`: this study, version, timeframe and input set;
  live rows, or repaint-validated history; a live row wins on its bar), so research and live read one rule.
* Barriers. ATR(14) of the decision bars, target / stop / time as configured (TV_GRID_V1 moves them +-25 %).
* Lot fit. A candidate the minimum lot cannot carry within the RiskGate's rule (`research.min_lot`, at the
  `lot_fit` account: settings `research.lot_fit_*` via `LotFitSpec.from_settings`; the default, $1,000 at 0.5 %, is
  no larger than the real account) is not proposed: "no trade the account cannot take". The account is not part of
  the configuration (or its agent id); RiskGate still sizes and refuses at execution.

Opt-in: not imported by goldbot.specialists; `enable()` registers the family, so no default population, pooled model
or loop grid changes.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.data.tv_catalog import PlotSpec, slug
from goldbot.features.technical import atr
from goldbot.features.tv_plots import MAX_LAG, bar_frame, eligible_rows, plot_name
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.research.lot_fit import LotFitSpec
from goldbot.research.min_lot import min_lot_feasibility
from goldbot.specialists.base import SPECIALISTS, AgentIdentity, Specialist
from goldbot.webhook.app import tf_seconds as tv_seconds

FAMILY = "tv_event"
TV_TF: dict[str, str] = {"15m": "15", "1h": "60", "4h": "240", "1d": "1D"}   # decision tf -> TradingView's spelling
PLOT_KEYS = ("study", "version", "meta_hash", "plot_id", "side")              # the plot: not a searched parameter


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


class TvEventSpecialist(Specialist):
    family = FAMILY
    timeframe = "4h"
    timeframes = ("15m", "1h", "1d")
    requires_tv = True
    entry_delay_s = int(MAX_LAG.total_seconds())     # research enters at the first 1m open after close + max_lag
    default_config = {"study": "", "version": "", "meta_hash": "", "plot_id": "", "side": 0,
                      "target_atr": 2.0, "stop_atr": 1.0, "max_bars": 12}       # 4h: two days
    timeframe_defaults = {"15m": {"max_bars": 32}, "1h": {"max_bars": 24}, "1d": {"max_bars": 10}}

    def __init__(self, identity: AgentIdentity | None = None, *, lot_fit: LotFitSpec | None = None, **overrides: Any) -> None:
        super().__init__(identity, **overrides)
        c = self.config
        if not (c["study"] and c["meta_hash"] and c["plot_id"]) or c["side"] not in (1, -1):
            raise ValueError("a TradingView event specialist needs an event plot with a known side "
                             "(TvEventSpecialist.from_plot)")
        self.lot_fit = lot_fit or LotFitSpec()

    @classmethod
    def from_plot(cls, spec: PlotSpec, *, lot_fit: LotFitSpec | None = None, **overrides: Any) -> TvEventSpecialist:
        """The specialist of one catalogued plot: an event with a known side, on the decision timeframe it was drawn
        on (`overrides` may set `timeframe` and the barriers)."""
        if not spec.can_trigger or spec.side is None:
            raise ValueError(f"{spec.feature_id} is not an event with a known side: a feature only, never a trigger")
        tf = overrides.get("timeframe", cls.timeframe)
        if TV_TF.get(tf) != spec.tf:
            raise ValueError(f"{spec.feature_id} is drawn on TradingView timeframe {spec.tf}, not the decision "
                             f"timeframe {tf}")
        plot = {"study": spec.study, "version": spec.version, "meta_hash": spec.meta_hash, "plot_id": spec.plot_id,
                "side": int(spec.side)}
        own = {} if tf == cls.timeframe else {"timeframe": tf}
        return cls(lot_fit=lot_fit, **{**overrides, **own, **plot})

    @property
    def plot(self) -> PlotSpec:
        c = self.config
        tf = TV_TF[self.timeframe]
        return PlotSpec(feature_id=self.column, study=c["study"], version=c["version"], meta_hash=c["meta_hash"],
                        tf=tf, plot_id=c["plot_id"], title="", plot_type="shapes", kind="event", side=c["side"])

    @property
    def column(self) -> str:
        """The decision-bar signal column (1 fired, 0 not, NaN unknown). Not a `tv_` feature id: the model never
        sees it, and `tv_plots`' columns (aligned at the bar open) are a different reading."""
        c = self.config
        return f"tvsig_{slug(c['study'])}_{c['meta_hash']}_{slug(TV_TF[self.timeframe])}_{slug(c['plot_id'])}"

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name=FAMILY)

    def decision_events(self, long: pd.DataFrame | None, mid_bars: pd.DataFrame, *, now: pd.Timestamp | None = None,
                        max_lag: pd.Timedelta = MAX_LAG) -> np.ndarray:
        """Per decision bar: 1.0 the mark is on it, 0.0 it arrived without the mark, NaN not usable (see the module
        docstring). `now`: the live decision time (None in research: each bar decides at its close + max_lag)."""
        out = np.full(len(mid_bars), np.nan)
        if long is None or len(long) == 0 or len(mid_bars) == 0:
            return out
        spec = self.plot
        d = eligible_rows(long, spec)
        d = d[d["plot"] == plot_name(spec.plot_id)]
        if d.empty:
            return out
        frame = bar_frame(d, pd.Timedelta(seconds=tv_seconds(spec.tf)))
        frame = frame.rename(columns={"bar_time": "bar"})
        limit = epoch_ns(utc_index(mid_bars["visible_at"])) + max_lag.value
        asof = limit if now is None else np.minimum(limit, epoch_ns(pd.DatetimeIndex([now]))[0])
        # the latest row known at the decision (available_utc <= min(close + max_lag, now)) counts only when it is the
        # decision bar's own: a later arrival, an earlier bar or nothing at all reads NaN
        left = pd.DataFrame({"ts_utc": pd.to_datetime(asof, utc=True), "_row": np.arange(len(mid_bars))})
        j = asof_join(left, frame, prefix="_tv_").sort_values("_row")
        same = np.asarray(utc_index(j["_tv_bar"]) == utc_index(mid_bars["ts_utc"]))
        v = pd.to_numeric(j[f"_tv_{plot_name(spec.plot_id)}"], errors="coerce").to_numpy(dtype=float)
        fired = (np.isfinite(v) & (v != 0)).astype(float)
        return np.where(same, fired, np.nan)

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """The bars where the plot's mark is on the decision bar (signal column == 1), on the plot's side, that the
        minimum lot can carry. No signal column, NaN or 0: no candidate."""
        if self.column not in features.columns:
            return _empty()
        sig = pd.to_numeric(features[self.column], errors="coerce").to_numpy(dtype=float)
        idx = np.flatnonzero(sig == 1.0)
        if len(idx) == 0:
            return _empty()
        a = atr(mid_bars, 14).to_numpy(dtype=float)
        close = mid_bars["close"].to_numpy(dtype=float)
        idx = np.array([i for i in idx if self._fits(float(close[i]), float(a[i]))], dtype=int)
        return pd.DataFrame({"idx": idx, "side": np.full(len(idx), int(self.config["side"]), dtype=int)})

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None,
                              ctx: dict[str, Any] | None = None) -> pd.DataFrame:
        """Research: the signal from ctx["tv_indicators"] by the live rule (decided at close + max_lag)."""
        sig = self.decision_events((ctx or {}).get("tv_indicators"), mid_bars)
        return self.candidates(mid_bars, features.assign(**{self.column: sig}))

    def _fits(self, price: float, atr_usd: float) -> bool:
        """The RiskGate's minimum-lot rule at the `lot_fit` account (research.min_lot): False when the minimum lot
        would risk more than the gate allows, or the stop cannot be sized (ATR not positive)."""
        lf = self.lot_fit
        if not (math.isfinite(price) and math.isfinite(atr_usd) and atr_usd > 0):
            return False
        f = min_lot_feasibility(price=price, atr_usd=atr_usd, stop_atr=float(self.config["stop_atr"]),
                                risk_fraction=lf.risk_per_trade, equity=lf.equity / lf.acct_per_usd, lot=lf.lot,
                                multiplier=lf.multiplier, size_down=lf.size_down,
                                multiplier_bounds=lf.multiplier_bounds, max_risk_per_trade=lf.max_risk_per_trade,
                                stops_level_points=lf.stops_level_points, spread_points=lf.spread_points)
        return bool(f.allowed)


def enable() -> str:
    """Register the family (idempotent) and return its name."""
    SPECIALISTS.setdefault(FAMILY, TvEventSpecialist)
    return FAMILY

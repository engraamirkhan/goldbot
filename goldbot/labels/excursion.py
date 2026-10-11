"""Maximum adverse and favourable excursion (MAE / MFE; BACKLOG 23, playbook G-9): how far a trade went against it
and in its favour while it was open, in price, in R of its initial risk (|entry - initial stop|) and when.

One definition for the three places that report it, so they agree:
* labels (`label_excursions`, research) and the shadow book (`engine/shadow.py`) read completed bars between entry
  and exit; the live engine (`engine/runner.py`) reads every tick while the position is open.
* Side-correct, as the labels' barriers: the price is the one the position would exit at. A long's MAE comes from
  the bid low and its MFE from the bid high; a short's MAE from the ask high and its MFE from the ask low. A trade
  entered on the paying side therefore starts with an MAE of at least the spread.
* The track starts at the entry price at entry time (excursion 0). A later price counts only if strictly beyond the
  extreme so far, so the time is the first time the extreme was reached.
* Bars are counted from the first bar after the signal bar through the exit bar, as the labels' barrier search. On
  the exit bar bars carry no intra-bar order, so the labels' convention decides (stop first, conservative): a stop or
  trail exit counts the adverse side only up to the exit price and none of that bar's favourable side (the stop is
  assumed first, the trade was out); a target exit counts the favourable side only up to the target and the whole
  adverse side; a time or hard-flat exit (at the bar's close) counts the whole bar. A stop gapped through is filled at
  the stop in labels and shadow (labels.exit_policy), so their MAE never exceeds -1 R on a stop; the live MAE includes
  the broker's actual fill.
* R is from price, gross of costs: side x (extreme - entry) / |entry - initial stop|. MAE in R is <= 0 (adverse),
  MFE in R >= 0 except on a trade that never traded through its entry price.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import Record, UtcTimestamp

ADVERSE_EXITS = frozenset({"stop", "trail"})       # the exit price is on the adverse side of the exit bar
FAVOURABLE_EXITS = frozenset({"target"})           # the exit price is on the favourable side of the exit bar
FIELDS = ("mae_price", "mae_r", "mae_utc", "mfe_price", "mfe_r", "mfe_utc")


class Excursion(Record):
    """The running extremes of one position (exit-side price and when each was first reached)."""

    mae_price: float
    mae_utc: UtcTimestamp
    mfe_price: float
    mfe_utc: UtcTimestamp

    @classmethod
    def start(cls, entry: float, at: pd.Timestamp) -> Excursion:
        return cls(mae_price=float(entry), mae_utc=at, mfe_price=float(entry), mfe_utc=at)

    def fold(self, side: int, adverse: float, adverse_utc: pd.Timestamp, favourable: float | None = None,
             favourable_utc: pd.Timestamp | None = None) -> None:
        """Take one observation: the worst and best exit-side price of a bar (or one tick's price for both)."""
        if np.isfinite(adverse) and side * (adverse - self.mae_price) < 0:
            self.mae_price, self.mae_utc = float(adverse), adverse_utc
        fav = adverse if favourable is None else favourable
        fav_utc = adverse_utc if favourable_utc is None else favourable_utc
        if np.isfinite(fav) and side * (fav - self.mfe_price) > 0:
            self.mfe_price, self.mfe_utc = float(fav), fav_utc

    def summary(self, side: int, entry: float, initial_stop: float | None) -> dict[str, Any]:
        """The record fields (FIELDS): prices, R of the initial risk (None without a usable stop) and times."""
        risk = abs(entry - initial_stop) if initial_stop is not None else 0.0
        ok = np.isfinite(risk) and risk > 0
        return {"mae_price": self.mae_price, "mae_utc": self.mae_utc,
                "mae_r": side * (self.mae_price - entry) / risk if ok else None,
                "mfe_price": self.mfe_price, "mfe_utc": self.mfe_utc,
                "mfe_r": side * (self.mfe_price - entry) / risk if ok else None}


def exit_bar_extremes(side: int, favourable: float, adverse: float, exit_price: float,
                      barrier: str) -> tuple[float, float]:
    """(favourable, adverse) of the exit bar under the labels' stop-first convention (module docstring)."""
    if barrier in ADVERSE_EXITS:
        adv = exit_price if side * (adverse - exit_price) < 0 else adverse
        return exit_price, adv
    if barrier in FAVOURABLE_EXITS:
        return (exit_price if side * (favourable - exit_price) > 0 else favourable), adverse
    return favourable, adverse


def bar_prices(side: int, bar: Any) -> tuple[float, float]:
    """(favourable, adverse) exit-side extremes of one bar (store schema): bid high/low for a long, ask low/high for a
    short."""
    if side > 0:
        return float(bar["bid_high"]), float(bar["bid_low"])
    return float(bar["ask_low"]), float(bar["ask_high"])


def label_excursions(bars: pd.DataFrame, labels: pd.DataFrame, stop_atr: float, atr: pd.Series) -> pd.DataFrame:
    """MAE / MFE of each triple-barrier label (`triple_barrier` output: idx, side, t_entry, t_exit, entry, exit,
    barrier_hit) over bars t_entry..t_exit, the initial stop at entry - side x stop_atr x ATR of the signal bar, as
    the labels place it. Returns FIELDS per label on the labels' index; times are bar open times (`ts_utc`), the
    entry's the signal bar's, as the shadow book stamps them."""
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    a = atr.to_numpy(dtype=float)
    rows = []
    for _, lab in labels.iterrows():
        side, entry, idx = int(lab["side"]), float(lab["entry"]), int(lab["idx"])
        e, x = int(lab["t_entry"]), int(lab["t_exit"])
        ex = Excursion.start(entry, ts[idx])
        for j in range(e, x + 1):
            fav, adv = bar_prices(side, bars.iloc[j])
            if j == x:
                fav, adv = exit_bar_extremes(side, fav, adv, float(lab["exit"]), str(lab["barrier_hit"]))
            ex.fold(side, adv, ts[j], fav, ts[j])
        rows.append(ex.summary(side, entry, entry - side * stop_atr * a[idx]))
    return pd.DataFrame(rows, index=labels.index, columns=list(FIELDS))

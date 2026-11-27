"""Buy-and-hold of the same market after costs and financing (ADR 0106 decision 4).

ADR 0106 decision 4: "Buy-and-hold of the same market over 6+ months, after costs, is added to the random twin. Both
must be beaten." Goal of record: "Beats buy-and-hold of the same market over 6+ months, after costs."

The book, as computed here (fixed before any paper read; the quant rules on changes):

* One unit of the instrument bought at the first close in the window and held, long, to the last close: the gross
  return is last / first - 1 (a held position compounds by itself; nothing is rebalanced).
* Costs: one round trip (`round_trip_per_unit`, price units per unit: spread plus commission plus slippage) charged at
  the entry price, as a fraction of it.
* Financing: the broker's LONG swap rate for every rollover held (`labels.triple_barrier.rollover_nights` on the swap's
  server clock, the triple day counted, a seven-night spec every night), each night at the close it was held from
  (`SwapSpec.usd_per_oz_at`). Signed as the broker books it: a credit counts. The benchmark is a bar to beat, so it is
  never understated (a paid-only rule would flatter every strategy against it in a positive-carry year).
* net = gross + financing - cost, in fractions of the entry price: the return of the same capital held unlevered.
* Sharpe: on the daily net returns between consecutive closes (the cost on the first), annualised by
  SESSION_DAYS_PER_YEAR, the unit the paper gate's own Sharpe uses.

Reporting and gate input only: nothing here trades, sizes or changes a limit.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights

SESSION_DAYS_PER_YEAR = 260          # Monday-Friday opportunity days a year: the paper gate's and this Sharpe's unit


class BuyHoldRead(FrozenRecord):
    """Buy-and-hold of one instrument over a window, after one round trip and the long swap (module docstring)."""
    symbol: str
    start_utc: UtcTimestamp            # the first close held from
    end_utc: UtcTimestamp              # the last close
    days: int = Field(ge=2)            # closes in the window
    nights: int = Field(ge=0)          # rollovers charged
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    gross_ret: float
    cost_frac: float = Field(ge=0)
    financing_frac: float              # broker sign: negative = paid
    net_ret: float
    sharpe_ann: float | None = None    # None below two daily returns or with no dispersion

    def text(self) -> str:
        sharpe = "n/a" if self.sharpe_ann is None else f"{self.sharpe_ann:.2f}"
        return (f"buy-and-hold {self.symbol} {self.net_ret:+.2%} net ({self.gross_ret:+.2%} gross, cost "
                f"{self.cost_frac:.3%}, financing {self.financing_frac:+.2%} over {self.nights} nights), Sharpe {sharpe}, "
                f"{self.start_utc:%Y-%m-%d}..{self.end_utc:%Y-%m-%d}")


class BenchmarkLine(FrozenRecord):
    """The two benchmarks ADR 0106 decision 4 sets side by side for a read: the random twin and buy-and-hold. Report
    only: frozen cards, G3 and the confirm thresholds do not read it."""
    random_twin_mean_r: float | None = None
    buy_hold: BuyHoldRead | None = None

    def text(self) -> str:
        twin = ("random twin not supplied" if self.random_twin_mean_r is None
                else f"random twin {self.random_twin_mean_r:+.4f} R/trade")
        bh = "buy-and-hold not supplied" if self.buy_hold is None else self.buy_hold.text()
        return f"{twin}; {bh}"


def buy_and_hold(closes: pd.Series, *, symbol: str, round_trip_per_unit: float, swap: SwapSpec,
                 start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> BuyHoldRead | None:
    """Buy-and-hold over the closes in [start, end] (module docstring). `closes`: close prices indexed by the UTC
    instant of each close, ascending. None with fewer than two closes in the window. A close that is not a positive
    finite price is refused (ValueError): a hole in the benchmark must never read as a flat day."""
    if round_trip_per_unit < 0 or not math.isfinite(round_trip_per_unit):
        raise ValueError(f"round trip must be finite and >= 0, got {round_trip_per_unit}")
    idx = pd.DatetimeIndex(closes.index)
    if idx.tz is None:
        raise ValueError("closes must be indexed by tz-aware UTC instants")
    s = pd.Series(closes.to_numpy(float), index=idx.tz_convert("UTC")).sort_index()
    if start is not None:
        s = s[s.index >= pd.Timestamp(start)]
    if end is not None:
        s = s[s.index <= pd.Timestamp(end)]
    if len(s) < 2:
        return None
    px = s.to_numpy(float)
    if not (np.isfinite(px).all() and (px > 0).all()):
        raise ValueError(f"{symbol}: every close must be a positive finite price")
    t = pd.DatetimeIndex(s.index)
    nights = rollover_nights(t[:-1], t[1:], swap.server_tz, swap.triple_weekday, swap.seven_night)
    rate = swap.usd_per_oz_at(np.ones(len(px) - 1), px[:-1])          # per unit per night at each held-from close
    fin = nights * rate / px[:-1]
    cost = round_trip_per_unit / px[0]
    daily = px[1:] / px[:-1] - 1.0 + fin
    daily[0] -= cost
    sd = float(np.std(daily, ddof=1)) if len(daily) >= 2 else 0.0
    sharpe = float(np.mean(daily)) / sd * math.sqrt(SESSION_DAYS_PER_YEAR) if sd > 0 else None
    gross = float(px[-1] / px[0] - 1.0)
    financing = float(fin.sum())
    return BuyHoldRead(symbol=symbol, start_utc=t[0], end_utc=t[-1], days=len(px), nights=int(nights.sum()),
                       entry_price=float(px[0]), exit_price=float(px[-1]), gross_ret=gross, cost_frac=float(cost),
                       financing_frac=financing, net_ret=gross + financing - float(cost), sharpe_ann=sharpe)

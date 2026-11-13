"""The 20-day measured cost/model series (BACKLOG 90): the input of ADR 0031's frozen cost rule, "20-day measured
cost/model > 1.25 -> floors, 1.10-1.25 -> one step down". The rule's thresholds live in `goldbot.risk.adaptive`
(COST_FLOOR, COST_STEP); this module only measures. It can only tighten: the controller has no loosening use for it.

Definition (pre-registered before any replay; fixed here, every amount USD per oz, adverse positive):

* Population: the account's closed demo fills on gold (`ClosedTrade.symbol` None or XAUUSD) that closed in the last
  WINDOW_RISK_DAYS risk days before the decision day: the UTC weekdays (a weekend is no risk day, ADR 0031 reading e),
  cut off strictly before the decision day's 00:00 UTC.
* Measured, per fill: spread at the fill (`decision_spread_points` x the point: the quote the order was priced from)
  + entry slippage side x (entry_price - requested_price) + exit slippage, the lots-weighted side x (requested -
  filled) of the position's store `exit_fills` legs (a broker stop or target leg is asked at its level, so a stop's
  overshoot is in it) + commission both sides and the paid swap at the account's own measured terms (its nightly cost
  table, else its broker's settings prior: `CostImputer.terms_for`, currency-free, the same rule as the closed-trade
  charge).
* Model, per fill: what the research labels charge for the same trade (labels/triple_barrier.py: in at the ask, out at
  the bid, plus the extra cost and the swap): the ask - bid close of the last 15m bar closed at the entry (an as-of
  join on the bar's close time, at most BAR_TOLERANCE old) + the canonical cost table's extra cost
  (`CostTable.extra_cost_usd`, what retraining charges) + the paid swap at the model's swap (`live_swap`). A swap
  credit counts on neither side.
* Ratio: pooled, sum(spread) + max(0, sum(slippage)) + sum(commission) + sum(swap) over sum(model). Slippage is pooled
  signed and floored after (as the cost of record does), so a price improvement offsets an adverse fill but the total
  never reads as a rebate.
* None below MIN_FILLS measured fills, or when the model total is not positive: the rule then triggers nothing
  (ADR 0031 reading 9: "No measured cost in the window (None) triggers nothing").
* A fill missing any part of its measure (no side, requested price, decision spread, exit leg, a leg without its
  requested price, no model bar) is counted `unmeasured` and reported in the job's row, never guessed: a guessed part
  could only understate or overstate the cost.

Reporting and the controller's input only: nothing here changes a record, a setting, a limit or an order.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns
from goldbot.instruments import GOLD, contract_size, point_of

if TYPE_CHECKING:
    from goldbot.labels.triple_barrier import SwapSpec
    from goldbot.ops.cost_imputation import CostImputer
    from goldbot.ops.gates_phase import ClosedTrade

WINDOW_RISK_DAYS = 20             # "20-day measured cost/model": risk days (UTC weekdays) before the decision day
MIN_FILLS = 20                    # pre-registered: fewer measured fills in the window give None (the rule is inert)
BAR_MINUTES = 15                  # the model's spread: the 15m bar's ask - bid close
BAR_TOLERANCE = pd.Timedelta(hours=1)   # a model bar closed longer ago than this before the entry: unmeasured


class FillCost(FrozenRecord):
    """One demo fill's measured cost and the model's charge for it, USD per oz (adverse positive)."""

    id: str
    exit_ns: int
    spread: float
    slippage: float               # entry + exit, signed (a price improvement is negative)
    commission: float
    swap: float
    model: float

    @property
    def measured(self) -> float:
        return self.spread + self.slippage + self.commission + self.swap


class CostRatio(FrozenRecord):
    """The account's 20-day measured cost over the model's (None: too few fills to measure), with its counts."""

    ratio: float | None
    since: date                   # the first risk day of the window
    fills: int = 0                # measured fills in the window
    unmeasured: int = 0           # fills in the window missing a part of their measure (module docstring)
    measured_usd_per_oz: float | None = None    # the pooled measured cost per fill
    model_usd_per_oz: float | None = None       # the pooled model charge per fill


def window_start(decision_day: date) -> date:
    """The first of the WINDOW_RISK_DAYS UTC weekdays before `decision_day`."""
    d, n = decision_day, 0
    while n < WINDOW_RISK_DAYS:
        d -= timedelta(days=1)
        n += d.weekday() < 5
    return d


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([pd.Timestamp(ts)]))[0])


def _exit_slippage(legs: pd.DataFrame) -> dict[int, float | None]:
    """position id -> lots-weighted side x (requested - filled); None when any leg lacks a finite price or lots."""
    out: dict[int, float | None] = {}
    if legs.empty or not {"position_id", "side", "lots", "requested", "filled"} <= set(legs.columns):
        return out
    for _, g in legs.groupby("position_id"):
        pid = int(g["position_id"].iloc[0])
        req, fil = g["requested"].to_numpy(float), g["filled"].to_numpy(float)
        side, lots = g["side"].to_numpy(float), g["lots"].to_numpy(float)
        if not (np.isfinite(req).all() and np.isfinite(fil).all() and np.isfinite(lots).all()) or lots.sum() <= 0:
            out[pid] = None
            continue
        out[pid] = float((side * (req - fil) * lots).sum() / lots.sum())
    return out


def _model_spreads(entries: list[pd.Timestamp], bars: pd.DataFrame) -> list[float | None]:
    """The ask - bid close of the last 15m bar closed at each entry (as-of on its close time, BAR_TOLERANCE)."""
    from goldbot.data.store import asof_join
    if not entries:
        return []
    if bars.empty or not {"ts_utc", "ask_close", "bid_close"} <= set(bars.columns):
        return [None] * len(entries)
    b = bars[["ts_utc", "ask_close", "bid_close"]].copy()
    b["available_utc"] = pd.to_datetime(b["ts_utc"], utc=True) + pd.Timedelta(minutes=BAR_MINUTES)
    b["bar_spread"] = b["ask_close"] - b["bid_close"]
    left = pd.DataFrame({"ts_utc": pd.to_datetime(entries, utc=True), "k": range(len(entries))})
    joined = asof_join(left, b[["available_utc", "bar_spread"]], tolerance=BAR_TOLERANCE).sort_values("k")
    return [float(x) if math.isfinite(x) else None for x in joined["bar_spread"].to_numpy(float)]


def _gold(t: ClosedTrade) -> bool:
    return t.symbol is None or t.symbol == GOLD


def fill_costs(trades: Iterable[ClosedTrade], account_id: str, *, imputer: CostImputer, legs: pd.DataFrame,
               bars: pd.DataFrame, model_extra_usd: float, model_swap: SwapSpec, start_ns: int, before_ns: int,
               ) -> tuple[list[FillCost], list[str]]:
    """Each of the account's demo gold fills closed in [start_ns, before_ns), measured and modelled (module
    docstring); the ids of the fills that could not be measured."""
    from goldbot.ops.cost_imputation import paid_swap_per_oz
    from goldbot.ops.limits_job import demo_trade_id
    pop = [t for t in trades if t.account_id == account_id and t.mode == "demo" and _gold(t)
           and start_ns <= _ns(t.exit_utc) < before_ns]
    slips = _exit_slippage(legs)
    entries = [t.entry_utc for t in pop if t.entry_utc is not None]
    spreads = iter(_model_spreads(entries, bars))
    point, lot = point_of(GOLD), contract_size(GOLD)
    out: list[FillCost] = []
    unmeasured: list[str] = []
    for t in pop:
        bar = next(spreads) if t.entry_utc is not None else None
        xslip = slips.get(t.position_id) if t.position_id is not None else None
        if (t.side not in (1, -1) or t.entry_utc is None or t.entry_price is None or t.requested_price is None
                or t.decision_spread_points is None or xslip is None or bar is None):
            unmeasured.append(demo_trade_id(t))
            continue
        side = int(t.side)
        terms = imputer.terms_for(t)
        out.append(FillCost(
            id=demo_trade_id(t), exit_ns=_ns(t.exit_utc), spread=float(t.decision_spread_points) * point,
            slippage=side * (t.entry_price - t.requested_price) + xslip,
            commission=2 * max(terms.commission_per_lot_side_usd, 0.0) / lot,
            swap=paid_swap_per_oz(terms.swap, side, t.entry_utc, t.exit_utc),
            model=bar + max(model_extra_usd, 0.0) + paid_swap_per_oz(model_swap, side, t.entry_utc, t.exit_utc)))
    return out, unmeasured


def pooled_ratio(fills: list[FillCost]) -> tuple[float | None, float, float]:
    """(measured / model or None, measured total, model total): slippage pooled signed and floored after."""
    measured = (sum(f.spread for f in fills) + max(0.0, sum(f.slippage for f in fills))
                + sum(f.commission for f in fills) + sum(f.swap for f in fills))
    model = sum(f.model for f in fills)
    if len(fills) < MIN_FILLS or not model > 0:
        return None, measured, model
    return measured / model, measured, model


def cost_ratio_20d(trades: Iterable[ClosedTrade], account_id: str, decision_day: date, *, imputer: CostImputer,
                   legs: pd.DataFrame, bars: pd.DataFrame, model_extra_usd: float, model_swap: SwapSpec) -> CostRatio:
    """The account's measured cost over the model's for the WINDOW_RISK_DAYS risk days before `decision_day`."""
    since = window_start(decision_day)
    fills, unmeasured = fill_costs(trades, account_id, imputer=imputer, legs=legs, bars=bars,
                                   model_extra_usd=model_extra_usd, model_swap=model_swap,
                                   start_ns=_ns(pd.Timestamp(since, tz="UTC")),
                                   before_ns=_ns(pd.Timestamp(decision_day, tz="UTC")))
    ratio, measured, model = pooled_ratio(fills)
    n = len(fills)
    return CostRatio(ratio=ratio, since=since, fills=n, unmeasured=len(unmeasured),
                     measured_usd_per_oz=measured / n if n else None, model_usd_per_oz=model / n if n else None)


__all__ = ["BAR_TOLERANCE", "MIN_FILLS", "WINDOW_RISK_DAYS", "CostRatio", "FillCost", "cost_ratio_20d", "fill_costs",
           "pooled_ratio", "window_start"]

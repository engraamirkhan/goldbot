"""The 20-day measured cost/model series (BACKLOG 90): the input of ADR 0031's frozen cost rule, "20-day measured
cost/model > 1.25 -> floors, 1.10-1.25 -> one step down". The rule's thresholds live in `goldbot.risk.adaptive`
(COST_FLOOR, COST_STEP); this module only measures. It can only tighten: the controller has no loosening use for it.

Definition (pre-registered before any replay; fixed here, every amount USD per oz, adverse positive):

* Population: the account's closed fills on gold (`ClosedTrade.symbol` None or XAUUSD), demo and live (the ratio only
  tightens, so a live account is measured too), that closed in the last WINDOW_RISK_DAYS risk days before the decision day: the UTC weekdays (a weekend is no risk day, ADR 0031 reading e),
  cut off strictly before the decision day's 00:00 UTC.
* Measured, per fill: spread at the fill (`decision_spread_points` x the point: the quote the order was priced from)
  + entry slippage side x (entry_price - requested_price) + exit slippage, the lots-weighted side x (requested -
  filled) of the position's store `exit_fills` legs (a broker stop or target leg is asked at its level, so a stop's
  overshoot is in it) + the record's own commission (`ClosedTrade.commission`) and paid swap (`ClosedTrade.swap`,
  a credit counted as 0), account currency over the engine's USD factor (`account_view.usd_to_account`) and the
  position's ounces; a record without one, or an account without a known factor, takes that part at the account's
  measured terms (its nightly cost table, else its broker's settings prior: `CostImputer.terms_for`).
* Model, per fill: what the research labels charge for the same trade (labels/triple_barrier.py: in at the ask, out at
  the bid, plus the extra cost and the swap): the ask - bid close of the last 15m bar closed at the entry (an as-of
  join on the bar's close time, at most BAR_TOLERANCE old, on the labels' own bars: store `bars_15m` source
  dukascopy) + the extra cost and paid swap the strategies were QUALIFIED under, MODEL_SOURCE = the settings priors
  (`settings_extra_cost_usd`: costs.slippage_prior_usd both sides + the canonical broker's commission;
  `settings_swap`: costs.swap_*). Never the live cost table: it already holds the measured slippage and commission, so
  it would put the measure on both sides and pull the ratio toward 1 (quant review of 9903fb2d). A swap credit counts
  on neither side.
* Ratio: pooled, sum(spread) + max(0, sum(slippage)) + sum(commission) + sum(swap) over sum(model). Slippage is pooled
  signed and floored after (as the cost of record does), so a price improvement offsets an adverse fill but the total
  never reads as a rebate.
* None below MIN_FILLS measured fills, when the model total is not positive, or when more than MAX_UNMEASURED_SHARE
  of the window's fills are unmeasured (the missing ones are most likely stop exits without their leg, the costly
  ones, so the rest would read low; the job raises a dq alert): the rule then triggers nothing (ADR 0031 reading 9:
  "No measured cost in the window (None) triggers nothing"). `CostRatio.reason` says which.
* A fill entered after the last label bar's close (the dukascopy bars are refreshed weekly, so up to about 8 days
  lag) is `pending`, out of the window's population until the refresh models it: a time cut, not an outcome one, so it
  biases nothing and does not count as unmeasured. Bounded: when the last label bar closed more than MAX_BAR_LAG_DAYS
  before the decision day (a stalled refresh), nothing is pending, those fills count as unmeasured and `bars_stale`
  makes the job raise a `cost_bars_stale` alert (quant re-check of 76066de6).
* Conservative bound (quant review of R150, a non-blocking suggestion; reporting only, never an input to the limits):
  `ratio_bound` prices each unmeasured fill at the window's worst measured fill (its largest measured cost) against the
  window's smallest model charge, pooled with the measured fills as the ratio is. It reads how high the ratio could be
  if the unmeasured fills were as costly as the worst one seen; it equals the ratio when nothing is unmeasured and is
  None without a measured fill or a positive model total. The job logs it and keeps it in its row; the controller only
  ever reads `ratio`.
* A fill missing any part of its measure, or with a non-finite one (no side, requested price, decision spread, exit
  leg, a leg without its requested price, no model bar, a NaN or infinite price) is counted `unmeasured` and reported in the job's row, never guessed: a guessed part
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
MAX_UNMEASURED_SHARE = 0.20       # pre-registered: a larger share of unmeasured fills gives None and a dq alert
MODEL_SOURCE = "settings priors"  # the cost the strategies were qualified under (module docstring)
BAR_MINUTES = 15                  # the model's spread: the 15m bar's ask - bid close
MAX_BAR_LAG_DAYS = 9              # = limits_job.ATR_MAX_LAG_DAYS: the weekly refresh plus slack; older bars are stale
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
    pending: int = 0              # fills entered after the last label bar: modelled after the weekly bar refresh
    bars_stale: bool = False      # no label bar, or the last older than MAX_BAR_LAG_DAYS: nothing pending, alerted
    measured_usd_per_oz: float | None = None    # the pooled measured cost per fill
    model_usd_per_oz: float | None = None       # the pooled model charge per fill
    model: str = MODEL_SOURCE
    reason: str | None = None     # why None: too_few_fills, unmeasured_share, model_cost_zero
    ratio_bound: float | None = None    # reporting only: unmeasured fills at the worst fill (module docstring)


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


def _finite(*xs: float | None) -> bool:
    return all(x is not None and math.isfinite(x) for x in xs)


def _record_usd_per_oz(amount: float | None, lots: float | None, acct_per_usd: float | None) -> float | None:
    """An account-currency amount of the record in USD per oz of the position; None when it cannot be converted."""
    if not _finite(amount, lots, acct_per_usd) or not (lots and lots > 0 and acct_per_usd and acct_per_usd > 0):
        return None
    assert amount is not None
    return amount / acct_per_usd / (lots * contract_size(GOLD))


def fill_costs(trades: Iterable[ClosedTrade], account_id: str, *, imputer: CostImputer, legs: pd.DataFrame,
               bars: pd.DataFrame, model_extra_usd: float, model_swap: SwapSpec, start_ns: int, before_ns: int,
               acct_per_usd: float | None) -> tuple[list[FillCost], list[str]]:
    """Each of the account's gold fills (demo and live) closed in [start_ns, before_ns), measured and modelled
    (module docstring); the ids of the fills that could not be measured. `acct_per_usd`: the account currency per USD
    (`account_view.usd_to_account`), None when unknown."""
    from goldbot.ops.cost_imputation import paid_swap_per_oz
    from goldbot.ops.limits_job import demo_trade_id
    pop = [t for t in trades if t.account_id == account_id and _gold(t)
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
                or not _finite(t.decision_spread_points, t.entry_price, t.requested_price, xslip, bar)):
            unmeasured.append(demo_trade_id(t))
            continue
        assert t.side is not None and t.entry_price is not None and t.requested_price is not None
        assert t.decision_spread_points is not None and xslip is not None and bar is not None
        side = int(t.side)
        terms = imputer.terms_for(t)
        comm = _record_usd_per_oz(abs(t.commission) if t.commission is not None else None, t.lots, acct_per_usd)
        paid = _record_usd_per_oz(max(0.0, -t.swap) if t.swap is not None else None, t.lots, acct_per_usd)
        out.append(FillCost(
            id=demo_trade_id(t), exit_ns=_ns(t.exit_utc), spread=float(t.decision_spread_points) * point,
            slippage=side * (t.entry_price - t.requested_price) + xslip,
            commission=comm if comm is not None else 2 * max(terms.commission_per_lot_side_usd, 0.0) / lot,
            swap=paid if paid is not None else paid_swap_per_oz(terms.swap, side, t.entry_utc, t.exit_utc),
            model=bar + max(model_extra_usd, 0.0) + paid_swap_per_oz(model_swap, side, t.entry_utc, t.exit_utc)))
    return out, unmeasured


def _modelled_yet(trades: Iterable[ClosedTrade], bars: pd.DataFrame, account_id: str, start_ns: int,
                  before_ns: int) -> tuple[list[ClosedTrade], int, bool]:
    """(the trades entered before the last label bar closed, how many of the rest entered after it, stale). Without
    bars, or with the last one closed more than MAX_BAR_LAG_DAYS before `before_ns`, nothing is pending: every later
    fill lacks its model bar and is unmeasured (a data failure, alerted)."""
    rows = list(trades)
    if bars.empty or "ts_utc" not in bars.columns:
        return rows, 0, True
    last = pd.to_datetime(bars["ts_utc"], utc=True).max() + pd.Timedelta(minutes=BAR_MINUTES)
    if _ns(last) < before_ns - MAX_BAR_LAG_DAYS * 86_400 * 10**9:
        return rows, 0, True
    keep = [t for t in rows if t.entry_utc is None or pd.Timestamp(t.entry_utc) <= last]
    later = [t for t in rows if t.entry_utc is not None and pd.Timestamp(t.entry_utc) > last]
    n = sum(t.account_id == account_id and _gold(t) and start_ns <= _ns(t.exit_utc) < before_ns for t in later)
    return keep, n, False


def pooled_ratio(fills: list[FillCost], unmeasured: int = 0) -> tuple[float | None, float, float, str | None]:
    """(measured / model or None, measured total, model total, why None): slippage pooled signed, floored after."""
    measured = (sum(f.spread for f in fills) + max(0.0, sum(f.slippage for f in fills))
                + sum(f.commission for f in fills) + sum(f.swap for f in fills))
    model = sum(f.model for f in fills)
    total = len(fills) + unmeasured
    why = ("too_few_fills" if len(fills) < MIN_FILLS
           else "unmeasured_share" if unmeasured > MAX_UNMEASURED_SHARE * total
           else "model_cost_zero" if not (math.isfinite(model) and model > 0) else None)
    if why is not None or not math.isfinite(measured):
        return None, measured, model, why or "not_finite"
    return measured / model, measured, model, None


def conservative_bound(fills: list[FillCost], unmeasured: int) -> float | None:
    """Reporting only, never a limit input: the pooled ratio with each of the `unmeasured` fills priced at the window's
    worst measured fill and charged the smallest model of the window (module docstring); None without a measured fill
    or a positive, finite total."""
    if not fills:
        return None
    _, measured, model, _ = pooled_ratio(fills)
    measured += unmeasured * max(0.0, max(f.measured for f in fills))
    model += unmeasured * min(f.model for f in fills)
    if not (math.isfinite(measured) and math.isfinite(model) and model > 0):
        return None
    return measured / model


def cost_ratio_20d(trades: Iterable[ClosedTrade], account_id: str, decision_day: date, *, imputer: CostImputer,
                   legs: pd.DataFrame, bars: pd.DataFrame, model_extra_usd: float, model_swap: SwapSpec,
                   acct_per_usd: float | None) -> CostRatio:
    """The account's measured cost over the model's for the WINDOW_RISK_DAYS risk days before `decision_day`."""
    since = window_start(decision_day)
    lo, hi = _ns(pd.Timestamp(since, tz="UTC")), _ns(pd.Timestamp(decision_day, tz="UTC"))
    trades, pending, stale = _modelled_yet(trades, bars, account_id, lo, hi)
    fills, unmeasured = fill_costs(trades, account_id, imputer=imputer, legs=legs, bars=bars,
                                   model_extra_usd=model_extra_usd, model_swap=model_swap, start_ns=lo, before_ns=hi,
                                   acct_per_usd=acct_per_usd)
    ratio, measured, model, why = pooled_ratio(fills, len(unmeasured))
    n = len(fills)
    return CostRatio(ratio=ratio, since=since, fills=n, unmeasured=len(unmeasured), pending=pending,
                     bars_stale=stale,
                     measured_usd_per_oz=measured / n if n else None, model_usd_per_oz=model / n if n else None,
                     reason=why, ratio_bound=conservative_bound(fills, len(unmeasured)))


__all__ = ["BAR_TOLERANCE", "MAX_BAR_LAG_DAYS", "MAX_UNMEASURED_SHARE", "MIN_FILLS", "MODEL_SOURCE", "WINDOW_RISK_DAYS", "CostRatio", "FillCost", "conservative_bound", "cost_ratio_20d", "fill_costs",
           "pooled_ratio", "window_start"]

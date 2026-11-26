"""Trades re-sized as if traded on real money under the ADR 0106 limits (decision 2).

ADR 0106 decision 2: "Every promotion read (challenger vs live, paper -> real money) is judged on the trades as if
sized under the real-money limits above, and a model that would breach them is not promoted." The limits: "risk per
trade <= 1 % of equity, daily loss limit 2 % of equity then stop for the day, max drawdown halt at 15 %, and order size
<= about 1 % of the asset's daily volume."

The replay, as coded here (one as-if USD account holding every trade given, events in time order):

* Equity is the closed balance: a trade is sized on the balance at its entry, after every exit before it.
* Size: the largest lot-step multiple whose loss at the stop, plus the round-trip spread (the decision quote's spread;
  the instrument's maximum spread when unknown, so sizing errs small), is at most RM_MAX_RISK_PER_TRADE of equity. No
  minimum-lot tolerance: a trade that needs more than the limit at the minimum lot is `below_min_lot`, never rounded up.
* Volume: the order's notional is cut to RM_MAX_ADV_SHARE of the instrument's reference daily volume (USD); below the
  minimum lot after the cut it is `over_adv`; an unknown volume refuses the trade (`adv_unknown`), as the RiskGate does
  on real money (fail closed).
* P&L: the trade's realised net R (`r`, P&L over the initial risk from price, costs inside) on the re-sized lots: r x
  lots x stop distance x contract. A non-USD profit currency has no conversion here: `no_fx`, excluded.
* Daily stop: once the risk day's (session day, [D-1 22:00, D 22:00) UTC) realised loss reaches RM_DAILY_STOP of the
  day-start balance, later entries that day are `daily_stopped`. Trades already open still close at their result, so
  a day that ends more than RM_DAILY_STOP down is a `daily_stop` breach. The replay mirrors the RiskGate's latch and
  does not cap a later entry to the day's remaining budget: a model whose third loser of a day takes the day past 2 %
  breaches here exactly as it would on real money.
* Drawdown: the closed balance falling RM_DD_HALT below its high-water mark is a `drawdown_halt` breach; every later
  entry is `halted`.
* A realised loss beyond RM_MAX_RISK_PER_TRADE (a gap or slippage past the stop) is counted in `overshoots` and shown;
  the per-trade limit is ex ante, so it is not a breach by itself (its days and drawdown are).
* Power: only `sized` trades count (`n_sized`, `n_sized_days`): excluded trades never inflate n.

The constants are the owner's numbers and equal goldbot/risk/real_money.py's (ADR 0106 lane 1;
tests/test_as_if_sizing.py pins them equal once that module exists). This module never sizes or places an order.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Final, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import opportunity_day
from goldbot.instruments import GOLD, spec_of

RM_MAX_RISK_PER_TRADE: Final = 0.01      # "risk per trade <= 1 % of equity"
RM_DAILY_STOP: Final = 0.02              # "daily loss limit 2 % of equity then stop for the day"
RM_DD_HALT: Final = 0.15                 # "max drawdown halt at 15 %"
RM_MAX_ADV_SHARE: Final = 0.01           # "order size <= about 1 % of the asset's daily volume"
EPS: Final = 1e-12

AsIfStatus = Literal["sized", "below_min_lot", "no_stop", "no_r", "no_fx", "unknown_instrument", "adv_unknown",
                     "over_adv", "daily_stopped", "halted"]
Breach = Literal["daily_stop", "drawdown_halt"]


class AsIfInput(FrozenRecord):
    """What the replay needs of one closed trade (paper ClosedTrade or a shadow trade, mapped by the caller)."""
    entry_utc: UtcTimestamp
    exit_utc: UtcTimestamp
    side: int | None = None
    entry_price: float = Field(gt=0)
    initial_sl: float | None = None
    r: float | None = None                   # realised net R: P&L over the initial risk from price, costs inside
    symbol: str = GOLD
    spread_points: float | None = None       # the decision quote's spread, in the instrument's points


class AsIfTrade(FrozenRecord):
    index: int                               # position in the input
    status: AsIfStatus
    day: date                                # session day of the exit
    lots: float = 0.0
    risk_frac: float = 0.0                   # loss at the stop incl. the spread, of the balance at entry
    pnl_money: float = 0.0                   # USD
    pnl_frac: float = 0.0                    # of the balance at entry


class AsIfReport(FrozenRecord):
    equity_start: float
    equity_end: float
    n_trades: int
    n_sized: int                             # the power count: sized trades only
    n_sized_days: int                        # distinct session days of the sized trades' entries
    below_min_lot: int
    excluded: dict[str, int]                 # status -> count, every status but "sized"
    breaches: list[Breach]
    worst_day_loss: float                    # largest realised day loss, of the day-start balance
    max_drawdown: float                      # of the closed-balance high-water mark
    worst_trade_loss: float                  # of the balance at entry
    overshoots: int                          # trades that lost more than RM_MAX_RISK_PER_TRADE
    daily: dict[date, float]                 # session day of the exit -> realised P&L over the day-start balance
    trades: list[AsIfTrade]

    @property
    def clean(self) -> bool:
        return not self.breaches

    def text(self) -> str:
        ex = ", ".join(f"{k} {v}" for k, v in sorted(self.excluded.items())) or "none"
        return (f"{self.n_sized} of {self.n_trades} trades sized at <= {RM_MAX_RISK_PER_TRADE:.0%} on "
                f"${self.equity_start:,.0f} (excluded: {ex}); worst day {-self.worst_day_loss:+.2%}, max drawdown "
                f"{self.max_drawdown:.2%}, {self.overshoots} loss(es) past 1%; breaches: "
                f"{', '.join(self.breaches) or 'none'}")


def _day(ts: pd.Timestamp) -> date:
    return opportunity_day(pd.DatetimeIndex([ts]))[0].date()


def _size(t: AsIfInput, equity: float, adv_usd: Mapping[str, float]) -> tuple[AsIfStatus, float, float, float]:
    """(status, lots, risk_frac, stop distance) of one entry on `equity` (module docstring)."""
    if t.initial_sl is None or not abs(t.entry_price - t.initial_sl) > 0:
        return "no_stop", 0.0, 0.0, 0.0
    if t.r is None or not math.isfinite(t.r):
        return "no_r", 0.0, 0.0, 0.0
    try:
        spec = spec_of(t.symbol)
    except KeyError:
        return "unknown_instrument", 0.0, 0.0, 0.0
    if spec.currency_profit != "USD":
        return "no_fx", 0.0, 0.0, 0.0
    stop = abs(t.entry_price - t.initial_sl)
    spread_pts = t.spread_points if t.spread_points is not None and t.spread_points >= 0 else spec.max_spread_points
    per_lot = (stop + spread_pts * spec.point) * spec.contract_size
    lots = math.floor(RM_MAX_RISK_PER_TRADE * equity / per_lot / spec.lot_step + 1e-9) * spec.lot_step
    if lots < spec.min_lot - EPS:
        return "below_min_lot", 0.0, 0.0, stop
    adv = adv_usd.get(t.symbol)
    if adv is None or not adv > 0:
        return "adv_unknown", 0.0, 0.0, stop
    cap = math.floor(RM_MAX_ADV_SHARE * adv / (spec.contract_size * t.entry_price) / spec.lot_step + 1e-9) * spec.lot_step
    lots = min(lots, cap)
    if lots < spec.min_lot - EPS:
        return "over_adv", 0.0, 0.0, stop
    return "sized", lots, lots * per_lot / equity, stop


def as_if_real_money(trades: Sequence[AsIfInput], *, equity: float, adv_usd: Mapping[str, float]) -> AsIfReport:
    """Replay `trades` on one as-if real-money account of `equity` USD under the ADR 0106 limits (module docstring).
    `adv_usd`: reference average daily volume per instrument, USD notional (a missing instrument refuses its trades)."""
    if not equity > 0:
        raise ValueError(f"as-if equity must be > 0, got {equity}")
    events: list[tuple[pd.Timestamp, int, int]] = []          # (time, 0 exit before 1 entry at the same instant, index)
    for i, t in enumerate(trades):
        events += [(pd.Timestamp(t.entry_utc), 1, i), (pd.Timestamp(t.exit_utc), 0, i)]
    events.sort()
    bal = hwm = equity
    day_start: dict[date, float] = {}
    day_pnl: dict[date, float] = {}
    sized: dict[int, tuple[float, float, float, float]] = {}  # index -> (lots, risk_frac, stop, balance at entry)
    status: dict[int, AsIfStatus] = {}
    breaches: list[Breach] = []
    halted = False
    max_dd = worst_trade = 0.0
    out: dict[int, AsIfTrade] = {}
    for ts, kind, i in events:
        t, d = trades[i], _day(ts)
        day_start.setdefault(d, bal)
        if kind == 1:
            if halted:
                status[i] = "halted"
            elif -day_pnl.get(d, 0.0) >= RM_DAILY_STOP * day_start[d] - EPS:
                status[i] = "daily_stopped"
            else:
                st, lots, risk, stop = _size(t, bal, adv_usd)
                status[i] = st
                if st == "sized":
                    sized[i] = (lots, risk, stop, bal)
            continue
        exit_day = d
        if i not in sized:
            out[i] = AsIfTrade(index=i, status=status[i], day=exit_day)
            continue
        lots, risk, stop, at_entry = sized[i]
        money = float(t.r or 0.0) * lots * stop * spec_of(t.symbol).contract_size
        bal += money
        day_pnl[d] = day_pnl.get(d, 0.0) + money
        hwm = max(hwm, bal)
        dd = 1.0 - bal / hwm
        max_dd = max(max_dd, dd)
        worst_trade = max(worst_trade, -money / at_entry)
        if dd >= RM_DD_HALT - EPS and not halted:
            halted = True
            breaches.append("drawdown_halt")
        out[i] = AsIfTrade(index=i, status="sized", day=exit_day, lots=round(lots, 8), risk_frac=risk,
                           pnl_money=money, pnl_frac=money / at_entry)
    daily = {d: p / day_start[d] for d, p in sorted(day_pnl.items())}
    worst_day = max((-v for v in daily.values()), default=0.0)
    if worst_day > RM_DAILY_STOP + EPS:
        breaches.insert(0, "daily_stop")
    order = sorted(out.values(), key=lambda a: (pd.Timestamp(trades[a.index].entry_utc), a.index))
    counts = Counter(a.status for a in order if a.status != "sized")
    entry_days = {_day(pd.Timestamp(trades[a.index].entry_utc)) for a in order if a.status == "sized"}
    return AsIfReport(equity_start=equity, equity_end=bal, n_trades=len(trades), n_sized=len(sized),
                      n_sized_days=len(entry_days), below_min_lot=counts.get("below_min_lot", 0),
                      excluded=dict(sorted(counts.items())), breaches=breaches, worst_day_loss=max(worst_day, 0.0),
                      max_drawdown=max_dd, worst_trade_loss=worst_trade,
                      overshoots=sum(1 for a in order if a.status == "sized" and -a.pnl_frac > RM_MAX_RISK_PER_TRADE + EPS),
                      daily=daily, trades=order)

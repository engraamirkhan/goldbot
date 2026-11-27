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
* A record whose exit is before its entry is `bad_times` (excluded, never replayed); a zero-length trade is entered
  before it is closed.
* Stricter of ADR 0106 and the settings (trading-safety review of #490, F1): `AsIfLimits.stricter(settings.risk)`
  replays at min(1 %, risk_per_trade) a trade, min(2 %, daily_cap) a day, a `weekly_cap` latch and breach (a
  realised week loss above it), and a drawdown breach at min(15 %, drawdown_stage2): the rails that bind today, so a
  13 % drawdown or a 6 % week never reads clean. `AsIfLimits()` alone is the ADR 0106 numbers.
* Power: only `sized` trades count (`n_sized`, `n_sized_days`): excluded trades never inflate n.
* Start: with `start`, the account opens then and a trade entered before it is not replayed (`before_start`), so two
  books replayed from the same start, equity, constants and cost model run "each as it would alone" (quant ruling on
  #490, P3). Each row keeps its `scoring_sha` (the scoring model's sha, ADR 0085 N18); `shas_by_day` logs them per
  session day of entry.

The constants are the owner's numbers and equal goldbot/risk/real_money.py's (ADR 0106 lane 1;
tests/test_as_if_sizing.py pins them equal once that module exists). This module never sizes or places an order.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Final, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import RiskSettings
from goldbot.data.timeutil import opportunity_day
from goldbot.instruments import GOLD, spec_of

RM_MAX_RISK_PER_TRADE: Final = 0.01      # "risk per trade <= 1 % of equity"
RM_DAILY_STOP: Final = 0.02              # "daily loss limit 2 % of equity then stop for the day"
RM_DD_HALT: Final = 0.15                 # "max drawdown halt at 15 %"
RM_MAX_ADV_SHARE: Final = 0.01           # "order size <= about 1 % of the asset's daily volume"
EPS: Final = 1e-12

class AsIfLimits(FrozenRecord):
    """The limits the replay applies; the default is ADR 0106's, `stricter` takes the minimum with the settings."""
    risk_per_trade: float = Field(RM_MAX_RISK_PER_TRADE, gt=0, le=RM_MAX_RISK_PER_TRADE)
    daily_stop: float = Field(RM_DAILY_STOP, gt=0, le=RM_DAILY_STOP)
    dd_halt: float = Field(RM_DD_HALT, gt=0, le=RM_DD_HALT)
    weekly_cap: float | None = Field(None, gt=0, lt=1)     # ADR 0106 sets none; the settings' weekly cap

    def text(self) -> str:
        weekly = f"{self.weekly_cap:.0%} weekly cap, " if self.weekly_cap is not None else ""
        return (f"{self.risk_per_trade:.2%}/trade, {self.daily_stop:.0%} daily stop, {weekly}{self.dd_halt:.0%} "
                f"drawdown, {RM_MAX_ADV_SHARE:.0%} of daily volume")

    def at_tiny_live(self, risk: RiskSettings) -> AsIfLimits:
        """The same limits at the tiny-live per-trade rate (trading-safety ruling on #490: reported, never decides)."""
        return self.model_copy(update={"risk_per_trade": min(self.risk_per_trade, risk.risk_per_trade_tiny_live)})

    @classmethod
    def stricter(cls, risk: RiskSettings) -> AsIfLimits:
        return cls(risk_per_trade=min(RM_MAX_RISK_PER_TRADE, risk.risk_per_trade),
                   daily_stop=min(RM_DAILY_STOP, risk.daily_cap), dd_halt=min(RM_DD_HALT, risk.drawdown_stage2),
                   weekly_cap=risk.weekly_cap)


AsIfStatus = Literal["sized", "below_min_lot", "no_stop", "no_r", "no_fx", "unknown_instrument", "adv_unknown",
                     "over_adv", "daily_stopped", "halted", "before_start", "bad_times", "weekly_stopped"]
Breach = Literal["daily_stop", "weekly_cap", "drawdown_halt"]


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
    scoring_sha: str | None = None           # the scoring model's sha (ADR 0085 N18); required by a paired read


class AsIfTrade(FrozenRecord):
    index: int                               # position in the input
    status: AsIfStatus
    day: date                                # session day of the exit
    entry_day: date                          # session day of the entry
    lots: float = 0.0
    notional_usd: float = 0.0                # lots x contract x entry price (0 when not sized)
    risk_frac: float = 0.0                   # loss at the stop incl. the spread, of the balance at entry
    pnl_money: float = 0.0                   # USD
    pnl_frac: float = 0.0                    # of the balance at entry
    scoring_sha: str | None = None


class AsIfReport(FrozenRecord):
    equity_start: float
    start_utc: UtcTimestamp | None = None    # the account's opening; None: from the first trade
    equity_end: float
    n_trades: int
    n_sized: int                             # the power count: sized trades only
    n_sized_days: int                        # distinct session days of the sized trades' entries
    below_min_lot: int
    excluded: dict[str, int]                 # status -> count, every status but "sized"
    breaches: list[Breach]
    limits: AsIfLimits = Field(default_factory=AsIfLimits)
    worst_day_loss: float                    # largest realised day loss, of the day-start balance
    worst_week_loss: float = 0.0             # largest realised week loss, of the week-start balance
    max_drawdown: float                      # of the closed-balance high-water mark
    worst_trade_loss: float                  # of the balance at entry
    overshoots: int                          # trades that lost more than RM_MAX_RISK_PER_TRADE
    daily: dict[date, float]                 # session day of the exit -> realised P&L over the day-start balance
    trades: list[AsIfTrade]
    shas_by_day: dict[date, list[str]] = Field(default_factory=dict)   # session day of entry -> scoring shas sized

    @property
    def per_trade(self) -> float:
        """The per-trade risk fraction the replay sized at."""
        return self.limits.risk_per_trade

    @property
    def clean(self) -> bool:
        return not self.breaches

    def text(self) -> str:
        ex = ", ".join(f"{k} {v}" for k, v in sorted(self.excluded.items())) or "none"
        lim = self.limits
        return (f"{self.n_sized} of {self.n_trades} trades sized at <= {lim.risk_per_trade:.2%} on "
                f"${self.equity_start:,.0f} (excluded: {ex}); worst day {-self.worst_day_loss:+.2%} (stop "
                f"{lim.daily_stop:.0%}), worst week {-self.worst_week_loss:+.2%}"
                + (f" (cap {lim.weekly_cap:.0%})" if lim.weekly_cap is not None else "")
                + f", max drawdown {self.max_drawdown:.2%} (halt {lim.dd_halt:.0%}), {self.overshoots} loss(es) past "
                f"the per-trade risk; breaches: "
                f"{', '.join(self.breaches) or 'none'}")


def _day(ts: pd.Timestamp) -> date:
    return opportunity_day(pd.DatetimeIndex([ts]))[0].date()


def _week(d: date) -> date:
    """The risk week of a session day: its Monday."""
    return d - timedelta(days=d.weekday())


def _size(t: AsIfInput, equity: float, adv_usd: Mapping[str, float],
          risk_frac: float = RM_MAX_RISK_PER_TRADE) -> tuple[AsIfStatus, float, float, float]:
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
    lots = math.floor(risk_frac * equity / per_lot / spec.lot_step + 1e-9) * spec.lot_step
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


def as_if_real_money(trades: Sequence[AsIfInput], *, equity: float, adv_usd: Mapping[str, float],
                     start: pd.Timestamp | None = None, limits: AsIfLimits | None = None) -> AsIfReport:
    """Replay `trades` on one as-if real-money account of `equity` USD under the ADR 0106 limits (module docstring).
    `adv_usd`: reference average daily volume per instrument, USD notional (a missing instrument refuses its trades)."""
    if not equity > 0:
        raise ValueError(f"as-if equity must be > 0, got {equity}")
    lim = limits if limits is not None else AsIfLimits()
    events: list[tuple[pd.Timestamp, int, int]] = []          # (time, 0 exit before 1 entry at the same instant, index)
    early: list[int] = []
    bad: list[int] = []
    for i, t in enumerate(trades):
        t_in, t_out = pd.Timestamp(t.entry_utc), pd.Timestamp(t.exit_utc)
        if t_out < t_in:                         # a clock-skewed record: never replayed, never a crash
            bad.append(i)
            continue
        if start is not None and t_in < pd.Timestamp(start):
            early.append(i)
            continue
        # an exit sorts before another trade's entry at the same instant, but never before its own entry
        events += [(t_in, 1, i), (t_out, 2 if t_out == t_in else 0, i)]
    events.sort()
    bal = hwm = equity
    day_start: dict[date, float] = {}
    day_pnl: dict[date, float] = {}
    week_start: dict[date, float] = {}
    week_pnl: dict[date, float] = {}
    sized: dict[int, tuple[float, float, float, float]] = {}  # index -> (lots, risk_frac, stop, balance at entry)
    status: dict[int, AsIfStatus] = {}
    breaches: list[Breach] = []
    halted = False
    max_dd = worst_trade = 0.0
    skipped: tuple[tuple[AsIfStatus, list[int]], ...] = (("before_start", early), ("bad_times", bad))
    out: dict[int, AsIfTrade] = {i: AsIfTrade(index=i, status=st, day=_day(pd.Timestamp(trades[i].exit_utc)),
                                              entry_day=_day(pd.Timestamp(trades[i].entry_utc)),
                                              scoring_sha=trades[i].scoring_sha)
                                 for st, idx in skipped for i in idx}
    for ts, kind, i in events:
        t, d = trades[i], _day(ts)
        wk = _week(d)
        day_start.setdefault(d, bal)
        week_start.setdefault(wk, bal)
        if kind == 1:
            if halted:
                status[i] = "halted"
            elif -day_pnl.get(d, 0.0) >= lim.daily_stop * day_start[d] - EPS:
                status[i] = "daily_stopped"
            elif lim.weekly_cap is not None and -week_pnl.get(wk, 0.0) >= lim.weekly_cap * week_start[wk] - EPS:
                status[i] = "weekly_stopped"
            else:
                st, lots, risk, stop = _size(t, bal, adv_usd, lim.risk_per_trade)
                status[i] = st
                if st == "sized":
                    sized[i] = (lots, risk, stop, bal)
            continue
        exit_day, entry_day = d, _day(pd.Timestamp(t.entry_utc))
        if i not in sized:
            out[i] = AsIfTrade(index=i, status=status[i], day=exit_day, entry_day=entry_day, scoring_sha=t.scoring_sha)
            continue
        lots, risk, stop, at_entry = sized[i]
        money = float(t.r or 0.0) * lots * stop * spec_of(t.symbol).contract_size
        bal += money
        day_pnl[d] = day_pnl.get(d, 0.0) + money
        week_pnl[wk] = week_pnl.get(wk, 0.0) + money
        hwm = max(hwm, bal)
        dd = 1.0 - bal / hwm
        max_dd = max(max_dd, dd)
        worst_trade = max(worst_trade, -money / at_entry)
        if dd >= lim.dd_halt - EPS and not halted:
            halted = True
            breaches.append("drawdown_halt")
        out[i] = AsIfTrade(index=i, status="sized", day=exit_day, entry_day=entry_day, lots=round(lots, 8),
                           notional_usd=lots * spec_of(t.symbol).contract_size * t.entry_price, risk_frac=risk,
                           pnl_money=money, pnl_frac=money / at_entry, scoring_sha=t.scoring_sha)
    daily = {d: p / day_start[d] for d, p in sorted(day_pnl.items())}
    worst_day = max((-v for v in daily.values()), default=0.0)
    worst_week = max((-p / week_start[w] for w, p in week_pnl.items()), default=0.0)
    if lim.weekly_cap is not None and worst_week > lim.weekly_cap + EPS:
        breaches.insert(0, "weekly_cap")
    if worst_day > lim.daily_stop + EPS:
        breaches.insert(0, "daily_stop")
    order = sorted(out.values(), key=lambda a: (pd.Timestamp(trades[a.index].entry_utc), a.index))
    counts = Counter(a.status for a in order if a.status != "sized")
    entry_days = {a.entry_day for a in order if a.status == "sized"}
    shas: dict[date, set[str]] = {}
    for a in order:
        if a.status == "sized" and a.scoring_sha:
            shas.setdefault(a.entry_day, set()).add(a.scoring_sha)
    return AsIfReport(equity_start=equity, start_utc=start, equity_end=bal, n_trades=len(trades), n_sized=len(sized),
                      n_sized_days=len(entry_days), below_min_lot=counts.get("below_min_lot", 0),
                      excluded=dict(sorted(counts.items())), breaches=breaches, worst_day_loss=max(worst_day, 0.0),
                      max_drawdown=max_dd, worst_trade_loss=worst_trade,
                      overshoots=sum(1 for a in order if a.status == "sized" and -a.pnl_frac > lim.risk_per_trade + EPS),
                      limits=lim, worst_week_loss=max(worst_week, 0.0),
                      daily=daily, trades=order, shas_by_day={d: sorted(v) for d, v in sorted(shas.items())})

"""The family-D simulator: realistic costs in R and the sizing scaffold size-v1 (ADR 0106 3: "RL (D) does sizing and
execution only"; specification: "Reinforcement learning (PPO, SAC, TD3). Role: position sizing and order execution,
trained in a simulator with realistic costs. Not the primary signal source"; "Size positions as a percentage of
equity, scaled by volatility"; "Risk per trade 1% of equity or less"; "Daily loss limit 2% of equity, then stop for
the day"; max drawdown halt at 15 %).

Costs (`SimCosts`), per unit of initial risk (k = 1e-4 x entry / risk, R per bp of price, as decision_cost):
- every exit pays the round trip `rt_bp` (spread + commission; W2 cost of record = decision_cost.RT_BP) plus the ub95
  slippage `slippage_ub95_bp` (0 in `w2()`: the W2 point value has none; a measured ub95 per broker x session replaces
  it, ADR 0102 3), and a stop exit also the overshoot `overshoot_bp` (decision_cost.OVERSHOOT_BP);
- swaps per rollover night at the research swap spec's rate on notional, absolute (a received swap is never credited,
  as decision_cost.cost_x).
History reads charge the D-3 cost of record instead of the constant: a label row charged by `srv3_cost_record.apply`
(cost_version "srv3-cost-...") carries its own round trip `cost_rt_bp` (gold: `gold_row_bp`, the ub95 ECR and era
spread with slippage inside), and `exit_cost_r(rt_bp=)` charges it in place of `rt_bp`; rl_exits refuses a separate
`slippage_ub95_bp` on such rows (the record already carries it). Such a row also carries its swap of record
`swap_rate` (sr_v3_train.CostTable.swap_rate: |swap per night| as a fraction of price for its instrument, month and
side), charged via `swap_r(rate=)` in place of the research spec's rate, so the RL swap per night equals the trainer's
`cost_parts` swap per night on the same row (tested). The overshoot stays decision_cost.OVERSHOOT_BP on stops and the
rollover nights stay decision_cost's swap calendar, both as `cost_parts`.

Sizing (`SizingSpec`, `risk_fraction`): risk fraction = min(max_risk, base_risk / vol_ratio), vol_ratio = the ATR at
the decision over its trailing median (`vol_ratio`, past values only). max_risk is the 1 % cap of ADR 0106 2.
`simulate` runs a trade list through it with the live limits as plain code (settings risk, never looser: field
validators): a trade is skipped when its reserve, (1 R + its cost + `gap_buffer_r`) x risk, could take the day past
2 % of the day's opening equity or the ISO week past 5 % (survival first: a full stop, gapped, never crosses the
limit; a day that loses more anyway is counted in `n_daily_breaches`); from an 8 % drawdown risk per trade is halved
until it is back under 5 %; all trading halts at 12 %. `compare` reports size-v1
against the fixed fraction base_risk on the same trades (descriptive). PPO/SAC/TD3 are allowed inside this simulator
by ADR 0106; none is fitted here: SYNTH-ml R7(d) declines them this quarter until RL-X1 passes, and the scaffold's
closed-form policy is the baseline any learned sizer must beat. Research only: nothing here reaches the engine, the
RiskGate or an order.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.research import decision_cost as DC

# The live limits (config/settings.yaml risk, goldbot.risk.gate.RiskLimits; ADR 0004), mirrored here because a history
# model never imports the order path; a test keeps them equal. They are tighter than ADR 0106 2's outer bounds (15 %
# halt) and win: no SizingSpec may exceed them (trading-safety M1 on #489).
DAILY_LOSS_STOP = 0.02      # risk.daily_cap (ADR 0106 2: "daily loss limit 2 % of equity, then stop for the day")
WEEKLY_CAP = 0.05           # risk.weekly_cap
DRAWDOWN_STAGE1 = 0.08      # risk.drawdown_stage1: risk per trade halved (goldbot.risk.sizing.SIZE_DOWN_RISK)
STAGE1_CLEAR = 0.05         # risk.drawdown_stage1_clear
DRAWDOWN_HALT = 0.12        # risk.drawdown_stage2 (ADR 0106 2's 15 % is the outer bound; the live 12 % fires first)
MAX_RISK = 0.01             # RiskLimits.max_risk_per_trade (ADR 0106 2: "risk per trade <= 1 % of equity")
SIZE_DOWN_RISK = 0.5        # = goldbot.risk.sizing.SIZE_DOWN_RISK


class SimCosts(FrozenRecord):
    rt_bp: float
    slippage_ub95_bp: float
    overshoot_bp: float
    swap: SwapSpec

    @classmethod
    def w2(cls) -> SimCosts:
        return cls(rt_bp=DC.RT_BP, slippage_ub95_bp=0.0, overshoot_bp=DC.OVERSHOOT_BP, swap=DC.swap_spec())

    def exit_cost_r(self, k: np.ndarray, stop: np.ndarray, *, rt_bp: np.ndarray | float | None = None
                    ) -> np.ndarray:
        """R charged at an exit: k (rt + ub95 slippage + overshoot on a stop); `rt_bp`, the row's own round trip of
        record, replaces the constant `self.rt_bp` when given."""
        rt = self.rt_bp if rt_bp is None else np.asarray(rt_bp, dtype=float)
        return np.asarray(k, float) * (rt + self.slippage_ub95_bp + self.overshoot_bp * np.asarray(stop, dtype=float))

    def swap_r(self, k: np.ndarray, side: np.ndarray, nights: np.ndarray, *,
               rate: np.ndarray | float | None = None) -> np.ndarray:
        """R charged for `nights` rollovers: nights x k x 1e4 x |swap rate(side) as a fraction of price|; `rate`, the
        row's swap of record per night (fraction of price), replaces the research spec's rate when given."""
        if rate is not None:
            return np.asarray(nights, float) * np.asarray(k, float) * 1e4 * np.abs(np.asarray(rate, float))
        if self.swap.ref_price_usd is None:
            raise ValueError("SimCosts: the swap spec needs a reference price (a rate on notional)")
        rate = np.abs(self.swap.usd_per_oz(np.asarray(side, float))) / self.swap.ref_price_usd
        return np.asarray(nights, float) * np.asarray(k, float) * 1e4 * rate


class SizingSpec(FrozenRecord):
    spec_id: str = "size-v1"
    base_risk: float = Field(0.005, gt=0, le=MAX_RISK)
    max_risk: float = Field(MAX_RISK, gt=0, le=MAX_RISK)
    vol_lookback: int = 100
    daily_loss_stop: float = Field(DAILY_LOSS_STOP, gt=0, le=DAILY_LOSS_STOP)
    weekly_cap: float = Field(WEEKLY_CAP, gt=0, le=WEEKLY_CAP)
    drawdown_stage1: float = Field(DRAWDOWN_STAGE1, gt=0, le=DRAWDOWN_STAGE1)
    stage1_clear: float = Field(STAGE1_CLEAR, gt=0, le=STAGE1_CLEAR)
    drawdown_halt: float = Field(DRAWDOWN_HALT, gt=0, le=DRAWDOWN_HALT)
    # the loss a trade may take beyond its 1 R stop (a gapped fill), reserved with its cost before the daily and weekly
    # checks: fixed here, before any read
    gap_buffer_r: float = Field(0.25, ge=0)


SIZING = SizingSpec()


def vol_ratio(atr: np.ndarray, *, lookback: int = SIZING.vol_lookback) -> np.ndarray:
    """ATR at each decision over the median of the `lookback` ATRs up to and including it (past only; 1 before
    enough history)."""
    s = pd.Series(np.asarray(atr, float))
    med = s.rolling(lookback, min_periods=max(2, lookback // 5)).median()
    return np.nan_to_num((s / med).to_numpy(), nan=1.0, posinf=1.0, neginf=1.0)


def checked(spec: SizingSpec) -> SizingSpec:
    """`spec` re-validated: `model_copy(update=...)` skips the field validators, so a loosened copy is refused here."""
    return SizingSpec(**spec.model_dump())          # not a persisted load: the constructor runs the validators


def risk_fraction(ratio: np.ndarray, spec: SizingSpec = SIZING) -> np.ndarray:
    """Fraction of equity risked per trade: base_risk / vol_ratio, capped at max_risk (1 %)."""
    spec = checked(spec)
    r = np.maximum(np.asarray(ratio, float), 1e-9)
    return np.minimum(spec.max_risk, spec.base_risk / r)


class SimResult(FrozenRecord):
    spec_id: str
    n_trades: int
    n_skipped_daily: int
    n_skipped_weekly: int
    n_daily_breaches: int           # days whose loss passed the daily stop anyway (a stop gapped past its reserve)
    size_down_trades: int           # trades taken in the 8 % stage at half risk
    halted: bool
    final_equity: float
    max_drawdown: float
    max_risk_used: float
    gross_profit: float
    costs: float
    cost_share: float | None        # costs / gross profit (goal: under one third), None without a gross profit
    daily_sharpe: float | None      # mean / sd of daily returns x sqrt(252), descriptive


def _check(t: pd.DataFrame) -> None:
    """Refuse inputs the limits cannot be applied to (trading-safety L2 on #489)."""
    vr = t["vol_ratio"].to_numpy(dtype=float)
    if not (np.isfinite(vr) & (vr > 0)).all():
        raise ValueError("simulate: vol_ratio must be finite and positive")
    if not np.isfinite(t["gross_r"].to_numpy(dtype=float)).all():
        raise ValueError("simulate: gross_r must be finite")
    c = t["cost_r"].to_numpy(dtype=float)
    if not (np.isfinite(c) & (c >= 0)).all():
        raise ValueError("simulate: cost_r must be finite and not negative (a cost is never a credit)")


def simulate(trades: pd.DataFrame, spec: SizingSpec = SIZING, *, fixed: bool = False,
             equity0: float = 1.0) -> SimResult:
    """Run `trades` (in time order: day, gross_r, cost_r, vol_ratio) through the sizing policy (`fixed`: base_risk
    flat) under the live limits: a trade is skipped when its reserve - (1 R + its cost + gap_buffer_r) x risk - could
    take the day past the daily stop or the ISO week past the weekly cap; from an 8 % drawdown risk is halved until it
    is back under 5 %; at 12 % all trading halts. A day that loses more than the daily stop anyway is counted."""
    spec = checked(spec)
    t = trades.reset_index(drop=True)
    _check(t)
    frac = np.full(len(t), spec.base_risk) if fixed else risk_fraction(t["vol_ratio"].to_numpy(), spec)
    frac = np.minimum(frac, spec.max_risk)
    eq, peak, mdd = equity0, equity0, 0.0
    n = skipped = skipped_w = breaches = size_down_n = 0
    halted = size_down = False
    gross = costs = 0.0
    used = 0.0
    day_ret: dict[object, float] = {}
    breached: set[object] = set()
    cur_day: object = None
    cur_week: object = None
    day_open = week_open = eq
    days = t["day"].to_numpy()
    weeks = [tuple(pd.Timestamp(d).isocalendar()[:2]) for d in days]
    g_r, c_r = t["gross_r"].to_numpy(dtype=float), t["cost_r"].to_numpy(dtype=float)
    for i in range(len(t)):
        if halted:
            break
        if days[i] != cur_day:
            cur_day, day_open = days[i], eq
        if weeks[i] != cur_week:
            cur_week, week_open = weeks[i], eq
        f = frac[i] * (SIZE_DOWN_RISK if size_down else 1.0)
        risk = eq * f
        reserve = (1.0 + float(c_r[i]) + spec.gap_buffer_r) * risk
        if (day_open - eq) + reserve > spec.daily_loss_stop * day_open + 1e-12:
            skipped += 1                      # this trade's gapped stop and cost could take the day past the limit
            continue
        if (week_open - eq) + reserve > spec.weekly_cap * week_open + 1e-12:
            skipped_w += 1
            continue
        g, c = float(g_r[i]) * risk, float(c_r[i]) * risk
        eq += g - c
        gross += max(g, 0.0)
        costs += c
        used = max(used, float(f))
        n += 1
        size_down_n += int(size_down)
        day_ret[cur_day] = eq / day_open - 1
        if day_open - eq > spec.daily_loss_stop * day_open + 1e-12 and cur_day not in breached:
            breached.add(cur_day)
            breaches += 1
        peak = max(peak, eq)
        dd = 1 - eq / peak
        mdd = max(mdd, dd)
        if dd >= spec.drawdown_halt:
            halted = True
        elif dd >= spec.drawdown_stage1:
            size_down = True
        elif size_down and dd < spec.stage1_clear:
            size_down = False
    r = np.array(list(day_ret.values()))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if len(r) > 1 and r.std(ddof=1) > 0 else None
    return SimResult(spec_id=spec.spec_id if not fixed else f"{spec.spec_id}:fixed", n_trades=n,
                     n_skipped_daily=skipped, n_skipped_weekly=skipped_w, n_daily_breaches=breaches,
                     size_down_trades=size_down_n, halted=halted, final_equity=eq, max_drawdown=mdd,
                     max_risk_used=used, gross_profit=gross, costs=costs,
                     cost_share=costs / gross if gross > 0 else None, daily_sharpe=sharpe)


class SizingReport(FrozenRecord):
    policy: SimResult
    fixed: SimResult


def compare(trades: pd.DataFrame, spec: SizingSpec = SIZING) -> SizingReport:
    """size-v1 against the fixed fraction on the same trades (descriptive; never a gate on its own)."""
    return SizingReport(policy=simulate(trades, spec), fixed=simulate(trades, spec, fixed=True))

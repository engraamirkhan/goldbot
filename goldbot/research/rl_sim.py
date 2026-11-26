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

Sizing (`SizingSpec`, `risk_fraction`): risk fraction = min(max_risk, base_risk / vol_ratio), vol_ratio = the ATR at
the decision over its trailing median (`vol_ratio`, past values only). max_risk is the 1 % cap of ADR 0106 2.
`simulate` runs a trade list through it with the real-money limits as plain code: a trade is skipped when its
full stop could take the day's loss past 2 % of the day's opening equity (survival first: the limit is never
crossed, not merely noticed after), all trading halts at a 15 % drawdown from the peak. `compare` reports size-v1
against the fixed fraction base_risk on the same trades (descriptive). PPO/SAC/TD3 are allowed inside this simulator
by ADR 0106; none is fitted here: SYNTH-ml R7(d) declines them this quarter until RL-X1 passes, and the scaffold's
closed-form policy is the baseline any learned sizer must beat. Research only: nothing here reaches the engine, the
RiskGate or an order.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.research import decision_cost as DC

DAILY_LOSS_STOP = 0.02      # ADR 0106 2: "daily loss limit 2 % of equity, then stop for the day"
DRAWDOWN_HALT = 0.15        # ADR 0106 2: "max drawdown halt at 15 %"
MAX_RISK = 0.01             # ADR 0106 2: "risk per trade <= 1 % of equity"


class SimCosts(FrozenRecord):
    rt_bp: float
    slippage_ub95_bp: float
    overshoot_bp: float
    swap: SwapSpec

    @classmethod
    def w2(cls) -> SimCosts:
        return cls(rt_bp=DC.RT_BP, slippage_ub95_bp=0.0, overshoot_bp=DC.OVERSHOOT_BP, swap=DC.swap_spec())

    def exit_cost_r(self, k: np.ndarray, stop: np.ndarray) -> np.ndarray:
        """R charged at an exit: k (rt + ub95 slippage + overshoot on a stop)."""
        return np.asarray(k, float) * (self.rt_bp + self.slippage_ub95_bp
                                       + self.overshoot_bp * np.asarray(stop, dtype=float))

    def swap_r(self, k: np.ndarray, side: np.ndarray, nights: np.ndarray) -> np.ndarray:
        """R charged for `nights` rollovers: nights x k x 1e4 x |swap rate(side) as a fraction of price|."""
        if self.swap.ref_price_usd is None:
            raise ValueError("SimCosts: the swap spec needs a reference price (a rate on notional)")
        rate = np.abs(self.swap.usd_per_oz(np.asarray(side, float))) / self.swap.ref_price_usd
        return np.asarray(nights, float) * np.asarray(k, float) * 1e4 * rate


class SizingSpec(FrozenRecord):
    spec_id: str = "size-v1"
    base_risk: float = 0.005
    max_risk: float = MAX_RISK
    vol_lookback: int = 100
    daily_loss_stop: float = DAILY_LOSS_STOP
    drawdown_halt: float = DRAWDOWN_HALT


SIZING = SizingSpec()


def vol_ratio(atr: np.ndarray, *, lookback: int = SIZING.vol_lookback) -> np.ndarray:
    """ATR at each decision over the median of the `lookback` ATRs up to and including it (past only; 1 before
    enough history)."""
    s = pd.Series(np.asarray(atr, float))
    med = s.rolling(lookback, min_periods=max(2, lookback // 5)).median()
    return np.nan_to_num((s / med).to_numpy(), nan=1.0, posinf=1.0, neginf=1.0)


def risk_fraction(ratio: np.ndarray, spec: SizingSpec = SIZING) -> np.ndarray:
    """Fraction of equity risked per trade: base_risk / vol_ratio, capped at max_risk (1 %)."""
    r = np.maximum(np.asarray(ratio, float), 1e-9)
    return np.minimum(spec.max_risk, spec.base_risk / r)


class SimResult(FrozenRecord):
    spec_id: str
    n_trades: int
    n_skipped_daily: int
    halted: bool
    final_equity: float
    max_drawdown: float
    max_risk_used: float
    gross_profit: float
    costs: float
    cost_share: float | None        # costs / gross profit (goal: under one third), None without a gross profit
    daily_sharpe: float | None      # mean / sd of daily returns x sqrt(252), descriptive


def simulate(trades: pd.DataFrame, spec: SizingSpec = SIZING, *, fixed: bool = False,
             equity0: float = 1.0) -> SimResult:
    """Run `trades` (in time order: day, gross_r, cost_r, vol_ratio) through the sizing policy (`fixed`: base_risk
    flat) with the 2 % daily stop and the 15 % drawdown halt."""
    t = trades.reset_index(drop=True)
    frac = np.full(len(t), spec.base_risk) if fixed else risk_fraction(t["vol_ratio"].to_numpy(), spec)
    frac = np.minimum(frac, spec.max_risk)
    eq, peak, mdd = equity0, equity0, 0.0
    n = skipped = 0
    halted = False
    gross = costs = 0.0
    used = 0.0
    day_ret: dict[object, float] = {}
    cur_day: object = None
    day_open = eq
    days = t["day"].to_numpy()
    g_r, c_r = t["gross_r"].to_numpy(dtype=float), t["cost_r"].to_numpy(dtype=float)
    for i in range(len(t)):
        if halted:
            break
        if days[i] != cur_day:
            cur_day, day_open = days[i], eq
        risk = eq * frac[i]
        if (day_open - eq) + risk > spec.daily_loss_stop * day_open + 1e-12:
            skipped += 1                      # this trade's full stop could take the day past the 2 % limit
            continue
        g, c = float(g_r[i]) * risk, float(c_r[i]) * risk
        eq += g - c
        gross += max(g, 0.0)
        costs += c
        used = max(used, float(frac[i]))
        n += 1
        day_ret[cur_day] = eq / day_open - 1
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        if mdd >= spec.drawdown_halt:
            halted = True
    r = np.array(list(day_ret.values()))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if len(r) > 1 and r.std(ddof=1) > 0 else None
    return SimResult(spec_id=spec.spec_id if not fixed else f"{spec.spec_id}:fixed", n_trades=n,
                     n_skipped_daily=skipped, halted=halted, final_equity=eq, max_drawdown=mdd, max_risk_used=used,
                     gross_profit=gross, costs=costs, cost_share=costs / gross if gross > 0 else None,
                     daily_sharpe=sharpe)


class SizingReport(FrozenRecord):
    policy: SimResult
    fixed: SimResult


def compare(trades: pd.DataFrame, spec: SizingSpec = SIZING) -> SizingReport:
    """size-v1 against the fixed fraction on the same trades (descriptive; never a gate on its own)."""
    return SizingReport(policy=simulate(trades, spec), fixed=simulate(trades, spec, fixed=True))

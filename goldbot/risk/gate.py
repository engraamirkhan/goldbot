"""RiskGate: hard limits no model output can override. Sits between the allocator and the broker.

Per-account limits (daily 2% / weekly 5% of own equity; staged drawdown 8% size-down, 12% stop), plus
the supervisor applies tighter limits on combined equity (1.5% / 4%) and writes a HALT flag.
All state is explicit and serialisable so the engine can reconcile after a restart.
"""
from __future__ import annotations

from enum import Enum

import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.data.timeutil import risk_day


class Stage(str, Enum):
    NORMAL = "normal"
    SIZE_DOWN = "size_down"      # 8% drawdown: multiplier ceiling 0.5, risk per trade halved
    HALTED = "halted"            # 12%: no entries until /rearm + 10 days positive shadow + 30 days propose


class RiskLimits(Record):
    risk_per_trade: float = 0.005
    max_risk_per_trade: float = 0.01
    multiplier_bounds: tuple[float, float] = (0.25, 1.5)
    daily_cap: float = 0.02
    weekly_cap: float = 0.05
    dd_stage1: float = 0.08
    dd_stage2: float = 0.12
    dd_stage1_clear: float = 0.05
    max_positions: int = 2
    max_spread_points: float = 45.0
    point: float = 0.01
    stale_tick_seconds: int = 20
    min_target_over_cost: float = 2.5
    margin_level_floor: float = 3.0      # 300%
    leverage_cap: float = 20.0           # FCA retail gold


class AccountState(Record):
    equity: float
    balance_closed_hwm: float
    day_start_equity: float
    week_start_equity: float
    open_positions: int
    margin_used: float
    last_tick_age_s: float
    spread_points: float
    stage: Stage = Stage.NORMAL
    supervisor_halt: bool = False
    owner_halt: bool = False          # /halt from Telegram or the dashboard (state/control.json)
    in_blackout: bool = False
    dq_error: bool = False


class Intent(Record):
    agent_id: str
    side: int
    p: float
    target_atr: float
    stop_atr: float
    atr_usd: float            # ATR in price units ($/oz)
    cost_atr: float           # round-trip cost in ATR
    multiplier: float         # from allocator x bet sizing, before bounds
    price: float
    contract_oz: float = 100.0
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 2.0
    stops_level_points: float = 0.0


class GateDecision(Record):
    allowed: bool
    lots: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    risk_fraction: float = 0.0
    stop_distance: float = 0.0


class RiskGate:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    # ---------------------------------------------------------------- drawdown stage machine
    def update_stage(self, st: AccountState) -> Stage:
        dd = 1 - st.equity / st.balance_closed_hwm if st.balance_closed_hwm > 0 else 0.0
        if st.stage == Stage.HALTED:
            return st.stage  # only /rearm clears it
        if dd >= self.limits.dd_stage2:
            st.stage = Stage.HALTED
        elif dd >= self.limits.dd_stage1:
            st.stage = Stage.SIZE_DOWN
        elif st.stage == Stage.SIZE_DOWN and dd < self.limits.dd_stage1_clear:
            st.stage = Stage.NORMAL
        return st.stage

    def rearm(self, st: AccountState, confirmation: str) -> bool:
        if confirmation != "REARM":  # the Telegram layer adds TOTP before calling this
            return False
        st.stage = Stage.NORMAL
        st.balance_closed_hwm = st.equity
        return True

    # ---------------------------------------------------------------- entry check + sizing
    def check(self, intent: Intent, st: AccountState, now_utc: pd.Timestamp | None = None) -> GateDecision:
        L = self.limits
        reasons: list[str] = []
        self.update_stage(st)
        if st.supervisor_halt:
            reasons.append("supervisor_halt")
        if st.owner_halt:
            reasons.append("owner_halt")
        if st.stage == Stage.HALTED:
            reasons.append("drawdown_halt")
        if st.dq_error:
            reasons.append("data_quality_error")
        if st.in_blackout:
            reasons.append("news_blackout")
        if st.last_tick_age_s > L.stale_tick_seconds:
            reasons.append("stale_data")
        if st.spread_points > L.max_spread_points:
            reasons.append("spread_too_wide")
        if st.open_positions >= L.max_positions:
            reasons.append("max_positions")
        day_loss = 1 - st.equity / st.day_start_equity if st.day_start_equity else 0.0
        week_loss = 1 - st.equity / st.week_start_equity if st.week_start_equity else 0.0
        if day_loss >= L.daily_cap:
            reasons.append("daily_cap")
        if week_loss >= L.weekly_cap:
            reasons.append("weekly_cap")
        # expected value and minimum target over cost
        ev = intent.p * intent.target_atr - (1 - intent.p) * intent.stop_atr - intent.cost_atr
        if ev <= 0:
            reasons.append("negative_ev")
        if intent.target_atr < L.min_target_over_cost * intent.cost_atr:
            reasons.append("target_below_cost_floor")
        if reasons:
            return GateDecision(allowed=False, reasons=reasons)

        # sizing
        lo, hi = L.multiplier_bounds
        mult = min(max(intent.multiplier, lo), hi)
        risk_frac = min(L.risk_per_trade, L.max_risk_per_trade)
        if st.stage == Stage.SIZE_DOWN:
            risk_frac *= 0.5
            mult = min(mult, 0.5)
        stop_distance = max(intent.stop_atr * intent.atr_usd, intent.stops_level_points * L.point + st.spread_points * L.point)
        risk_usd = st.equity * risk_frac * mult
        lots_raw = risk_usd / (stop_distance * intent.contract_oz)
        lots = max(intent.volume_min, (lots_raw // intent.volume_step) * intent.volume_step)
        lots = min(lots, intent.volume_max)
        realised_risk = lots * stop_distance * intent.contract_oz / st.equity
        if realised_risk > 1.2 * risk_frac * mult and lots_raw < intent.volume_min:
            return GateDecision(allowed=False, reasons=["min_lot_exceeds_risk"], stop_distance=stop_distance)
        # margin at FCA cap
        notional = lots * intent.contract_oz * intent.price
        margin_needed = notional / L.leverage_cap
        level_after = st.equity / max(st.margin_used + margin_needed, 1e-9)
        if level_after < L.margin_level_floor:
            return GateDecision(allowed=False, reasons=["margin_level_floor"], stop_distance=stop_distance)
        return GateDecision(allowed=True, lots=round(lots, 2), risk_fraction=realised_risk, stop_distance=stop_distance)


def new_day(st: AccountState, now_utc: pd.Timestamp, last_reset: pd.Timestamp | None) -> bool:
    """Reset the day-start equity at the risk-day boundary (00:00 UTC)."""
    if last_reset is None or risk_day(pd.DatetimeIndex([now_utc]))[0] != risk_day(pd.DatetimeIndex([last_reset]))[0]:
        st.day_start_equity = st.equity
        return True
    return False

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
from goldbot.config import RiskSettings
from goldbot.data.timeutil import floor_tf, risk_day

REARM_PHRASE = "REARM"   # what rearm() expects; the engine passes it only for an owner re-arm seen on the bus


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

    @classmethod
    def from_settings(cls, risk: RiskSettings, *, tiny_live: bool) -> RiskLimits:
        """The `risk:` section of settings.yaml; `tiny_live` selects the tiny-live phase's risk per trade."""
        return cls(risk_per_trade=risk.risk_per_trade_tiny_live if tiny_live else risk.risk_per_trade,
                   multiplier_bounds=risk.multiplier_bounds, daily_cap=risk.daily_cap, weekly_cap=risk.weekly_cap,
                   dd_stage1=risk.drawdown_stage1, dd_stage2=risk.drawdown_stage2,
                   dd_stage1_clear=risk.drawdown_stage1_clear, max_positions=risk.max_positions_per_account,
                   max_spread_points=risk.max_spread_points, stale_tick_seconds=risk.stale_tick_seconds,
                   min_target_over_cost=risk.min_target_over_cost)


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
        if confirmation != REARM_PHRASE:  # the owner's TOTP-verified re-arm (control.json rearm_seq) precedes this
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


def _risk_week(ts: pd.Timestamp) -> pd.Timestamp:
    return floor_tf(pd.DatetimeIndex([ts]), 7 * 86400)[0]      # weeks start Sunday 00:00 UTC (gold opens Sunday)


def new_day(st: AccountState, now_utc: pd.Timestamp, last_reset: pd.Timestamp | None) -> bool:
    """Reset the day-start equity at the risk-day boundary (00:00 UTC), and the week-start equity when the reset
    also crosses into a new risk week. Returns True when a reset happened (the caller persists `now_utc`)."""
    if last_reset is None or risk_day(pd.DatetimeIndex([now_utc]))[0] != risk_day(pd.DatetimeIndex([last_reset]))[0]:
        st.day_start_equity = st.equity
        if last_reset is None or _risk_week(now_utc) != _risk_week(last_reset):
            st.week_start_equity = st.equity
        return True
    return False

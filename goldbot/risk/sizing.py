"""RiskGate's sizing arithmetic as pure functions (design "Sizing" and "Hard limits"), so the analytics that must agree
with the gate (research/ruin.py, research/min_lot.py) share one copy of it.

This mirrors goldbot/risk/gate.py `RiskGate.check` (the "sizing" block) line for line:
* the model multiplier is clamped to `multiplier_bounds`;
* in the 8% stage (or the supervisor's combined 8% stage) risk per trade is halved AND the multiplier is capped at 0.5,
  so at m = 1 the real risk is a quarter of the normal rate;
* risk per trade x multiplier is capped at `max_risk_per_trade` after the multiplier (clamped, not rejected);
* stop distance = max(stop_atr x ATR, stops level + spread);
* lots = equity x risk x m / (stop distance x contract), rounded down to the lot step, at least the minimum lot, at
  most the maximum; the trade is skipped when the minimum lot would risk more than 1.2x the target.
tests/test_sizing_parity.py runs RiskGate.check against these functions so the two cannot drift apart. gate.py does not
call this module yet (other work is changing it); once that lands it should, and the parity test then guards a
single implementation.
"""
from __future__ import annotations

import math
from typing import Protocol

from goldbot.base import FrozenRecord

MIN_LOT_TOLERANCE = 1.2      # the minimum lot is used only while it keeps risk within 1.2x the target
SIZE_DOWN_RISK = 0.5         # 8% stage: risk per trade halved
SIZE_DOWN_MULT_CAP = 0.5     # 8% stage: multiplier ceiling


class SizingLimits(Protocol):
    """What sizing reads from the limits (gate.RiskLimits has these fields)."""

    @property
    def risk_per_trade(self) -> float: ...
    @property
    def max_risk_per_trade(self) -> float: ...
    @property
    def multiplier_bounds(self) -> tuple[float, float]: ...


class EffectiveRisk(FrozenRecord):
    risk_frac: float          # risk per trade after the stage
    mult: float               # multiplier after the bounds, the stage cap and the hard maximum
    target: float             # risk_frac x mult: the fraction of equity the trade is sized to lose at the stop


class LotSize(FrozenRecord):
    lots_raw: float
    lots: float               # unrounded steps x step (the gate reports round(lots, 2))
    realised_risk: float      # lots x stop x contract / equity
    refused: bool             # min_lot_exceeds_risk


def effective_risk(limits: SizingLimits, size_down: bool, mult: float) -> EffectiveRisk:
    """`size_down`: the account is in the 8% stage or the supervisor's combined size-down flag is set (the gate's
    `st.stage == Stage.SIZE_DOWN or st.combined_size_down`)."""
    lo, hi = limits.multiplier_bounds
    m = min(max(mult, lo), hi)
    risk_frac = limits.risk_per_trade
    if size_down:
        risk_frac *= SIZE_DOWN_RISK
        m = min(m, SIZE_DOWN_MULT_CAP)
    if risk_frac * m > limits.max_risk_per_trade:
        m = limits.max_risk_per_trade / risk_frac
    return EffectiveRisk(risk_frac=risk_frac, mult=m, target=risk_frac * m)


def stop_distance(stop_atr: float, atr_usd: float, *, stops_level_points: float = 0.0, spread_points: float = 0.0,
                  point: float = 0.01) -> float:
    """The planned stop in price units, floored at the broker's stops level plus the spread."""
    return max(stop_atr * atr_usd, stops_level_points * point + spread_points * point)


def size_lots(*, equity: float, risk: EffectiveRisk, stop_distance: float, contract_oz: float, volume_min: float,
              volume_step: float, volume_max: float) -> LotSize:
    """Lots for a trade sized to lose risk.target x equity at `stop_distance`. The products keep the gate's operand
    order (equity x risk_frac x mult, 1.2 x risk_frac x mult) so boundary cases round the same way."""
    risk_usd = equity * risk.risk_frac * risk.mult
    lots_raw = risk_usd / (stop_distance * contract_oz)
    steps = math.floor(lots_raw / volume_step + 1e-9)   # 0.25 // 0.01 == 24.0 in floats: not here
    lots = max(volume_min, steps * volume_step)
    lots = min(lots, volume_max)
    realised = lots * stop_distance * contract_oz / equity
    refused = realised > MIN_LOT_TOLERANCE * risk.risk_frac * risk.mult and lots_raw < volume_min
    return LotSize(lots_raw=lots_raw, lots=lots, realised_risk=realised, refused=refused)


def next_stage(stage: str, dd: float, stage1: float, stage2: float, clear: float) -> str:
    """The drawdown stage machine (gate.py `update_stage`) on Stage values: "normal", "size_down", "halted". `dd` is
    1 - equity / closed-balance high-water mark. Halted is sticky (only the owner's re-arm clears it)."""
    if stage == "halted":
        return stage
    if dd >= stage2:
        return "halted"
    if dd >= stage1:
        return "size_down"
    if stage == "size_down" and dd < clear:
        return "normal"
    return stage

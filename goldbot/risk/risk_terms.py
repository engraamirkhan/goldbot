"""ADR 0039 per-strategy risk terms, kept free of scipy so the MT5 bridge (which imports the limits profile) does not
need it (goldbot/risk/adaptive.py re-exports these)."""
from __future__ import annotations

from pydantic import Field

from goldbot.base import FrozenRecord

MIN_LOT_MAX = 0.01               # the per-trade hard limit
RISK_MIN = 0.0025                # r_s floor: also the value for LCB <= 0


class RiskTerm(FrozenRecord):
    """ADR 0039: one strategy's per-trade risk and why (published in `adaptive_state.risk_terms`, the `limits_change`
    journal and the dashboard). `r`: the decided risk; `target`: where the evidence points; `f`: the quarter-Kelly
    fraction (A1 math before /Q_SCALE); `w`: the shrinkage n_days/(n_days+20); the k factors are 1 here and set by the
    engine at each decision (they only lower r)."""

    r: float = Field(gt=0, le=MIN_LOT_MAX)
    target: float | None = None
    f: float | None = None
    n_days: int = 0
    n_demo: int = 0
    lcb: float | None = None
    w: float = 0.0
    k_vol: float = 1.0
    k_dd: float = 1.0
    k_day: float = 1.0
    reason: str
    why: str = ""

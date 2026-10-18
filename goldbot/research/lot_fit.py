"""H-27 lot-fit overlay (playbook small-account-playbook-2026-10-18, section 3): "no £ figure will again be claimed for
trades the account cannot take".

Each simulated trade is sized as the live RiskGate sizes it: `risk.sizing.size_lots` (the gate's own function, not a
copy) at the configured equity, risk per trade, minimum lot, lot step, contract size and account-currency conversion.
The stop is the label's own (`risk` x `entry`, USD per oz). A trade the gate would refuse `min_lot_exceeds_risk`
(the minimum lot risks more than 1.2x the target, capped at 1%) is `unfit`: it is excluded from the £ P&L and reported
as a count and a fraction. A fitting trade's P&L is its per-oz result x the contract x the actual lot x the conversion.
Reporting only, opt-in (`lot_fit=` on run_specialist / evaluate): without it every research output is unchanged.
Also modelled, from the gate: the minimum-lot exception (`min_lot_risk_cap`, off by default, never in tiny-live; its
cap is `RiskGate.min_lot_cap`), the 8% size-down stage and the model multiplier (fixed per run), and the stop floor
(stops level plus spread). tests/test_lot_fit_overlay.py runs RiskGate.check over a grid and requires the same verdict.
Not evaluated, as in min_lot.py: margin, the other gate checks, and compounding (equity is fixed).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.research.min_lot import LotSpec
from goldbot.risk import sizing
from goldbot.risk.gate import RiskGate, RiskLimits

if TYPE_CHECKING:
    from goldbot.config import Settings


class LotFitSpec(FrozenRecord):
    """The account the overlay sizes for. `acct_per_usd`: account currency per USD (a GBP account at GBPUSD 1.33 is
    0.75); a research assumption, since the engine reads the broker's figure live. `min_lot_risk_cap`: the gate's
    minimum-lot exception (None = off, the default; never in tiny-live). `size_down`: the 8% stage (risk halved,
    multiplier capped at 0.5, exception cap halved), held for every trade. `multiplier`: the model multiplier.
    `spread_points`, `stops_level_points`: the gate's stop floor (stops level plus spread)."""

    equity: float = Field(1000.0, gt=0)
    acct_per_usd: float = Field(1.0, gt=0)
    risk_per_trade: float = Field(0.005, gt=0, le=0.01)
    max_risk_per_trade: float = Field(0.01, gt=0)
    multiplier_bounds: tuple[float, float] = (0.25, 1.5)
    min_lot_risk_cap: float | None = Field(None, gt=0, le=0.01)
    size_down: bool = False
    multiplier: float = Field(1.0, gt=0)
    spread_points: float = Field(0.0, ge=0)
    stops_level_points: float = Field(0.0, ge=0)
    lot: LotSpec = Field(default_factory=LotSpec)

    @classmethod
    def from_settings(cls, settings: Settings, *, tiny_live: bool = False, size_down: bool = False) -> LotFitSpec:
        """Equity and conversion from `research.lot_fit_*`; the risk terms through `RiskLimits.from_settings`, so
        tiny-live's rate and its refusal of the exception are the gate's own."""
        gl = RiskLimits.from_settings(settings.risk, tiny_live=tiny_live)
        return cls(equity=settings.research.lot_fit_equity, acct_per_usd=settings.research.lot_fit_acct_per_usd,
                   risk_per_trade=gl.risk_per_trade, max_risk_per_trade=gl.max_risk_per_trade,
                   multiplier_bounds=gl.multiplier_bounds, min_lot_risk_cap=gl.min_lot_risk_cap, size_down=size_down,
                   spread_points=settings.research.lot_fit_spread_points,
                   stops_level_points=settings.research.lot_fit_stops_level_points)

    def limits(self) -> RiskLimits:
        return RiskLimits(risk_per_trade=self.risk_per_trade, max_risk_per_trade=self.max_risk_per_trade,
                          multiplier_bounds=self.multiplier_bounds, min_lot_risk_cap=self.min_lot_risk_cap)


def lot_fit_lines(lf: dict[str, Any] | None) -> list[str]:
    """Report lines for `metrics.lot_fit` (empty without the overlay)."""
    if not lf:
        return []

    def money(x: float | None) -> str:
        return "n/a" if x is None else f"{'-' if x < 0 else ''}£{abs(x):,.2f}"

    def num(x: float | None, fmt: str) -> str:
        return "n/a" if x is None else format(x, fmt)

    ref = lf.get("model_filtered") or lf.get("all_candidates") or {}
    spread, level = ref.get("spread_points", 0.0), ref.get("stops_level_points", 0.0)
    floor = (f"stop floor: spread prior only ({spread:g} points)" if not level
             else f"stop floor: stops level {level:g} + spread {spread:g} points")
    lines = ["### Lot fit (H-27: what the account can take at the gate's own sizing)", "",
             f"Account {money(ref.get('equity'))} at {ref.get('acct_per_usd')} per USD, risk {num(ref.get('risk_fraction'), '.3%')}"
             f" per trade, min-lot exception cap {ref.get('min_lot_risk_cap')}, size-down {ref.get('size_down')}. A trade "
             "the gate would refuse `min_lot_exceeds_risk` is unfit: no P&L is claimed for it.", "",
             "Fixed assumptions: equity (no compounding) and the account conversion; "
             f"model multiplier fixed at {ref.get('multiplier', 1.0):.1f} (live varies with p; fit fraction optimistic "
             f"for low-p trades); {floor}.", "",
             "| set | n | fit fraction | unfit | mean lots | £ P&L at lots | £ per fit trade | R at lots | net R (fit) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ("all_candidates", "model_filtered"):
        s = lf.get(name)
        if not s:
            continue
        ff = s["fit_fraction"]
        lines.append(f"| {name} | {s['n']} | fit fraction {num(None if ff is None else ff * 100, '.1f')}% | "
                     f"{s['n_unfit']} unfit | {num(s['mean_lots'], '.3f')} | {money(s['pnl_gbp'])} | "
                     f"{money(s['mean_pnl_gbp'])} | R at lots {num(s['r_at_lots'], '+.3f')} | {num(s['net_r_fit'], '+.3f')} |")
    return [*lines, ""]


def lot_fit_overlay(trades: pd.DataFrame, spec: LotFitSpec) -> dict[str, Any]:
    """Fit and £ outcome of `trades` (rows with `entry`, `ret` and `risk`, the last two fractions of the entry price,
    as the pipeline's labels). Reports n, n_fit, n_unfit, fit_fraction, mean_lots, pnl_gbp and mean_pnl_gbp (account
    currency, at the actual lots), r_at_lots (mean of P&L / the intended risk, equity x risk x multiplier) and net_r_fit
    (mean ret / risk of the fitting trades: the lot does not change it). A row without a finite positive stop is
    unfit (no stop, no size)."""
    n = int(len(trades))
    entry = trades["entry"].to_numpy(dtype=float)
    ret = trades["ret"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    limits = spec.limits()
    eff = sizing.effective_risk(limits, spec.size_down, spec.multiplier)
    cap = RiskGate(limits).min_lot_cap(spec.size_down)         # the gate's own exception cap (None: off)
    lots = np.zeros(n)
    fit = np.zeros(n, dtype=bool)
    n_exc = 0
    for i in range(n):
        raw = risk[i] * entry[i]
        if not (np.isfinite(raw) and raw > 0 and np.isfinite(ret[i])):
            continue
        stop = sizing.stop_distance(1.0, float(raw), stops_level_points=spec.stops_level_points,
                                    spread_points=spec.spread_points, point=limits.point)
        sz = sizing.size_lots(equity=spec.equity, risk=eff, stop_distance=stop, contract_oz=spec.lot.contract_oz,
                              volume_min=spec.lot.volume_min, volume_step=spec.lot.volume_step,
                              volume_max=spec.lot.volume_max, acct_per_usd=spec.acct_per_usd)
        if not sz.refused:
            lots[i], fit[i] = sz.lots, True
        elif (cap is not None and spec.lot.volume_min <= spec.lot.volume_max
              and spec.lot.volume_min * sz.risk_per_lot / spec.equity <= cap):    # gate.check: min_lot_exception
            lots[i], fit[i] = spec.lot.volume_min, True
            n_exc += 1
    pnl = np.where(fit, ret * entry * spec.lot.contract_oz * lots * spec.acct_per_usd, 0.0)
    n_fit = int(fit.sum())
    intended = spec.equity * eff.target
    return {
        "n": n, "n_fit": n_fit, "n_unfit": n - n_fit, "n_min_lot_exception": n_exc,
        "fit_fraction": n_fit / n if n else None,
        "mean_lots": float(lots[fit].mean()) if n_fit else None,
        "pnl_gbp": float(pnl.sum()),
        "mean_pnl_gbp": float(pnl[fit].mean()) if n_fit else None,
        "r_at_lots": float((pnl[fit] / intended).mean()) if n_fit else None,
        "net_r_fit": float((ret[fit] / risk[fit]).mean()) if n_fit else None,
        "equity": spec.equity, "acct_per_usd": spec.acct_per_usd, "risk_fraction": eff.target,
        "min_lot_risk_cap": cap, "size_down": spec.size_down, "spread_points": spec.spread_points,
        "stops_level_points": spec.stops_level_points, "multiplier": spec.multiplier,
    }

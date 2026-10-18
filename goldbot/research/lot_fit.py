"""H-27 lot-fit overlay (playbook small-account-playbook-2026-10-18, section 3): "no £ figure will again be claimed for
trades the account cannot take".

Each simulated trade is sized as the live RiskGate sizes it: `risk.sizing.size_lots` (the gate's own function, not a
copy) at the configured equity, risk per trade, minimum lot, lot step, contract size and account-currency conversion.
The stop is the label's own (`risk` x `entry`, USD per oz). A trade the gate would refuse `min_lot_exceeds_risk`
(the minimum lot risks more than 1.2x the target, capped at 1%) is `unfit`: it is excluded from the £ P&L and reported
as a count and a fraction. A fitting trade's P&L is its per-oz result x the contract x the actual lot x the conversion.
Reporting only, opt-in (`lot_fit=` on run_specialist / evaluate): without it every research output is unchanged.
Not evaluated here, as in min_lot.py: the minimum-lot exception (`min_lot_risk_cap`, off by default and never in
tiny-live), caps, margin, the 8% size-down stage and the model multiplier (1.0), and compounding (equity is fixed).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.research.min_lot import LotSpec
from goldbot.risk import sizing

if TYPE_CHECKING:
    from goldbot.config import Settings


class _Limits(FrozenRecord):
    """What sizing reads from the gate's limits (`sizing.SizingLimits`)."""

    risk_per_trade: float
    max_risk_per_trade: float
    multiplier_bounds: tuple[float, float]


class LotFitSpec(FrozenRecord):
    """The account the overlay sizes for. `acct_per_usd`: account currency per USD (a GBP account at GBPUSD 1.33 is
    0.75); a research assumption, since the engine reads the broker's figure live."""

    equity: float = Field(1000.0, gt=0)
    acct_per_usd: float = Field(1.0, gt=0)
    risk_per_trade: float = Field(0.005, gt=0, le=0.01)
    max_risk_per_trade: float = Field(0.01, gt=0)
    multiplier_bounds: tuple[float, float] = (0.25, 1.5)
    lot: LotSpec = Field(default_factory=LotSpec)

    @classmethod
    def from_settings(cls, settings: Settings) -> LotFitSpec:
        return cls(equity=settings.research.lot_fit_equity, acct_per_usd=settings.research.lot_fit_acct_per_usd,
                   risk_per_trade=settings.risk.risk_per_trade, multiplier_bounds=settings.risk.multiplier_bounds)

    def limits(self) -> _Limits:
        return _Limits(risk_per_trade=self.risk_per_trade, max_risk_per_trade=self.max_risk_per_trade,
                       multiplier_bounds=self.multiplier_bounds)


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
    eff = sizing.effective_risk(spec.limits(), False, 1.0)
    lots = np.zeros(n)
    fit = np.zeros(n, dtype=bool)
    for i in range(n):
        stop = risk[i] * entry[i]
        if not (np.isfinite(stop) and stop > 0 and np.isfinite(ret[i])):
            continue
        sz = sizing.size_lots(equity=spec.equity, risk=eff, stop_distance=float(stop), contract_oz=spec.lot.contract_oz,
                              volume_min=spec.lot.volume_min, volume_step=spec.lot.volume_step,
                              volume_max=spec.lot.volume_max, acct_per_usd=spec.acct_per_usd)
        if not sz.refused:
            lots[i], fit[i] = sz.lots, True
    pnl = np.where(fit, ret * entry * spec.lot.contract_oz * lots * spec.acct_per_usd, 0.0)
    n_fit = int(fit.sum())
    intended = spec.equity * eff.target
    return {
        "n": n, "n_fit": n_fit, "n_unfit": n - n_fit,
        "fit_fraction": n_fit / n if n else None,
        "mean_lots": float(lots[fit].mean()) if n_fit else None,
        "pnl_gbp": float(pnl.sum()),
        "mean_pnl_gbp": float(pnl[fit].mean()) if n_fit else None,
        "r_at_lots": float((pnl[fit] / intended).mean()) if n_fit else None,
        "net_r_fit": float((ret[fit] / risk[fit]).mean()) if n_fit else None,
        "equity": spec.equity, "acct_per_usd": spec.acct_per_usd, "risk_fraction": eff.target,
    }

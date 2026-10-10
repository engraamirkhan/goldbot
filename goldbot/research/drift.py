"""Drift and health (design: Drift and health; rows M26, M27).

* Feature drift: PSI of each model input on recent candidates against the distribution the model was trained on
  (`reference_bins`, stored on the model at fit time). Above `psi_warn` (0.1) on a top-10 feature warns, above
  `psi_size_down` (0.25) sizes the agent down to 50%. "Top-10" is by the model's gain importance: per-decision TreeSHAP
  is not stored yet (row M28), so gain stands in for SHAP rank.
* Calibration drift: ECE and Brier score of the agent's own p on its trailing `calib_trades` (100) closed shadow trades
  that were taken; ECE above `ece_size_down` (0.08) sizes down.
* Specialist halt: a one-sided CUSUM on standardised trade residuals (realised R minus the R the model's p implied).
  An alarm halts that agent's entries until the owner reviews it or a new champion replaces the version.
* System halt: two agents halted at once, or an agent's 30-day shadow drawdown above 1.5x its backtest drawdown, halts
  every entry pending the owner's review (`python -m goldbot.ops.run drift-review --clear`).

Exits are never affected: halts and size-downs act on new entries only.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.research.metrics import calibration_ece

NAN_BIN = "nan"


def reference_bins(Z: pd.DataFrame, bins: int = 10) -> dict[str, dict[str, Any]]:
    """Per column: interior decile edges of the training values and the share of training rows in each bin (plus
    the share that was NaN), so live values can be compared without keeping the training data."""
    out: dict[str, dict[str, Any]] = {}
    for c in Z.columns:
        v = pd.to_numeric(Z[c], errors="coerce").to_numpy(dtype=float)
        finite = v[np.isfinite(v)]
        if len(finite) == 0:
            continue
        edges = np.unique(np.quantile(finite, np.linspace(0, 1, bins + 1)[1:-1]))
        out[c] = {"edges": edges.tolist(), "props": _props(v, edges).tolist(), "n": int(len(v))}
    return out


def _props(v: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Shares per bin: len(edges) + 1 value bins, then one NaN bin."""
    v = np.asarray(v, dtype=float)
    fin = np.isfinite(v)
    counts = np.bincount(np.searchsorted(edges, v[fin], side="right"), minlength=len(edges) + 1).astype(float)
    counts = np.append(counts, float((~fin).sum()))
    return counts / max(len(v), 1)


def psi(ref: dict[str, Any], values: np.ndarray, eps: float = 1e-4) -> float:
    """Population stability index of `values` against a reference from `reference_bins`."""
    edges = np.asarray(ref["edges"], dtype=float)
    p_ref = np.clip(np.asarray(ref["props"], dtype=float), eps, None)
    p_new = np.clip(_props(np.asarray(values, dtype=float), edges), eps, None)
    return float(np.sum((p_new - p_ref) * np.log(p_new / p_ref)))


def residual_cusum(z: list[float], k: float = 0.5, h: float = 4.0) -> tuple[bool, float]:
    """One-sided (downward) CUSUM on standardised residuals: (alarm, final statistic). M25: k and h are fixed, not yet
    tuned to the design's 5% quarterly false-alarm rate."""
    s, alarm = 0.0, False
    for x in z:
        s = max(0.0, s - x - k)
        alarm = alarm or s > h
    return alarm, s


def trade_residuals(trades: list[Any]) -> list[float]:
    """Standardised residual per closed taken shadow trade: (realised R - expected R) / sd, where the model's p implies
    E[R] = p*T - (1-p) and sd = sqrt(p(1-p)) * (T + 1) with T the target distance in stop units."""
    z: list[float] = []
    for t in trades:
        risk = abs(t.entry - t.stop)
        if risk <= 0 or t.exit is None:
            continue
        T = abs(t.target - t.entry) / risk
        r = (t.exit - t.entry) * t.side / risk
        p = min(max(float(t.p), 1e-3), 1 - 1e-3)
        sd = np.sqrt(p * (1 - p)) * (T + 1)
        z.append(float((r - (p * T - (1 - p))) / sd))
    return z


def drawdown(rets: list[float]) -> float:
    """Largest peak-to-trough fall of the compounded equity curve of `rets` (as a positive fraction)."""
    if not rets:
        return 0.0
    eq = np.cumprod(1 + np.asarray(rets, dtype=float))
    peak = np.maximum.accumulate(np.maximum(eq, 1.0))
    return float(np.max(1 - eq / peak))


class AgentHealth(Record):
    agent_id: str
    version: str
    psi: dict[str, float] = Field(default_factory=dict)       # top features only
    psi_warn: list[str] = Field(default_factory=list)
    psi_size_down: list[str] = Field(default_factory=list)
    n_live_rows: int = 0
    ece: float | None = None
    brier: float | None = None
    n_calib: int = 0
    cusum: float = 0.0
    cusum_alarm: bool = False
    dd_30d: float = 0.0
    backtest_dd: float | None = None
    size_factor: float = 1.0
    halted: bool = False
    notes: list[str] = Field(default_factory=list)


def assess(agent_id: str, version: str, *, model: Any, live: pd.DataFrame | None, closed_taken: list[Any],
           recent_taken: list[Any], backtest_dd: float | None, s: Any) -> AgentHealth:
    """One agent's health from its model (reference bins, importance), recent candidate features (`live`, model
    inputs before side-alignment), its taken shadow trades since the version started (`closed_taken`, oldest first)
    and those of the last 30 days (`recent_taken`). `s`: DriftSettings."""
    h = AgentHealth(agent_id=agent_id, version=version, backtest_dd=backtest_dd)
    ref = getattr(model, "feature_ref", None) or {}
    if not ref:
        h.notes.append("no training reference on this model (trained before drift checks): PSI not computed")
    elif live is None or len(live) < s.min_rows:
        h.n_live_rows = 0 if live is None else len(live)
        h.notes.append(f"{h.n_live_rows} recent candidates (< {s.min_rows}): PSI not computed")
    else:
        Z = model.design(live)
        h.n_live_rows = len(Z)
        try:
            imp = model.importance()
            top = [c for c in imp.index[: s.top_features] if c in ref]
        except Exception:                                # no fitted booster (stand-in model): every referenced input
            top = list(ref)[: s.top_features]
        for c in top:
            if c in Z.columns:
                v = psi(ref[c], pd.to_numeric(Z[c], errors="coerce").to_numpy(dtype=float))
                h.psi[c] = round(v, 4)
                if v > s.psi_size_down:
                    h.psi_size_down.append(c)
                elif v > s.psi_warn:
                    h.psi_warn.append(c)
    calib = closed_taken[-s.calib_trades:]
    h.n_calib = len(calib)
    if len(calib) >= s.min_calib_trades:
        p = np.array([t.p for t in calib], dtype=float)
        y = np.array([t.barrier == "target" for t in calib], dtype=float)
        h.ece, h.brier = round(calibration_ece(p, y), 4), round(float(np.mean((p - y) ** 2)), 4)
    h.cusum_alarm, cus = residual_cusum(trade_residuals(closed_taken), s.cusum_k, s.cusum_h)
    h.cusum = round(cus, 3)
    h.dd_30d = round(drawdown([float(t.ret) for t in recent_taken if t.ret is not None]), 4)
    if h.psi_size_down:
        h.size_factor = s.size_down_factor
        h.notes.append(f"PSI > {s.psi_size_down} on {', '.join(h.psi_size_down)}: sized down")
    if h.ece is not None and h.ece > s.ece_size_down:
        h.size_factor = s.size_down_factor
        h.notes.append(f"ECE {h.ece:.3f} > {s.ece_size_down}: sized down")
    if h.cusum_alarm:
        h.halted = True
        h.notes.append("CUSUM alarm on trade residuals: agent halted")
    return h


def system_halt_reasons(health: list[AgentHealth], s: Any) -> list[str]:
    """Design: two specialists halted at once, or a 30-day drawdown above 1.5x backtest, halts the system."""
    out: list[str] = []
    halted = [h.agent_id for h in health if h.halted]
    if len(halted) >= 2:
        out.append(f"{len(halted)} agents halted at once: {', '.join(halted)}")
    for h in health:
        if h.backtest_dd and h.backtest_dd > 0 and h.dd_30d > s.dd_mult * h.backtest_dd:
            out.append(f"{h.agent_id}: 30-day drawdown {h.dd_30d:.1%} > {s.dd_mult}x backtest {h.backtest_dd:.1%}")
    return out

"""Drift and health (design: Drift and health; rows M26, M27).

* Feature drift: PSI of each model input on recent candidates against the distribution the model was trained on
  (`reference_bins`, stored on the model at fit time). Above `psi_warn` (0.1) on a top-10 feature warns, above
  `psi_size_down` (0.25) sizes the agent down to 50%. "Top-10" is by the model's gain importance: per-decision TreeSHAP
  is not stored yet (row M28), so gain stands in for SHAP rank.
* Calibration drift: ECE and Brier score of the agent's own p on its trailing `calib_trades` (100) closed shadow trades
  that were taken; ECE above `ece_size_down` (0.08) sizes down.
* Specialist halt: a one-sided CUSUM on standardised trade residuals (realised R minus the R the model's p implied),
  its decision interval tuned to a 5% quarterly false-alarm rate at the backtest's trade rate, on two-point trade
  residuals at the mean p of the trades in the CUSUM (row M25, research/cusum.py).
  An alarm halts that agent's entries until the owner reviews it or a new champion replaces the version.
* Scored-model drift (S5, plan 2026-11-25 W1-C: "drift watches every shadow and active model, PSI of inputs and
  scores"): `scored_model_drift` for a model that keeps no reference bins (the self-retrain LogitModel): the reference
  is rebuilt from its own training rows, PSI per input and PSI of the model's scores, live against training. Reported
  (learning_kpis, #34), never sizing or halting by itself: a filter's halt rule is its pre-registered forward read.
* System halt: two agents halted at once, or an agent's 30-day shadow drawdown above 1.5x its backtest drawdown, halts
  every entry pending the owner's review (`python -m goldbot.ops.run drift-review --clear`).

Exits are never affected: halts and size-downs act on new entries only.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.research.cusum import DEFAULT_K, FALSE_ALARM_QUARTER, calibrated_h
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


def residual_cusum(z: list[float], k: float = DEFAULT_K, h: float | None = None, trades_per_week: float | None = None,
                   false_alarm: float = FALSE_ALARM_QUARTER, p: float | None = None,
                   ps: list[float] | None = None) -> tuple[bool, float]:
    """One-sided (downward) CUSUM on standardised residuals: (alarm, final statistic). Row M25: unless `h` is given,
    the decision interval is tuned so in-control residuals alarm within one quarter's expected trades (from the
    backtest's `trades_per_week`) with probability at most `false_alarm` (design: 5%; research/cusum.py), simulated as
    two-point trade residuals resampling the taken p values `ps` (else at the mean taken `p`; normal when both are
    None); without a trade rate the fixed FALLBACK_H applies."""
    h = calibrated_h(trades_per_week, k, false_alarm, p=p, ps=ps) if h is None else h
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


def taken_ps(trades: list[Any]) -> list[float]:
    """Model p of each closed taken trade `trade_residuals` scores (clipped as it clips them): the in-control win
    probabilities the CUSUM's h is calibrated on (row M25, resampled per trade)."""
    return [min(max(float(t.p), 1e-3), 1 - 1e-3) for t in trades if t.exit is not None and abs(t.entry - t.stop) > 0]


def mean_taken_p(trades: list[Any]) -> float | None:
    """Mean model p of the closed taken trades `trade_residuals` scores, or None when there are none."""
    ps = taken_ps(trades)
    return float(np.mean(ps)) if ps else None


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
    cusum_h: float | None = None          # the decision interval used (M25: tuned to the trade rate)
    cusum_alarm: bool = False
    dd_30d: float = 0.0
    backtest_dd: float | None = None
    size_factor: float = 1.0
    halted: bool = False
    notes: list[str] = Field(default_factory=list)


def assess(agent_id: str, version: str, *, model: Any, live: pd.DataFrame | None, closed_taken: list[Any],
           recent_taken: list[Any], backtest_dd: float | None, s: Any,
           trades_per_week: float | None = None) -> AgentHealth:
    """One agent's health from its model (reference bins, importance), recent candidate features (`live`, model
    inputs before side-alignment), its taken shadow trades since the version started (`closed_taken`, oldest first)
    and those of the last 30 days (`recent_taken`). `s`: DriftSettings. `trades_per_week`: the backtest's trade rate,
    which with the p values of `closed_taken` sets the CUSUM's decision interval (row M25)."""
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
    # M25: h on two-point residuals resampling the trades' own p values (0.01 buckets), not only their mean
    ps = taken_ps(closed_taken)
    h_used = (calibrated_h(trades_per_week, s.cusum_k, s.cusum_false_alarm, p=mean_taken_p(closed_taken), ps=ps)
              if trades_per_week else s.cusum_h)
    h.cusum_h = round(h_used, 3)
    h.cusum_alarm, cus = residual_cusum(trade_residuals(closed_taken), s.cusum_k, h_used)
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


class ScoredModelDrift(Record):
    """PSI of a scored model's inputs and of its scores, live rows against its training rows."""

    model_id: str
    status: str = ""                      # the model's own status (self-retrain: active or shadow)
    n_ref: int = 0
    n_live: int = 0
    psi: dict[str, float] = Field(default_factory=dict)
    psi_warn: list[str] = Field(default_factory=list)
    psi_size_down: list[str] = Field(default_factory=list)      # the size-down band, named so; reported, never acted on
    psi_score: float | None = None
    score_shift: bool = False             # the score PSI above psi_size_down: the model sees a different population
    notes: list[str] = Field(default_factory=list)


def scored_model_drift(model_id: str, ref: pd.DataFrame, live: pd.DataFrame,
                       score: Callable[[pd.DataFrame], np.ndarray], s: Any) -> ScoredModelDrift:
    """PSI per input column of `ref` (the model's training design) and of `score` on both, `live` against `ref`.
    `s`: DriftSettings (psi_warn, psi_size_down, min_rows). Nothing is computed under `min_rows` live rows (said)."""
    d = ScoredModelDrift(model_id=model_id, n_ref=len(ref), n_live=len(live))
    if len(live) < s.min_rows or len(ref) < s.min_rows:
        d.notes.append(f"{len(live)} live / {len(ref)} training rows (< {s.min_rows}): PSI not computed")
        return d
    bins = reference_bins(ref)
    for c, b in bins.items():
        if c not in live.columns:
            continue
        v = psi(b, pd.to_numeric(live[c], errors="coerce").to_numpy(dtype=float))
        d.psi[c] = round(v, 4)
        if v > s.psi_size_down:
            d.psi_size_down.append(c)
        elif v > s.psi_warn:
            d.psi_warn.append(c)
    ref_scores = pd.DataFrame({"score": np.asarray(score(ref), dtype=float)})
    sb = reference_bins(ref_scores).get("score")
    if sb is not None:
        d.psi_score = round(psi(sb, np.asarray(score(live), dtype=float)), 4)
        d.score_shift = d.psi_score > s.psi_size_down
    return d


def _sr_rows(con: Any, broker: str, arm_id: str, nominal_from_ns: int, features: tuple[str, ...]) -> pd.DataFrame:
    """An arm's candidates with an admitted point-in-time snapshot: decision time, side and the model's features."""
    import json
    rows = con.execute(
        "SELECT o.decision_ns, o.side, v.features FROM exploration_obs o "
        "JOIN exploration_feature_snapshots s ON s.candidate_id = o.candidate_id "
        "JOIN exploration_feature_vectors v ON v.sha256 = s.vector_sha256 "
        "WHERE o.broker = ? AND o.arm_id = ? AND s.status = 'ok' "
        "AND (s.pit = 'written' OR (s.pit = 'nominal' AND s.decision_ns >= ?))",
        (broker, arm_id, nominal_from_ns)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["decision_ns", "side", *features])
    vals = pd.DataFrame([json.loads(f) for *_, f in rows]).reindex(columns=list(features))
    head = pd.DataFrame([r[:2] for r in rows], columns=["decision_ns", "side"])
    return pd.concat([head, vals], axis=1)


def self_retrain_drift(con: Any, s: Any, statuses: tuple[str, ...] = ("active", "shadow")) -> dict[str, dict[str, Any]]:
    """{model_version: ScoredModelDrift dump} for every self-retrain model in `statuses`, from an open aux.db
    connection (read-only is enough): training = the arm's admitted rows up to the model's cutoff (before the nominal
    audit, so a superset of what it trained on), live = after it. Reported (drift.json `self_retrain`, learning_kpis),
    never sizing or halting: a filter is judged forward by its own demotion rule (research/self_retrain.py)."""
    from goldbot.research import self_retrain as sr
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'self_retrain_models'").fetchone():
        return {}
    q = ", ".join("?" for _ in statuses)
    got = con.execute(f"SELECT model_version, broker, arm_id, status, cutoff_ns, model FROM self_retrain_models "
                      f"WHERE status IN ({q})", statuses).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for version, broker, arm_id, status, cutoff_ns, text in got:
        rows = _sr_rows(con, broker, arm_id, int(sr.NOMINAL_FROM.value), sr.FEATURES)
        model = sr.LogitModel.model_validate_json(text)
        ref, live = rows[rows["decision_ns"] <= cutoff_ns], rows[rows["decision_ns"] > cutoff_ns]
        d = scored_model_drift(str(version), sr.design(ref), sr.design(live), model.score, s)
        d.status = str(status)
        out[str(version)] = {"broker": broker, "arm_id": arm_id, **d.model_dump()}
    return out

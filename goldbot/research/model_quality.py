"""Model-quality rules of ADR 0106 (owner specification of 2026-11-26, "Model quality"):

- "Results stable across at least 5 random seeds" -> `seed_stability`: fewer than `MIN_SEEDS` seeds, or any seed whose
  edge does not share the sign of the pooled (positive) edge, is not deployable.
- "Strategy stays profitable when parameters are shifted slightly" -> `param_shift`: net mean > 0 at the declared point
  and at -20 %, -10 %, +10 %, +20 % of each declared strategy parameter, one parameter at a time. The grid is built
  from the declared parameters BEFORE any point is evaluated and is recorded with the verdict.
- "Every deep model beats the gradient-boosted baseline out of sample, or it is not deployed" (decision 3: A, B and D
  must beat C) -> `beats_baseline`: a paired after-cost comparison on the SAME out-of-sample rows, mean net R per
  decision row (0 where a model does not trade), stationary day-block bootstrap (model_zoo.JointBootstrap), pass when
  the difference is > 0 and p = (1 + #draws <= 0) / (1 + B) <= `ALPHA`.

`require_servable(manifest)` is the serving rule built from them: a fitted model is served only when its run manifest
(research/run_manifest.py) was recorded from a clean tree, carries a passing `seed_stability` verdict over the
manifest's own seeds (at least MIN_SEEDS), no failed verdict of any kind, and, for families A, B and D, a passing
`beats_baseline` verdict whose baseline is a recorded C-family manifest. The rules are plain code; settings can
tighten the studies' parameters (`model_quality:` in config/settings.yaml), never the serving rule.

Verdicts are computed by the trainer in the run that fits the model and passed to `run_manifest.record(quality=...)`,
so a model and the evidence that it may serve are one immutable record.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

if TYPE_CHECKING:
    from goldbot.research.run_manifest import RunManifest

__all__ = ["ALPHA", "BASELINE_FAMILY", "BASELINE_REQUIRED", "BOOT", "MANIFEST_REQUIRED_FROM", "MIN_SEEDS",
           "ModelFamily", "ModelRefused", "QualityVerdict", "SEED", "SHIFTS", "ShiftPoint", "beats_baseline",
           "param_shift", "require_servable", "seed_stability", "servable_reasons", "shift_grid"]

# ADR 0106 decision 3: the roles of record. A sequence DL, B order-book DL, C gradient-boosted trees (the baseline),
# D reinforcement learning (sizing/execution), E LLM features, F classical statistics, G ensemble.
ModelFamily = Literal["A", "B", "C", "D", "E", "F", "G"]
BASELINE_FAMILY: ModelFamily = "C"
BASELINE_REQUIRED: frozenset[str] = frozenset({"A", "B", "D"})
MIN_SEEDS = 5
SHIFTS: tuple[float, ...] = (0.1, 0.2)          # +- each: four shifted points per parameter
ALPHA = 0.05
BOOT = 2_000
SEED = 20261126
# A fitted registry model created at or after this instant serves only with a run manifest; older entries were trained
# before manifests existed and keep serving as they are (ADR 0109). Rule models are not fitted and need none.
MANIFEST_REQUIRED_FROM = pd.Timestamp("2026-11-27", tz="UTC")

Check = Literal["seed_stability", "param_shift", "beats_baseline"]


class ModelRefused(PermissionError):
    """A model that may not be loaded for serving, with the reasons."""


class QualityVerdict(FrozenRecord):
    check: Check
    passed: bool
    reasons: list[str]
    detail: dict[str, Any]


# ---------------------------------------------------------------------------------------------- seeds
def seed_stability(edges: Mapping[int, float], *, pooled: float | None = None,
                   min_seeds: int = MIN_SEEDS) -> QualityVerdict:
    """`edges`: seed -> the model's out-of-sample net edge fitted with that seed (same rows, same read). `pooled`:
    the pooled edge when the trainer computes it on the pooled rows; else the mean of the seeds' edges. Passes when
    there are at least `min_seeds` seeds, the pooled edge is > 0 and every seed's edge is > 0 too."""
    seeds = sorted(int(s) for s in edges)
    vals = {int(s): float(e) for s, e in edges.items()}
    pool = float(np.mean(list(vals.values()))) if pooled is None and vals else float(pooled or 0.0)
    reasons: list[str] = []
    if len(seeds) < max(min_seeds, MIN_SEEDS):
        reasons.append(f"{len(seeds)} seeds; at least {max(min_seeds, MIN_SEEDS)} seeds are required")
    if not np.isfinite(pool) or pool <= 0:
        reasons.append(f"pooled edge {pool:+.4f} is not positive")
    for s in seeds:
        if not np.isfinite(vals[s]) or vals[s] <= 0:
            reasons.append(f"seed {s} edge {vals[s]:+.4f} does not share the pooled edge's sign")
    return QualityVerdict(check="seed_stability", passed=not reasons, reasons=reasons,
                          detail={"seeds": seeds, "edges": [vals[s] for s in seeds], "pooled": pool,
                                  "min_seeds": max(min_seeds, MIN_SEEDS)})


# ---------------------------------------------------------------------------------------------- parameter shift
class ShiftPoint(FrozenRecord):
    param: str
    shift: float                       # signed fraction, e.g. -0.2
    value: float | int
    point: dict[str, Any]              # every declared parameter, this one shifted


def _label(shift: float) -> str:
    return f"{shift * 100:+.0f}%"


def _shifted(base: float | int, shift: float) -> float | int:
    if isinstance(base, bool):
        raise ValueError("a boolean is not a shiftable strategy parameter")
    if isinstance(base, int):
        v = int(np.floor(base * (1 + shift) + 0.5)) if base >= 0 else int(np.ceil(base * (1 + shift) - 0.5))
        if v == base:                  # an integer step smaller than one: move one step in the shift's direction
            v = base + (1 if (shift > 0) == (base >= 0) else -1)
        return v
    return float(base) * (1 + shift)


def shift_grid(params: Mapping[str, float | int], shifts: Sequence[float] = SHIFTS) -> list[ShiftPoint]:
    """One point per (parameter, -s, +s for s in shifts), every other parameter at its declared value. Integers stay
    integers and move by at least one step."""
    signed = sorted({-abs(float(s)) for s in shifts} | {abs(float(s)) for s in shifts})
    out: list[ShiftPoint] = []
    for name in params:
        for sh in signed:
            v = _shifted(params[name], sh)
            out.append(ShiftPoint(param=name, shift=sh, value=v, point={**params, name: v}))
    return out


def param_shift(params: Mapping[str, float | int], evaluate: Callable[[dict[str, Any]], float], *,
                shifts: Sequence[float] = SHIFTS) -> QualityVerdict:
    """`evaluate(point)` -> net mean (after costs) of the strategy at that parameter point, on the trainer's fixed
    out-of-sample read. The grid is built before the first call; the declared point is read first, then the grid in
    order. Passes when every value is > 0."""
    if not params:
        raise ValueError("param_shift: no declared parameter to shift")
    grid = shift_grid(params, shifts)          # fixed before any point is read
    base = float(evaluate(dict(params)))
    reasons = [] if np.isfinite(base) and base > 0 else [f"declared point net mean {base:+.4f} is not positive"]
    rows: list[dict[str, Any]] = []
    for p in grid:
        v = float(evaluate(dict(p.point)))
        rows.append({"param": p.param, "shift": p.shift, "value": p.value, "point": p.point, "net_mean": v})
        if not np.isfinite(v) or v <= 0:
            reasons.append(f"{p.param} {_label(p.shift)} ({p.value}): net mean {v:+.4f} is not positive")
    return QualityVerdict(check="param_shift", passed=not reasons, reasons=reasons,
                          detail={"declared": dict(params), "declared_net_mean": base,
                                  "shifts": [float(s) for s in shifts], "grid": rows})


# ---------------------------------------------------------------------------------------------- baseline
def beats_baseline(model_net: pd.Series, baseline_net: pd.Series, days: pd.Series, *, baseline_manifest_id: str,
                   alpha: float = ALPHA, boot: int = BOOT, seed: int = SEED) -> QualityVerdict:
    """Paired after-cost comparison of a model with the C-family baseline on the same out-of-sample rows.

    `model_net`, `baseline_net`: net R after costs per out-of-sample decision row (0 where that model does not trade),
    indexed by the same row keys; `days`: each row's session day, same index. d = mean(model - baseline) per row;
    the p-value is the share of stationary day-block bootstrap draws with d <= 0 (model_zoo.JointBootstrap)."""
    from goldbot.research.model_zoo import JointBootstrap, _p
    if not (model_net.index.equals(baseline_net.index) and model_net.index.equals(days.index)):
        raise ValueError("beats_baseline: the model and the baseline must be read on the same out-of-sample rows "
                         "(identical index, same order)")
    if not model_net.index.is_unique:
        raise ValueError("beats_baseline: duplicate row keys")
    m, b = model_net.to_numpy(float), baseline_net.to_numpy(float)
    if not (np.isfinite(m).all() and np.isfinite(b).all()):
        raise ValueError("beats_baseline: every row needs a finite after-cost net value (0 for no trade)")
    diff = m - b
    bs = JointBootstrap(days.astype(str).to_numpy(), boot=boot, seed=seed)
    _, (n_day, d_day) = bs.day_sums(np.zeros(len(diff)), np.ones(len(diff)), diff)
    d0 = float(diff.mean()) if len(diff) else float("nan")
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = (bs.weights @ d_day[:, 0]) / (bs.weights @ n_day[:, 0])
    p = _p(draws)
    reasons: list[str] = []
    if not np.isfinite(d0) or d0 <= 0:
        reasons.append(f"mean paired difference {d0:+.4f} R is not positive")
    if p > alpha:
        reasons.append(f"p {p:.4f} > {alpha}")
    return QualityVerdict(check="beats_baseline", passed=not reasons, reasons=reasons,
                          detail={"baseline_manifest_id": baseline_manifest_id, "d": d0, "p": p, "alpha": alpha,
                                  "boot": boot, "seed": seed, "n_rows": int(len(diff)), "n_days": int(len(bs.days)),
                                  "model_mean": float(m.mean()) if len(m) else None,
                                  "baseline_mean": float(b.mean()) if len(b) else None})


# ---------------------------------------------------------------------------------------------- serving rule
def servable_reasons(manifest: RunManifest, *, lookup: Callable[[str], RunManifest]) -> list[str]:
    """Why `manifest`'s model may not serve (empty: it may). `lookup(manifest_id)` loads another manifest (the
    baseline's) and raises when it cannot."""
    out: list[str] = []
    if manifest.code_dirty:
        out.append("trained from a dirty tree: it cannot be rebuilt exactly (ADR 0106 decision 7)")
    for v in manifest.quality:
        if not v.passed:
            out.append(f"{v.check} failed: {'; '.join(v.reasons)}")
    seeds = [v for v in manifest.quality if v.check == "seed_stability" and v.passed]
    if not seeds:
        out.append(f"no passing seed_stability verdict (at least {MIN_SEEDS} seeds, every one on the pooled edge's "
                   "side)")
    elif any(sorted(v.detail.get("seeds", [])) != sorted(manifest.seeds) for v in seeds):
        out.append(f"seed_stability verdict seeds differ from the manifest's seeds {sorted(manifest.seeds)}")
    if len(manifest.seeds) < MIN_SEEDS:
        out.append(f"{len(manifest.seeds)} seeds recorded; at least {MIN_SEEDS} are required")
    if manifest.family in BASELINE_REQUIRED:
        wins = [v for v in manifest.quality if v.check == "beats_baseline" and v.passed]
        if not wins:
            out.append(f"family {manifest.family} needs a passing beats_baseline verdict against a C-family "
                       "baseline on the same out-of-sample rows after costs (ADR 0106 decision 3)")
        for v in wins:
            bid = str(v.detail.get("baseline_manifest_id", ""))
            try:
                base = lookup(bid)
            except Exception as exc:   # noqa: BLE001 - any failure to read the baseline refuses the model
                out.append(f"baseline manifest {bid[:12]} cannot be read: {type(exc).__name__}: {exc}")
                continue
            if base.family != BASELINE_FAMILY:
                out.append(f"baseline {bid[:12]} is family {base.family}, not a C-family gradient-boosted model")
    return out


def require_servable(manifest: RunManifest, *, lookup: Callable[[str], RunManifest]) -> None:
    """Raise ModelRefused unless `servable_reasons` is empty. Engine loaders call this before a model scores."""
    reasons = servable_reasons(manifest, lookup=lookup)
    if reasons:
        raise ModelRefused(f"{manifest.model} ({manifest.manifest_id[:12]}) may not serve: " + "; ".join(reasons))

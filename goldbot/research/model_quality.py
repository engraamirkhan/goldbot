"""Model-quality rules of ADR 0106 (owner specification of 2026-11-26, "Model quality"), as ruled by the quant review
of PR #494 (Q1-Q3):

- Seeds (Q1): "Seeds are the deterministic list `seed_list(manifest_key) = [sha256(manifest_key|i) mod 2^31 for i in
  0..n-1]`, n >= MIN_SEEDS, fixed before the first fit. Every seed fitted is recorded, and `seed_stability` covers all
  of them. The served artifact is the seed ensemble (mean of the seeds' scores) or seed index 0, declared in the
  manifest before fitting, never the best seed." `seed_stability`: fewer than MIN_SEEDS seeds, or any seed whose edge
  does not share the sign of the pooled (positive) edge, is not deployable.
- Baseline (Q2), owner goal "every deep model beats the gradient-boosted baseline out of sample, or it is not deployed"
  (ADR 0106 decision 3: A, B, D must beat C) -> `beats_baseline`, a paired after-cost comparison, mean net R per row (0
  where a model does not trade). Window: "The out-of-sample rows are the learn-window walk-forward OOF rows (purged per
  `feature_store.purge`); confirm and test_old rows enter only under a booked read id." Baseline: "The baseline is
  chosen by rule, not by the trainer: the unit's C-family registry manifest fitted on the same folds and `train_end`.
  `servable_reasons` checks the baseline's `train_end` and fold keys equal the model's." Dependence: "Mean block =
  max(5, ceil(1.5 x the longest label horizon in session days among the rows)); B >= 10,000 (the card's minimum)."
  Multiplicity: "Each read is one registry trial (K +1). The A, B and D reads of one unit in one cycle are
  Holm-adjusted at alpha 0.05." -> `holm_adjust`; only a Holm-adjusted pass serves.
- Parameter shift (Q3): "`param_shift` and `seed_stability` evaluate on the same learn-window walk-forward OOF rows as
  `beats_baseline`, never on confirm, test_old or the holdout. The grid result is report-only unless it fails, and a
  recorded failure refuses serving (as coded)." `param_shift`: net mean > 0 at the declared point and at -20 %,
  -10 %, +10 %, +20 % of each declared parameter, one at a time, the grid built before any point is read. Every
  verdict carries `rows_key` (sha256 of the row keys it read); the serving rule requires one rows_key across them.

`require_servable(manifest)` is the serving rule built from them: a fitted model is served only when its run manifest
(research/run_manifest.py) was recorded from a clean tree, its seeds are `seed_list(manifest_key)`, it carries a passing
`seed_stability` over exactly those seeds, no failed verdict of any kind, all verdicts on the same learn-window OOF
rows, and, for families A, B and D, a Holm-adjusted passing `beats_baseline` against the unit's C-family manifest with
the same `train_end` and fold keys. The rules are plain code; settings can tighten the studies' parameters
(`model_quality:` in config/settings.yaml), never the serving rule.

Verdicts are computed by the trainer in the run that fits the model and passed to `run_manifest.record(quality=...)`,
so a model and the evidence that it may serve are one immutable record.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

if TYPE_CHECKING:
    from goldbot.research.run_manifest import RunManifest

__all__ = ["ALPHA", "BASELINE_FAMILY", "BASELINE_REQUIRED", "BOOT", "MANIFEST_REQUIRED_FROM", "MIN_BLOCK_DAYS",
           "MIN_SEEDS", "OOF_WINDOW", "ModelFamily", "ModelRefused", "QualityVerdict", "SEED", "SHIFTS", "Served",
           "ShiftPoint", "beats_baseline", "holm_adjust", "mean_block_days", "param_shift", "require_servable",
           "rows_key", "seed_list", "seed_stability", "servable_reasons", "shift_grid"]

# ADR 0106 decision 3: the roles of record. A sequence DL, B order-book DL, C gradient-boosted trees (the baseline),
# D reinforcement learning (sizing/execution), E LLM features, F classical statistics, G ensemble.
ModelFamily = Literal["A", "B", "C", "D", "E", "F", "G"]
BASELINE_FAMILY: ModelFamily = "C"
BASELINE_REQUIRED: frozenset[str] = frozenset({"A", "B", "D"})
MIN_SEEDS = 5
SHIFTS: tuple[float, ...] = (0.1, 0.2)          # +- each: four shifted points per parameter
ALPHA = 0.05
BOOT = 10_000                                   # the card's minimum number of bootstrap draws
MIN_BLOCK_DAYS = 5
SEED = 20261126
OOF_WINDOW = "learn_oof"                        # the learn-window walk-forward out-of-fold rows
Served = Literal["ensemble", "seed0"]           # what serves: the seeds' mean score, or seed index 0
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


# ---------------------------------------------------------------------------------------------- rows and seeds
def seed_list(manifest_key: str, n: int = MIN_SEEDS) -> list[int]:
    """[sha256(manifest_key|i) mod 2^31 for i in 0..n-1]: the run's seeds, fixed by its key before the first fit."""
    if not manifest_key:
        raise ValueError("seed_list needs a manifest key")
    return [int.from_bytes(hashlib.sha256(f"{manifest_key}|{i}".encode()).digest(), "big") % 2**31 for i in range(n)]


def rows_key(index: pd.Index | Sequence[Any]) -> str:
    """sha256 of the row keys a read used, in order: verdicts of one model must share it."""
    return hashlib.sha256("\n".join(str(k) for k in index).encode()).hexdigest()


def _window_detail(window: str, read_id: str | None) -> dict[str, Any]:
    if window != OOF_WINDOW and not read_id:
        raise ValueError(f"rows of window {window!r} enter only under a booked read id (quant Q2); the default is "
                         f"the learn-window walk-forward OOF rows ({OOF_WINDOW!r})")
    return {"window": window, "read_id": read_id}


def seed_stability(edges: Mapping[int, float], *, rows: str, pooled: float | None = None,
                   min_seeds: int = MIN_SEEDS) -> QualityVerdict:
    """`edges`: seed -> the model's net edge on the learn-window walk-forward OOF rows fitted with that seed (every
    seed fitted, none left out); `rows`: `rows_key` of those rows. `pooled`: the pooled edge when the trainer computes
    it on the pooled rows; else the mean of the seeds' edges. Passes when there are at least `min_seeds` seeds, the
    pooled edge is > 0 and every seed's edge is > 0 too."""
    seeds = [int(s) for s in edges]
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
                                  "min_seeds": max(min_seeds, MIN_SEEDS), "rows_key": rows,
                                  **_window_detail(OOF_WINDOW, None)})


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


def param_shift(params: Mapping[str, float | int], evaluate: Callable[[dict[str, Any]], float], *, rows: str,
                shifts: Sequence[float] = SHIFTS) -> QualityVerdict:
    """`evaluate(point)` -> net mean (after costs) of the strategy at that parameter point on the learn-window
    walk-forward OOF rows whose `rows_key` is `rows` (the rows of `beats_baseline` and `seed_stability`). The grid is
    built before the first call; the declared point is read first, then the grid in order. Passes when every value is
    > 0; the grid is report-only unless it fails."""
    if not params:
        raise ValueError("param_shift: no declared parameter to shift")
    grid = shift_grid(params, shifts)          # fixed before any point is read
    base = float(evaluate(dict(params)))
    reasons = [] if np.isfinite(base) and base > 0 else [f"declared point net mean {base:+.4f} is not positive"]
    out: list[dict[str, Any]] = []
    for p in grid:
        v = float(evaluate(dict(p.point)))
        out.append({"param": p.param, "shift": p.shift, "value": p.value, "point": p.point, "net_mean": v})
        if not np.isfinite(v) or v <= 0:
            reasons.append(f"{p.param} {_label(p.shift)} ({p.value}): net mean {v:+.4f} is not positive")
    return QualityVerdict(check="param_shift", passed=not reasons, reasons=reasons,
                          detail={"declared": dict(params), "declared_net_mean": base,
                                  "shifts": [float(s) for s in shifts], "grid": out, "rows_key": rows,
                                  **_window_detail(OOF_WINDOW, None)})


# ---------------------------------------------------------------------------------------------- baseline
def mean_block_days(horizon_days: pd.Series | np.ndarray) -> int:
    """max(MIN_BLOCK_DAYS, ceil(1.5 x the longest label horizon in session days among the rows))."""
    h = np.asarray(horizon_days, dtype=float)
    if len(h) == 0 or not np.isfinite(h).all() or (h < 0).any():
        raise ValueError("beats_baseline: every row needs a finite, non-negative label horizon in session days")
    return max(MIN_BLOCK_DAYS, math.ceil(1.5 * float(h.max())))


def beats_baseline(model_net: pd.Series, baseline_net: pd.Series, days: pd.Series, horizon_days: pd.Series, *,
                   baseline_manifest_id: str, train_end: pd.Timestamp, fold_keys: Sequence[str], trial: int,
                   window: str = OOF_WINDOW, read_id: str | None = None, alpha: float = ALPHA, boot: int = BOOT,
                   seed: int = SEED) -> QualityVerdict:
    """Paired after-cost comparison of a model with the unit's C-family baseline on the same rows.

    `model_net`, `baseline_net`: net R after costs per decision row (0 where that model does not trade), on the
    learn-window walk-forward OOF rows (purged per `feature_store.purge`), indexed by the same row keys; `days`: each
    row's session day; `horizon_days`: each row's label horizon in session days (sets the block length). `train_end`,
    `fold_keys`: the folds both models were fitted on (the serving rule checks them against both manifests). `trial`:
    the registry trial booked for this read (K +1). d = mean(model - baseline); p = share of stationary day-block
    bootstrap draws with d <= 0. The pass here is unadjusted: `holm_adjust` over the A, B and D reads of the unit's
    cycle sets the verdict that serves."""
    from goldbot.research.model_zoo import JointBootstrap, _p
    if boot < BOOT:
        raise ValueError(f"beats_baseline: B = {boot} draws; at least {BOOT} (the card's minimum)")
    if trial < 1:
        raise ValueError("beats_baseline: book the read as a registry trial (K +1) and pass its number")
    win = _window_detail(window, read_id)
    idx = model_net.index
    if not (idx.equals(baseline_net.index) and idx.equals(days.index) and idx.equals(horizon_days.index)):
        raise ValueError("beats_baseline: the model and the baseline must be read on the same out-of-sample rows "
                         "(identical index, same order)")
    if not idx.is_unique:
        raise ValueError("beats_baseline: duplicate row keys")
    m, b = model_net.to_numpy(float), baseline_net.to_numpy(float)
    if not (np.isfinite(m).all() and np.isfinite(b).all()):
        raise ValueError("beats_baseline: every row needs a finite after-cost net value (0 for no trade)")
    block = mean_block_days(horizon_days)
    diff = m - b
    bs = JointBootstrap(days.astype(str).to_numpy(), boot=boot, seed=seed, mean_block=block)
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
                                  "boot": boot, "seed": seed, "mean_block_days": block, "trial": int(trial),
                                  "train_end": pd.Timestamp(train_end).tz_convert("UTC").isoformat(),
                                  "fold_keys": [str(k) for k in fold_keys], "rows_key": rows_key(idx),
                                  "n_rows": int(len(diff)), "n_days": int(len(bs.days)),
                                  "model_mean": float(m.mean()) if len(m) else None,
                                  "baseline_mean": float(b.mean()) if len(b) else None, **win})


def holm_adjust(verdicts: Mapping[str, QualityVerdict], *, group: str, alpha: float = ALPHA
                ) -> dict[str, QualityVerdict]:
    """Holm over the A, B and D `beats_baseline` reads of one unit in one cycle (`group`, e.g. "XAUUSD:15m|2027-01"):
    name -> verdict with `detail.holm` = {group, m, missing, p_holm} and `passed` = d > 0 and p_holm <= alpha.

    m is fixed at |BASELINE_REQUIRED| = 3 per unit and cycle; a read not passed in (its family fitted in another run,
    or not at all) counts as p = 1. Adjusting a lone read over m = 1 would let each family pass at the unadjusted
    alpha and the unit's family-wise false-pass rate rise to ~1 - 0.95^3 = 0.14."""
    if not verdicts or any(v.check != "beats_baseline" for v in verdicts.values()):
        raise ValueError("holm_adjust takes the beats_baseline verdicts of one unit and cycle")
    order = sorted(verdicts, key=lambda k: float(verdicts[k].detail["p"]))
    m, running, out = max(len(order), len(BASELINE_REQUIRED)), 0.0, {}
    missing = m - len(order)     # padded at p = 1: they sort last, so they only raise the earlier ranks' multipliers
    for i, k in enumerate(order):
        v = verdicts[k]
        running = max(running, min(1.0, (m - i) * float(v.detail["p"])))
        reasons = [r for r in v.reasons if not r.startswith("p ")]
        if running > alpha:
            reasons.append(f"Holm p {running:.4f} > {alpha} over {m} reads of {group}")
        out[k] = QualityVerdict(check="beats_baseline", passed=not reasons, reasons=reasons,
                                detail={**v.detail, "holm": {"group": group, "m": m, "missing": missing,
                                                             "p_holm": running}})
    return out


# ---------------------------------------------------------------------------------------------- serving rule
def servable_reasons(manifest: RunManifest, *, lookup: Callable[[str], RunManifest]) -> list[str]:
    """Why `manifest`'s model may not serve (empty: it may). `lookup(manifest_id)` loads another manifest (the
    baseline's) and raises when it cannot."""
    out: list[str] = []
    if manifest.code_dirty:
        out.append("trained from a dirty tree: it cannot be rebuilt exactly (ADR 0106 decision 7)")
    if manifest.seeds != seed_list(manifest.manifest_key, len(manifest.seeds)):
        out.append("seeds are not seed_list(manifest_key): seeds must be fixed by the key before the first fit")
    if len(manifest.seeds) < MIN_SEEDS:
        out.append(f"{len(manifest.seeds)} seeds recorded; at least {MIN_SEEDS} are required")
    for v in manifest.quality:
        if not v.passed:
            out.append(f"{v.check} failed: {'; '.join(v.reasons)}")
        if v.check in ("seed_stability", "param_shift") and v.detail.get("window") != OOF_WINDOW:
            out.append(f"{v.check} was not read on the learn-window walk-forward OOF rows")
    keys = {str(v.detail.get("rows_key")) for v in manifest.quality}
    if len(keys) > 1:
        out.append("the quality verdicts were read on different rows (one rows_key required)")
    seeds = [v for v in manifest.quality if v.check == "seed_stability" and v.passed]
    if not seeds:
        out.append(f"no passing seed_stability verdict (at least {MIN_SEEDS} seeds, every one on the pooled edge's "
                   "side)")
    elif any(sorted(v.detail.get("seeds", [])) != sorted(manifest.seeds) for v in seeds):
        out.append(f"seed_stability verdict seeds differ from the manifest's seeds {sorted(manifest.seeds)}")
    if manifest.family in BASELINE_REQUIRED:
        out += _baseline_reasons(manifest, lookup)
    return out


def _baseline_reasons(manifest: RunManifest, lookup: Callable[[str], RunManifest]) -> list[str]:
    reads = [v for v in manifest.quality if v.check == "beats_baseline"]
    wins = [v for v in reads if v.passed and "holm" in v.detail]
    if not wins:
        return [f"family {manifest.family} needs a Holm-adjusted passing beats_baseline verdict against the unit's "
                "C-family baseline on the same out-of-sample rows after costs (ADR 0106 decision 3)"]
    out: list[str] = []
    end = manifest.train_end.isoformat()
    for v in wins:
        hm = int((v.detail.get("holm") or {}).get("m", 0))
        if hm < len(BASELINE_REQUIRED):
            out.append(f"beats_baseline Holm m {hm} < {len(BASELINE_REQUIRED)}: the adjustment must count the A, B "
                       "and D reads of the unit's cycle (a missing read at p = 1)")
        bid = str(v.detail.get("baseline_manifest_id", ""))
        if v.detail.get("train_end") != end or list(v.detail.get("fold_keys", [])) != manifest.fold_keys:
            out.append("beats_baseline was read on other folds or another train_end than the model's")
        try:
            base = lookup(bid)
        except Exception as exc:   # noqa: BLE001 - any failure to read the baseline refuses the model
            out.append(f"baseline manifest {bid[:12]} cannot be read: {type(exc).__name__}: {exc}")
            continue
        if base.family != BASELINE_FAMILY:
            out.append(f"baseline {bid[:12]} is family {base.family}, not a C-family gradient-boosted model")
        if base.unit != manifest.unit:
            out.append(f"baseline {bid[:12]} is unit {base.unit!r}, not the model's {manifest.unit!r}")
        if base.train_end != manifest.train_end or base.fold_keys != manifest.fold_keys:
            out.append(f"baseline {bid[:12]} was fitted with another train_end or other folds than the model")
    return out


def require_servable(manifest: RunManifest, *, lookup: Callable[[str], RunManifest]) -> None:
    """Raise ModelRefused unless `servable_reasons` is empty. Engine loaders call this before a model scores."""
    reasons = servable_reasons(manifest, lookup=lookup)
    if reasons:
        raise ModelRefused(f"{manifest.model} ({manifest.manifest_id[:12]}) may not serve: " + "; ".join(reasons))

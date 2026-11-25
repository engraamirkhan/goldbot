"""obj-v1, the one objective of record for every trade-filter model (docs/decisions/0098-objective-of-record-obj-v1.md
and its 2026-11-27 amendment, rulings R-H and R-I; ML lifecycle plan 2026-11-25 items N1 and N5).

"The **same** measure is used for training, threshold, selection and the gate." This module is that measure, once.
sr-v3, zoo-v2, OMB-1 and H-102 call it; the legacy statistics (self_retrain sr-v2, model_zoo zoo-v1, gates.py) keep
their signed objectives and stay reporting-only beside it.

The pieces, as the ADR words them:
- **Clip.** "Clip at ±10 R in every use. Rows beyond ±10 R enter every statistic at ±10 R; quarantine is an audit flag,
  never an exclusion. £ reports use unclipped net R." (`clip_r`, `quarantine_count`, `ObjectiveRead.gbp_per_day`)
- **Fit (R-I, primary).** "The learner predicts E[gross R | x] (clipped at ±10 R; uniqueness weights only,
  squared-error loss; cost(x) is the ub95 cost of record known at decision time, never the realised cost), the cost of
  record cost(x) enters as a known offset, and a candidate is kept iff E[gross R | x] − cost(x) > 0." (`fit_offset`,
  `keep_rule`). Fixed regularisation, no inner selection (library defaults that select on their own metric are banned).
- **Abstention (D2).** "No keep floor. MIN_KEPT = 100 out-of-fold kept candidates and >= 30 kept days. ABSTAIN is
  decided from kept counts before any out-of-fold net-R statistic is computed." An abstained unit keeps nothing, so it
  counts 0 R per candidate.
- **The statistic (D3).** "S = mean(net R x keep) − max(0, mean(net R)), per candidate, day-block bootstrap", the
  max(0, ·) taken per judged unit (R-M), never per arm.
- **Gate legs.** "S ≥ +0.05 R with lower bound > 0, kept mean > 0, gross ≥ 0, a side-matched control leg"; R-H: "the
  2x cost stress row must keep S ≥ 0". Deflation at the K of record and the two consecutive judged reads are the
  caller's (the registry), not this module's.
- **Decomposition (R-I).** "Every read reports S split into gross skill (kept gross − all gross) and cost avoidance
  (the rest)." The rest is split once more so the three parts add up exactly (`Decomposition`).
- **Weights (section 3, N5).** Kish n_eff = (Σw)²/Σw². Fit weights are normalised to sum to n_eff / H, where H is the
  overlap design effect the caller states (H = P for P phase copies of one label). The ridge penalty λ0 is a prior worth
  λ0 effective rows per standardised coefficient (`fit_offset`): weights x 10 give the identical fit, P phase copies at
  1/P with H = P reproduce the real-only fit, and an overlap H scales the penalty by H (ADR 0102 records why this, and
  not the plan's C_fold = 1/(λ0 · n_eff), is the unit convention).

Guard: AUC, accuracy, win rate and *unweighted* log-loss "are banned from selection, threshold and gate, and are
reported as diagnostics only" (`research/diagnostics.py`). `guard_violations` checks every module in
`GUARDED_MODULES` and the goldbot modules it imports; a new fit/threshold/select/gate module adds itself there.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

# ---------------------------------------------------------------------------------------------- the frozen spec
OBJECTIVE_ID: Literal["obj-v1"] = "obj-v1"
CLIP_R = 10.0
MIN_KEPT = 100              # out-of-fold kept candidates, as self_retrain.MIN_KEPT
MIN_KEPT_DAYS = 30          # kept session days
MIN_S_R = 0.05              # the gate's S per candidate
STRESS_COST_MULT = 2.0      # R-H: S must stay >= 0 at 2x the cost leg
BOOT = 10_000
SEED = 20261127
MEAN_BLOCK_DAYS = 5         # stationary bootstrap over session days (as model_zoo.JointBootstrap)
LOWER_Q = 0.05              # one-sided 95 % lower bound
_BOOT_CHUNK = 500           # draws held in memory at once

# modules on a fit, threshold, select or gate path (each new one adds itself; diagnostics.py must never be here)
GUARDED_MODULES: tuple[str, ...] = ("goldbot.research.objective", "goldbot.research.baselines")
# names that may not appear on those paths, nor in any goldbot module they import
BANNED_NAMES: frozenset[str] = frozenset({
    "roc_auc_score", "roc_curve", "auc", "accuracy_score", "balanced_accuracy_score", "log_loss", "brier_score_loss",
    "win_rate", "within_arm_auc", "LogisticRegressionCV", "CalibratedClassifierCV", "early_stopping",
})
BANNED_MODULES: frozenset[str] = frozenset({"sklearn.metrics", "sklearn.calibration", "goldbot.research.diagnostics",
                                            "goldbot.research.metrics"})


# ---------------------------------------------------------------------------------------------- clip and weights
def clip_r(r: np.ndarray | pd.Series | Sequence[float]) -> np.ndarray:
    """R clipped at ±CLIP_R ("every statistic uses net R clipped at ±10 R")."""
    return np.clip(np.asarray(r, dtype=float), -CLIP_R, CLIP_R)


def quarantine_count(r: np.ndarray | pd.Series | Sequence[float]) -> int:
    """Rows beyond ±CLIP_R: an audit flag, never an exclusion."""
    return int((np.abs(np.asarray(r, dtype=float)) > CLIP_R).sum())


def uniqueness_weights(t0: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """Average uniqueness of each label (López de Prado 2018, ch. 4): the mean over the label's life [t0, t1) of
    1 / (labels open at that instant). Times are int64 epoch ns (`timeutil.epoch_ns`). A zero-length label takes
    1 / (labels open at its instant + zero-length labels at that instant)."""
    a = np.asarray(t0, dtype=np.int64)
    b = np.asarray(t1, dtype=np.int64)
    if len(a) == 0:
        return np.zeros(0)
    if (b < a).any():
        raise ValueError("uniqueness_weights: a label ends before it starts")
    pts = np.unique(np.concatenate([a, b]))
    open_at = np.searchsorted(np.sort(a), pts, side="right") - np.searchsorted(np.sort(b), pts, side="right")
    seg = np.diff(pts).astype(float)
    inv = np.where(open_at[:-1] > 0, 1.0 / np.maximum(open_at[:-1], 1), 0.0)
    cum = np.concatenate([[0.0], np.cumsum(seg * inv)])
    i0, i1 = np.searchsorted(pts, a), np.searchsorted(pts, b)
    dur = (b - a).astype(float)
    zero = dur == 0
    out = np.empty(len(a))
    out[~zero] = (cum[i1[~zero]] - cum[i0[~zero]]) / dur[~zero]
    if zero.any():
        zt, zc = np.unique(a[zero], return_counts=True)
        z_at = zc[np.searchsorted(zt, a[zero])]
        out[zero] = 1.0 / (open_at[i0[zero]] + z_at)
    return out


def kish_n_eff(w: np.ndarray | Sequence[float]) -> float:
    """Kish effective sample size (Σw)² / Σw²; 0 for no weight."""
    w = np.asarray(w, dtype=float)
    ss = float((w * w).sum())
    return float(w.sum()) ** 2 / ss if ss > 0 else 0.0


def normalise_weights(w: np.ndarray | Sequence[float], design_effect: float = 1.0) -> np.ndarray:
    """Weights rescaled to sum to kish_n_eff(w) / design_effect (N5): the data term of a fit counts effective rows,
    so its scale never depends on how the caller happened to scale the weights."""
    w = np.asarray(w, dtype=float)
    if (w < 0).any() or not np.isfinite(w).all():
        raise ValueError("normalise_weights: weights must be finite and >= 0")
    if design_effect < 1.0:
        raise ValueError("normalise_weights: the overlap design effect is >= 1")
    s = float(w.sum())
    return w * (kish_n_eff(w) / design_effect / s) if s > 0 else w.copy()


# ---------------------------------------------------------------------------------------------- the offset-form fit
class OffsetModel(FrozenRecord):
    """A fitted obj-v1 offset-form filter: E[gross R | x] by weighted ridge on standardised inputs. Loading refuses an
    artifact without `objective_id` or with another id (ADR 0098 section 4)."""
    objective_id: Literal["obj-v1"]
    features: tuple[str, ...]
    impute: tuple[float, ...]       # per-column median of the training rows (NaN inputs take it)
    center: tuple[float, ...]       # weighted mean after imputation
    scale: tuple[float, ...]        # weighted SD after imputation (1 where constant)
    coef: tuple[float, ...]         # per standardised column, gross R per SD
    intercept: float
    lam0: float
    design_effect: float
    n_rows: int
    n_eff: float                    # Kish n_eff of the raw fit weights

    def predict_gross(self, X: np.ndarray | pd.DataFrame) -> np.ndarray:
        """E[gross R | x] for each row."""
        return self.intercept + _standardise(_matrix(X, self.features), self) @ np.asarray(self.coef)

    def keep(self, X: np.ndarray | pd.DataFrame, cost: np.ndarray) -> np.ndarray:
        """The obj-v1 keep rule on these rows at their known ub95 cost."""
        return keep_rule(self.predict_gross(X), cost)


def _matrix(X: np.ndarray | pd.DataFrame, features: tuple[str, ...] | None = None) -> np.ndarray:
    if isinstance(X, pd.DataFrame):
        X = X[list(features)] if features else X
        return X.to_numpy(dtype=float)
    M = np.asarray(X, dtype=float)
    return M.reshape(-1, 1) if M.ndim == 1 else M


def _standardise(M: np.ndarray, m: OffsetModel) -> np.ndarray:
    M = np.where(np.isnan(M), np.asarray(m.impute), M)
    return (M - np.asarray(m.center)) / np.asarray(m.scale)


def fit_offset(X: np.ndarray | pd.DataFrame, gross_r: np.ndarray, weights: np.ndarray, *, lam0: float,
               design_effect: float = 1.0) -> OffsetModel:
    """R-I's primary form: minimise Σ w̃ (clip(gross R) − b − zβ)² + λ0 ||β||², w̃ = `normalise_weights(weights,
    design_effect)` (Σw̃ = n_eff / H effective rows), z the inputs median-imputed and standardised with the weights,
    the intercept unpenalised. λ0 is fixed in the card before data; nothing here selects it."""
    if lam0 < 0:
        raise ValueError("fit_offset: lam0 >= 0")
    features = tuple(str(c) for c in X.columns) if isinstance(X, pd.DataFrame) else ()
    M = _matrix(X)
    y = clip_r(gross_r)
    w = np.asarray(weights, dtype=float)
    if not (len(M) == len(y) == len(w)) or len(y) == 0:
        raise ValueError("fit_offset: X, gross_r and weights need the same, non-zero length")
    wn = normalise_weights(w, design_effect)
    impute = np.array([float(np.nanmedian(c)) if np.isfinite(c).any() else 0.0 for c in M.T])
    M = np.where(np.isnan(M), impute, M)
    sw = float(wn.sum())
    center = (wn @ M) / sw
    sd = np.sqrt((wn @ (M - center) ** 2) / sw)
    scale = np.where(sd > 0, sd, 1.0)
    Z = np.column_stack([np.ones(len(M)), (M - center) / scale])
    A = Z.T @ (Z * wn[:, None])
    A[1:, 1:] += lam0 * np.eye(Z.shape[1] - 1)
    theta = np.linalg.solve(A, Z.T @ (wn * y))
    return OffsetModel(objective_id=OBJECTIVE_ID, features=features, impute=tuple(impute.tolist()),
                       center=tuple(center.tolist()), scale=tuple(scale.tolist()), coef=tuple(theta[1:].tolist()),
                       intercept=float(theta[0]), lam0=float(lam0), design_effect=float(design_effect),
                       n_rows=len(y), n_eff=kish_n_eff(w))


def keep_rule(pred_gross: np.ndarray, cost: np.ndarray) -> np.ndarray:
    """Keep iff E[gross R | x] − cost(x) > 0 (strict; full abstention allowed, no keep floor)."""
    return (np.asarray(pred_gross, dtype=float) - np.asarray(cost, dtype=float)) > 0


def net_logit_weights(net_r: np.ndarray, uniqueness: np.ndarray) -> np.ndarray:
    """The single named alternative's weights (ADR 0098 section 2): uniqueness x min(|net R|, 10), with
    y = 1[net R > 0]. Used only if the offset form is NOT STARTED, never in parallel (quant ruling, review of #458)."""
    return np.asarray(uniqueness, dtype=float) * np.abs(clip_r(net_r))


# ---------------------------------------------------------------------------------------------- the read
class Decomposition(FrozenRecord):
    """S = gross_skill + cost_avoidance + base, per candidate (q = keep share):
    gross_skill = q (kept gross − all gross), cost_avoidance = q (all cost − kept cost),
    base = q · mean net − max(0, mean net) (what keeping a random q-share of the unit would earn over the bar)."""
    gross_skill: float
    cost_avoidance: float
    base: float


class ObjectiveRead(FrozenRecord):
    """One obj-v1 read of a judged unit. With `abstain` the keep vector was zeroed BEFORE any statistic, so `s` is
    −max(0, mean net) and `kept_mean` is None."""
    objective_id: Literal["obj-v1"] = OBJECTIVE_ID
    n: int
    n_days: int
    n_kept: int                     # as decided, before abstention
    n_kept_days: int
    abstain: bool
    keep_share: float               # after abstention
    mean_net: float
    mean_gross: float
    kept_mean: float | None
    kept_gross_minus_all: float | None
    s: float
    s_lo: float | None              # day-block one-sided 95 % lower bound (None: boot = 0)
    s_stress: float                 # S with the cost leg x STRESS_COST_MULT, same keep unless a stress keep was given
    decomposition: Decomposition
    n_eff: float | None             # Kish n_eff of the uniqueness weights, if given
    quarantined: int
    gbp_per_day: float | None


def abstains(keep: np.ndarray, day: np.ndarray) -> bool:
    """ABSTAIN from counts alone: fewer than MIN_KEPT kept candidates or fewer than MIN_KEPT_DAYS kept days."""
    k = np.asarray(keep, dtype=bool)
    return int(k.sum()) < MIN_KEPT or len(np.unique(np.asarray(day)[k])) < MIN_KEPT_DAYS


def abstain_groups(keep: np.ndarray, day: np.ndarray, group: np.ndarray) -> np.ndarray:
    """The keep with every group (arm, cell) that abstains by `abstains` zeroed: an abstained arm keeps nothing and
    counts 0 R per candidate in the unit's S (the unit's own MIN_KEPT check still follows, in `read`)."""
    k = np.asarray(keep, dtype=bool).copy()
    d, g = np.asarray(day), np.asarray(group).astype(str)
    for name in np.unique(g):
        m = g == name
        if abstains(k[m], d[m]):
            k[m] = False
    return k


def s_statistic(net: np.ndarray, keep: np.ndarray) -> float:
    """S = mean(net R x keep) − max(0, mean net R) over the unit's candidates (net already clipped)."""
    return float((net * keep).mean() - max(0.0, float(net.mean())))


def read(net_r: np.ndarray, gross_r: np.ndarray, keep: np.ndarray, day: np.ndarray, *,
         weights: np.ndarray | None = None, gbp_per_r: float | None = None, stress_keep: np.ndarray | None = None,
         boot: int = BOOT, seed: int = SEED, mean_block: int = MEAN_BLOCK_DAYS) -> ObjectiveRead:
    """The obj-v1 read of one judged unit. `net_r` and `gross_r` are unclipped R per candidate (cost = gross − net is
    the cost leg charged); `day` the session day; `keep` the decided keep. Abstention first, from counts."""
    raw = np.asarray(net_r, dtype=float)
    net, gross = clip_r(raw), clip_r(gross_r)
    day = np.asarray(day).astype(str)
    k_dec = np.asarray(keep, dtype=bool)
    if not (len(net) == len(gross) == len(k_dec) == len(day)) or len(net) == 0:
        raise ValueError("read: net_r, gross_r, keep and day need the same, non-zero length")
    n_kept, n_kept_days = int(k_dec.sum()), len(np.unique(day[k_dec]))
    ab = abstains(k_dec, day)
    k = np.zeros(len(net), dtype=bool) if ab else k_dec
    kf = k.astype(float)
    cost = gross - net
    q = float(kf.mean())
    s = s_statistic(net, kf)
    mean_net = float(net.mean())
    dec = Decomposition(
        gross_skill=q * (float(gross[k].mean()) - float(gross.mean())) if k.any() else 0.0,
        cost_avoidance=q * (float(cost.mean()) - float(cost[k].mean())) if k.any() else 0.0,
        base=q * mean_net - max(0.0, mean_net))
    ks = k if stress_keep is None else (np.zeros(len(net), bool) if abstains(stress_keep, day)
                                        else np.asarray(stress_keep, dtype=bool))
    s_stress = s_statistic(clip_r(gross - STRESS_COST_MULT * cost), ks.astype(float))
    s_lo = None
    if boot > 0:
        draws = _boot_s(net, kf, day, boot=boot, seed=seed, mean_block=mean_block)
        s_lo = float(np.quantile(draws, LOWER_Q))
    n_days = len(np.unique(day))
    return ObjectiveRead(
        n=len(net), n_days=n_days, n_kept=n_kept, n_kept_days=n_kept_days, abstain=ab, keep_share=q,
        mean_net=mean_net, mean_gross=float(gross.mean()), kept_mean=float(net[k].mean()) if k.any() else None,
        kept_gross_minus_all=float(gross[k].mean() - gross.mean()) if k.any() else None, s=s, s_lo=s_lo,
        s_stress=s_stress, decomposition=dec, n_eff=None if weights is None else kish_n_eff(weights),
        quarantined=quarantine_count(raw),
        gbp_per_day=None if gbp_per_r is None else float((raw * kf).sum()) / n_days * gbp_per_r)


# ---------------------------------------------------------------------------------------------- day-block bootstrap
def _day_weights(n_days: int, *, boot: int, seed: int, mean_block: int) -> Iterator[np.ndarray]:
    """Stationary bootstrap (Politis-Romano 1994) over session days, circular, mean block `mean_block`: yields
    (chunk x n_days) day-count matrices, `boot` draws in all, seeded and reproducible."""
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    done = 0
    while done < boot:
        b = min(_BOOT_CHUNK, boot - done)
        idx = np.empty((b, n_days), dtype=np.int64)
        idx[:, 0] = rng.integers(n_days, size=b)
        for t in range(1, n_days):
            jump = rng.random(b) < p
            idx[:, t] = np.where(jump, rng.integers(n_days, size=b), (idx[:, t - 1] + 1) % n_days)
        flat = (np.arange(b)[:, None] * n_days + idx).ravel()
        yield np.bincount(flat, minlength=b * n_days).reshape(b, n_days).astype(float)
        done += b


def _day_sums(day: np.ndarray, *cols: np.ndarray) -> tuple[int, list[np.ndarray]]:
    _, inv = np.unique(day, return_inverse=True)
    nd = int(inv.max()) + 1
    return nd, [np.bincount(inv, weights=c, minlength=nd) for c in cols]


def _boot_s(net: np.ndarray, kf: np.ndarray, day: np.ndarray, *, boot: int, seed: int, mean_block: int) -> np.ndarray:
    nd, (cnt, s_all, s_kept) = _day_sums(day, np.ones(len(net)), net, net * kf)
    out = []
    for W in _day_weights(nd, boot=boot, seed=seed, mean_block=mean_block):
        n = W @ cnt
        out.append((W @ s_kept) / n - np.maximum(0.0, (W @ s_all) / n))
    return np.concatenate(out)


def paired_delta(net_r: np.ndarray, keep_a: np.ndarray, keep_b: np.ndarray, day: np.ndarray, *, boot: int = BOOT,
                 seed: int = SEED, mean_block: int = MEAN_BLOCK_DAYS) -> tuple[float, float]:
    """(S(a) − S(b), its one-sided 95 % lower bound) on the same rows with identical day draws. The max(0, ·) terms
    cancel, so this is mean(net x (keep_a − keep_b)). Keeps are used as given (abstain them first)."""
    net = clip_r(net_r)
    d = net * (np.asarray(keep_a, dtype=float) - np.asarray(keep_b, dtype=float))
    d0 = float(d.mean())
    nd, (cnt, sd) = _day_sums(np.asarray(day).astype(str), np.ones(len(net)), d)
    draws = np.concatenate([(W @ sd) / (W @ cnt) for W in _day_weights(nd, boot=boot, seed=seed,
                                                                         mean_block=mean_block)])
    return d0, float(np.quantile(draws, LOWER_Q))


# ---------------------------------------------------------------------------------------------- the gate
class GateVerdict(FrozenRecord):
    """Every leg named; `passed` only when all hold. The K-of-record deflation and the two consecutive judged reads
    of >= 100 unseen rows are applied by the registry on top of this."""
    objective_id: Literal["obj-v1"] = OBJECTIVE_ID
    passed: bool
    legs: dict[str, bool]
    reasons: tuple[str, ...]


def gate(rd: ObjectiveRead, *, control_kept_mean: float | None) -> GateVerdict:
    """ADR 0098 section 2 + R-H: not abstained; S >= MIN_S_R with a lower bound > 0; kept mean > 0; gross (kept gross
    − all gross) >= 0; kept mean beats the side-matched control's mean net R; 2x-cost S >= 0. No control or no lower
    bound fails closed."""
    legs = {
        "not_abstained": not rd.abstain,
        "s_min": rd.s >= MIN_S_R,
        "s_lower_bound": rd.s_lo is not None and rd.s_lo > 0,
        "kept_mean": rd.kept_mean is not None and rd.kept_mean > 0,
        "gross": rd.kept_gross_minus_all is not None and rd.kept_gross_minus_all >= 0,
        "control": (control_kept_mean is not None and rd.kept_mean is not None
                    and rd.kept_mean > control_kept_mean),
        "cost_stress": rd.s_stress >= 0,
    }
    return GateVerdict(passed=all(legs.values()), legs=legs, reasons=tuple(k for k, ok in legs.items() if not ok))


# ---------------------------------------------------------------------------------------------- the import guard
def guard_violations_in_source(src: str, where: str) -> list[str]:
    """Banned names or modules imported or referenced in this source (AST, so strings and comments are free)."""
    out: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in BANNED_MODULES:
                out.append(f"{where}:{node.lineno} imports {mod}")
            for a in node.names:
                full = f"{mod}.{a.name}"
                if a.name in BANNED_NAMES or full in BANNED_MODULES:
                    out.append(f"{where}:{node.lineno} imports {full}")
        elif isinstance(node, ast.Import):
            out += [f"{where}:{node.lineno} imports {a.name}" for a in node.names if a.name in BANNED_MODULES]
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_NAMES:
            out.append(f"{where}:{node.lineno} uses .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            out.append(f"{where}:{node.lineno} uses {node.id}")
    return out


def _goldbot_imports(src: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("goldbot"):
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names if a.name.startswith("goldbot"))
    return mods


def guard_violations(module: str) -> list[str]:
    """Violations in `module` and, transitively, every goldbot module it imports."""
    out: list[str] = []
    seen: set[str] = set()
    todo = [module]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in BANNED_MODULES and name != module:
            out.append(f"{module} reaches {name}")
            continue
        spec = _find(name)
        if spec is None or spec.origin is None or not spec.origin.endswith(".py"):
            continue
        src = Path(spec.origin).read_text()
        out += guard_violations_in_source(src, spec.origin)
        todo += sorted(_goldbot_imports(src) - seen)
    return out


def _find(name: str) -> importlib.machinery.ModuleSpec | None:
    """The module's spec, or None for a name that is not a module (an imported class or function)."""
    try:
        return importlib.util.find_spec(name)
    except (ModuleNotFoundError, ValueError):
        return None

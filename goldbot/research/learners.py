"""The sr-v3 / zoo-v2 learner framework: one interface, pre-named learners, frozen JSON artifacts with a sha
(SYNTH-ml 2026-11-25 rulings R2, R4, items 3 and 10; ADR 0106 "Model quality"; card sr-v3 3.3-3.6).

Interface. Every learner is fitted on a `TrainData` (raw inputs, clipped gross R, decision-time cost(x), net R,
uniqueness weights, instrument, session day, tf, the overlap design effect) and returns a FrozenRecord artifact:
- `value(...)` on a batch and `value_row(...)` on one vector, with the same arithmetic (row parity, tested);
- `kind`: "gross" (E[gross R | x]; keep iff value − cost(x) > 0, the obj-v1 offset rule R-I) or "p" (a cost-weighted
  probability; keep iff p_w >= 0.5, i.e. E[net R] >= 0: SYNTH R2 "new cards keep a candidate when p_w >= 0.5",
  Zadrozny 2003);
- `artifact_json` / `artifact_sha`: canonical JSON (no pickle, ADR 0010) and its SHA-256. A fit is deterministic:
  the same inputs give the same bytes (card test 14).

The learners (pre-named, "no hyperparameter grids"):
- **L-cw**, the sr-v3 body (card 3.3): ĝ = a_unit + a_instrument + βᵀz, z the inputs median-imputed and standardised
  with the training rows only, loss Σ w̃ (clip(g, ±10) − ĝ)² + λ‖β‖² + λ_a Σ a_instrument², λ = 540, λ_a = 1.35/τ_a²,
  the unit intercept unpenalised, closed form in float64, w̃ = `objective.normalise_weights` (Σw̃ = Kish n_eff / H).
  Fitted from additive `Moments`, so a unit's years can be streamed and the in-memory fit is the same numbers.
- **G-cw**, GBT-slow (SYNTH item 10): LightGBM, depth 2, learning rate 0.01, 400 trees, min_child max(200, 2 % of
  rows), monotone increasing in range-to-cost, NaN native, y = 1[net R > 0] with sample_weight = u × min(|net R|, 10)
  (`objective.net_logit_weights`, R1). ADR 0106: "every deep model must beat the gradient-boosted baseline out of
  sample after costs"; G-cw is that baseline here (paired D vs G-cw in every training report).
- **HB**, hierarchical over the tf ladder (ml-6 P3 + ml-7 P4): per (tf, class) cell β = θ_tf + γ_tf,class, a Gaussian
  random walk θ_tf − θ_tf−1 ~ N(0, σ²/λ_rw), θ_first ~ N(0, σ²/λ0), class deviations γ ~ N(0, σ²/λ_c), the cell
  intercept unpenalised, the same squared-error offset loss. With a Gaussian likelihood the Laplace approximation is
  exact: the MAP is one linear solve and `coef_sd` is the posterior SD at σ² = 1.35 R².
- **AVG**, equal-weight rank fusion (R4): each base's score is mapped to its empirical CDF on the calibration rows
  (offset bases rank E[net R] = value − cost(x)), the ranks are averaged with equal weights, and a weighted isotonic
  calibrator on real OOF rows maps the fused rank to p_w (keep iff p_w >= 0.5). Fitted weights (the stack FS) stay
  off until the diversity read (R4: mean pairwise rho < 0.90 and F2 beats F1).
- **D-40** dense prior (ml-10 §3): two LightGBM heads on the unit's dense bars give `dense_p_side` = P(T2 gross > 0 |
  x, side) and `dense_range_cost` = E[|T0 gross R| | x] / cost(x). Cross-fitted by the trainer (a row's columns come
  from a fit that never saw its label). They are inputs of G-cw only: the L-cw body is the card's prefix, HB's ladder
  shares one column set.
- Slots (registered, not built): **B6** TabPFN v2 (R5, SYNTH item 16, conditional), **SEQ-1** pooled TCN (R6, item 17,
  conditional; built by another lane). **FS** (fitted stack) is off.

Calibrators: weighted Platt (Newton, deterministic) and weighted isotonic (pool-adjacent-violators). "Unweighted
calibrators on weighted models" are on the stop-list (SYNTH 3). This module is on the obj-v1 guard: it never computes
AUC, accuracy, log-loss or a win rate (the diagnostics live in research/diagnostics.py and the trainer's report).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import numpy as np

from goldbot.base import FrozenRecord
from goldbot.research import objective as O
from goldbot.research.sr_v3 import instrument_class

Kind = Literal["gross", "p"]
RANGE_TO_COST = "range_to_cost"
DENSE_COLUMNS: tuple[str, ...] = ("dense_p_side", "dense_range_cost")
RESIDUAL_VAR = 1.35
GBT_THREADS = 1                  # pinned: the artifact's bytes may depend on the thread count
GCW_PARAMS: dict[str, Any] = {"objective": "binary", "max_depth": 2, "num_leaves": 4, "learning_rate": 0.01,
                              "num_iterations": 400, "min_child_floor": 200, "min_child_share": 0.02,
                              "feature_fraction": 1.0, "bagging_fraction": 1.0, "lambda_l2": 0.0}
DENSE_PARAMS: dict[str, Any] = {"num_leaves": 15, "min_data_in_leaf": 2_000, "learning_rate": 0.03,
                                "num_iterations": 300}
AVG_REF_POINTS = 1_001


class NotBuilt(NotImplementedError):
    """A registered learner slot whose card has not run (B6, SEQ-1) or a learner switched off (FS)."""


class LearnerSpec(FrozenRecord):
    id: str
    kind: Kind | None
    status: Literal["built", "off", "slot"]
    family: str                      # ADR 0106 model family
    note: str


LEARNERS: dict[str, LearnerSpec] = {
    "L-cw": LearnerSpec(id="L-cw", kind="gross", status="built", family="linear (sr-v3 body)",
                        note="card 3.3 ridge body with instrument levels; the H-102 base; Stage 0 Riley sizes it"),
    "G-cw": LearnerSpec(id="G-cw", kind="p", status="built", family="C gradient-boosted trees",
                        note="GBT-slow, the baseline every model must beat out of sample after costs (ADR 0106)"),
    "HB": LearnerSpec(id="HB", kind="gross", status="built", family="hierarchical Bayes",
                      note="random walk along the tf ladder plus class deviations, Gaussian (exact Laplace)"),
    "AVG": LearnerSpec(id="AVG", kind="p", status="built", family="G ensemble",
                       note="equal-weight rank fusion of the built bases, weighted isotonic calibrator"),
    "FS": LearnerSpec(id="FS", kind="p", status="off", family="G ensemble",
                      note="fitted stack: +1 K only if the H-101 P0 diversity read shows mean rho < 0.90 (R4)"),
    "B6": LearnerSpec(id="B6", kind="gross", status="slot", family="A deep (tabular foundation)",
                      note="TabPFN v2, inference only, conditional (R5, item 16): licence, torch, timing"),
    "SEQ-1": LearnerSpec(id="SEQ-1", kind="gross", status="slot", family="A deep (sequence)",
                         note="pooled TCN, conditional on the R-20 ruling (R6, item 17); another lane builds it"),
}


def fit_slot(learner: str) -> None:
    spec = LEARNERS[learner]
    raise NotBuilt(f"{learner} is {spec.status}: {spec.note}")


# ---------------------------------------------------------------------------------------------- data
@dataclass(frozen=True)
class TrainData:
    """One learner's rows. X: raw inputs (NaN allowed) in `columns` order; gross: gross R (clipped at use); net: net R
    after the decision-time cost(x); w: uniqueness weights (a phase row's u / P); design_effect: the overlap H."""
    X: np.ndarray
    columns: tuple[str, ...]
    gross: np.ndarray
    net: np.ndarray
    cost: np.ndarray
    w: np.ndarray
    instrument: np.ndarray
    day: np.ndarray
    tf: np.ndarray
    design_effect: float

    def subset(self, mask: np.ndarray, **update: Any) -> TrainData:
        m = np.asarray(mask, dtype=bool)
        sliced = {f: getattr(self, f)[m] for f in ("X", "gross", "net", "cost", "w", "instrument", "day", "tf")}
        return replace(self, **{**sliced, **update})


def _as_matrix(X: np.ndarray, p: int) -> np.ndarray:
    M = np.asarray(X, dtype=float)
    M = M.reshape(-1, p) if M.ndim == 1 else M
    return np.where(np.isinf(M), np.nan, M)


def _rowdot(Z: np.ndarray, coef: np.ndarray) -> np.ndarray:
    """Z·coef per row with a per-row summation, so a one-row batch gives the same bits as that row in any batch (a
    BLAS matrix-vector product may block differently by batch size: row parity failed on Linux runners)."""
    return (Z * coef).sum(axis=1)


def medians(X: np.ndarray) -> np.ndarray:
    """Per-column median of the finite training values (0 where a column has none)."""
    M = _as_matrix(X, np.asarray(X).shape[1] if np.asarray(X).ndim == 2 else 1)
    return np.array([float(np.nanmedian(c)) if np.isfinite(c).any() else 0.0 for c in M.T])


def keep(kind: Kind, value: np.ndarray, cost: np.ndarray) -> np.ndarray:
    """The served rule: offset learners keep iff E[gross R | x] − cost(x) > 0 (strict, R-I); probability learners
    keep iff p_w >= 0.5 (E[net R] >= 0, R2)."""
    if kind == "gross":
        return O.keep_rule(value, cost)
    return np.asarray(value, dtype=float) >= 0.5


def artifact_json(a: FrozenRecord) -> str:
    return a.model_dump_json()


def artifact_sha(a: FrozenRecord) -> str:
    return hashlib.sha256(artifact_json(a).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- moments, L-cw
class Moments(FrozenRecord):
    """Additive weighted moments of one instrument's rows on imputed raw inputs x and y = clip(gross R)."""
    n: int
    s0: float
    sww: float
    s1: tuple[float, ...]
    s2: tuple[tuple[float, ...], ...]
    sy: float
    sxy: tuple[float, ...]

    def __add__(self, o: Moments) -> Moments:
        return Moments(n=self.n + o.n, s0=self.s0 + o.s0, sww=self.sww + o.sww,
                       s1=tuple((np.asarray(self.s1) + np.asarray(o.s1)).tolist()),
                       s2=tuple(map(tuple, (np.asarray(self.s2) + np.asarray(o.s2)).tolist())),
                       sy=self.sy + o.sy, sxy=tuple((np.asarray(self.sxy) + np.asarray(o.sxy)).tolist()))


def _moments(M: np.ndarray, y: np.ndarray, w: np.ndarray) -> Moments:
    wm = M * w[:, None]
    return Moments(n=len(y), s0=float(w.sum()), sww=float((w * w).sum()), s1=tuple(wm.sum(axis=0).tolist()),
                   s2=tuple(map(tuple, (M.T @ wm).tolist())), sy=float(w @ y), sxy=tuple((wm.T @ y).tolist()))


def moments(d: TrainData, impute: np.ndarray) -> dict[str, Moments]:
    """Per instrument, on inputs imputed with `impute` (the caller's past-only medians)."""
    p = len(d.columns)
    M = _as_matrix(d.X, p)
    M = np.where(np.isnan(M), np.asarray(impute, dtype=float), M) if p else M
    y = O.clip_r(d.gross)
    w = np.asarray(d.w, dtype=float)
    if not (np.isfinite(y).all() and np.isfinite(w).all()) or (w < 0).any():
        raise ValueError("moments: gross and weights must be finite, weights >= 0")
    inst = np.asarray(d.instrument).astype(str)
    return {i: _moments(M[inst == i], y[inst == i], w[inst == i]) for i in sorted(set(inst.tolist()))}


def _scaler(total: Moments, p: int) -> tuple[np.ndarray, np.ndarray]:
    s0 = total.s0
    c = np.asarray(total.s1, dtype=float) / s0 if p else np.zeros(0)
    var = np.diag(np.asarray(total.s2, dtype=float)).copy() / s0 - c ** 2 if p else np.zeros(0)
    sd = np.sqrt(np.maximum(var, 0.0))
    return c, np.where(sd > 1e-12 * np.maximum(1.0, np.abs(c)), sd, 1.0)


def _std_blocks(m: Moments, c: np.ndarray, s: np.ndarray, a: float) -> tuple[float, np.ndarray, np.ndarray, float,
                                                                              np.ndarray]:
    """(Σw̃, Σw̃ z, Σw̃ z zᵀ, Σw̃ y, Σw̃ z y) of one instrument in the standardised coordinates, w̃ = a·w."""
    s1, s2, sxy = np.asarray(m.s1, float), np.asarray(m.s2, float), np.asarray(m.sxy, float)
    if len(c):
        zs = (s1 - m.s0 * c) / s
        zz = (s2 - np.outer(s1, c) - np.outer(c, s1) + m.s0 * np.outer(c, c)) / np.outer(s, s)
        zy = (sxy - c * m.sy) / s
    else:
        zs, zz, zy = np.zeros(0), np.zeros((0, 0)), np.zeros(0)
    return a * m.s0, a * zs, a * zz, a * m.sy, a * zy


class LcwArtifact(FrozenRecord):
    """The sr-v3 L-cw body (card 3.3)."""
    learner: Literal["L-cw"] = "L-cw"
    kind: Literal["gross"] = "gross"
    columns: tuple[str, ...]
    impute: tuple[float, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    levels: dict[str, float]               # shrunk, towards the unit intercept (λ_a)
    levels_unshrunk: dict[str, float]      # the level at λ_a = 0 with every other parameter held (report only)
    level_rows: dict[str, float]           # Σw̃ per instrument (the effective rows behind each level)
    lam: float
    lam_a: float
    design_effect: float
    n_rows: int
    n_eff: float

    def _z(self, M: np.ndarray) -> np.ndarray:
        M = np.where(np.isnan(M), np.asarray(self.impute), M)
        return (M - np.asarray(self.center)) / np.asarray(self.scale)

    def value(self, X: np.ndarray, instrument: np.ndarray) -> np.ndarray:
        p = len(self.columns)
        M = _as_matrix(X, p)
        lv = np.array([self.levels.get(str(i), 0.0) for i in np.asarray(instrument)])
        lin = _rowdot(self._z(M), np.asarray(self.coef)) if p else np.zeros(len(M))
        return (self.intercept + lv) + lin

    def value_row(self, x: np.ndarray, instrument: str) -> float:
        return float(self.value(np.asarray(x, dtype=float).reshape(1, -1), np.array([instrument]))[0])


def lcw_from_moments(moms: Mapping[str, Moments], impute: np.ndarray, columns: Sequence[str], *, lam: float,
                     lam_a: float, design_effect: float) -> LcwArtifact:
    """The closed-form L-cw solve. One instrument: no level (a gold body). Several: one level each, prior λ_a."""
    if not lam > 0:
        raise ValueError("L-cw: λ > 0")
    insts = sorted(moms)
    p = len(columns)
    total = moms[insts[0]]
    for i in insts[1:]:
        total = total + moms[i]
    if total.s0 <= 0:
        raise ValueError("L-cw: no weight")
    kish = total.s0 ** 2 / total.sww
    a = kish / float(design_effect) / total.s0
    c, s = _scaler(total, p)
    k = len(insts) if len(insts) > 1 else 0
    q = 1 + k + p
    A, b = np.zeros((q, q)), np.zeros(q)
    blocks = {i: _std_blocks(moms[i], c, s, a) for i in insts}
    for j, i in enumerate(insts):
        s0, zs, zz, sy, zy = blocks[i]
        A[0, 0] += s0
        b[0] += sy
        A[0, 1 + k:] += zs
        A[1 + k:, 1 + k:] += zz
        b[1 + k:] += zy
        if k:
            A[0, 1 + j] = A[1 + j, 0] = s0
            A[1 + j, 1 + j] = s0 + lam_a
            A[1 + j, 1 + k:] = A[1 + k:, 1 + j] = zs
            b[1 + j] = sy
    A[1 + k:, 0] = A[0, 1 + k:]
    A[1 + k:, 1 + k:] += lam * np.eye(p)
    theta = np.linalg.solve(A, b)
    if not np.isfinite(theta).all():
        raise ValueError("L-cw: the solution is not finite")
    beta = theta[1 + k:]
    levels, unshrunk, rows = {}, {}, {}
    for j, i in enumerate(insts if k else []):
        s0, zs, _, sy, _ = blocks[i]
        lv = float(theta[1 + j])
        levels[i] = lv
        unshrunk[i] = float((sy - s0 * theta[0] - zs @ beta) / s0) if s0 > 0 else 0.0
        rows[i] = float(s0)
    return LcwArtifact(columns=tuple(columns), impute=tuple(np.asarray(impute, float).tolist()),
                       center=tuple(c.tolist()), scale=tuple(s.tolist()), coef=tuple(beta.tolist()),
                       intercept=float(theta[0]), levels=levels, levels_unshrunk=unshrunk, level_rows=rows,
                       lam=float(lam), lam_a=float(lam_a), design_effect=float(design_effect), n_rows=total.n,
                       n_eff=float(kish))


def fit_lcw(d: TrainData, *, lam: float, lam_a: float, impute: np.ndarray | None = None) -> LcwArtifact:
    """L-cw on rows in memory: `moments` then `lcw_from_moments` (one arithmetic for streamed and in-memory fits)."""
    imp = medians(d.X) if impute is None else np.asarray(impute, dtype=float)
    if not len(d.columns):
        imp = np.zeros(0)
    return lcw_from_moments(moments(d, imp), imp, d.columns, lam=lam, lam_a=lam_a, design_effect=d.design_effect)


# ---------------------------------------------------------------------------------------------- G-cw, D-40
def _lgb_params(base: Mapping[str, Any], *, seed: int) -> dict[str, Any]:
    return {**base, "verbose": -1, "seed": int(seed), "deterministic": True, "force_row_wise": True,
            "num_threads": GBT_THREADS, "use_missing": True, "zero_as_missing": False}


class GcwArtifact(FrozenRecord):
    learner: Literal["G-cw"] = "G-cw"
    kind: Literal["p"] = "p"
    columns: tuple[str, ...]
    params: dict[str, Any]
    model_text: str
    n_rows: int

    def _booster(self) -> Any:
        import lightgbm as lgb
        return lgb.Booster(model_str=self.model_text)

    def value(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self._booster().predict(_as_matrix(X, len(self.columns))), dtype=float)

    def value_row(self, x: np.ndarray) -> float:
        return float(self.value(np.asarray(x, dtype=float).reshape(1, -1))[0])


def gcw_params(n_rows: int, columns: Sequence[str], *, seed: int, shift: Mapping[str, float] | None = None
               ) -> dict[str, Any]:
    """The pre-named G-cw parameters; `shift` multiplies named settings (the ADR 0106 parameter-shift check only)."""
    base = dict(GCW_PARAMS)
    for k, f in (shift or {}).items():
        base[k] = base[k] * f
    n_it = int(round(base.pop("num_iterations")))
    floor, share = base.pop("min_child_floor"), base.pop("min_child_share")
    mono = [1 if c in (RANGE_TO_COST, "dense_range_cost") else 0 for c in columns]
    return _lgb_params({**base, "num_iterations": n_it,
                        "min_data_in_leaf": int(max(floor, math.ceil(share * n_rows))),
                        "monotone_constraints": mono, "monotone_constraints_method": "basic"}, seed=seed)


def fit_gcw(d: TrainData, *, seed: int = 20261208, shift: Mapping[str, float] | None = None) -> GcwArtifact:
    """GBT-slow on y = 1[net R > 0], sample_weight = u × min(|net R|, 10) (R1, obj-v1's classification weights)."""
    import lightgbm as lgb
    y = (np.asarray(d.net, dtype=float) > 0).astype(float)
    sw = O.net_logit_weights(d.net, d.w)
    params = gcw_params(len(y), d.columns, seed=seed, shift=shift)
    ds = lgb.Dataset(_as_matrix(d.X, len(d.columns)), label=y, weight=sw, feature_name=list(_safe(d.columns)),
                     params={"verbose": -1, "max_bin": 255}, free_raw_data=True)
    booster = lgb.train(params, ds)
    return GcwArtifact(columns=tuple(d.columns), params=params, model_text=booster.model_to_string(),
                       n_rows=len(y))


def _safe(cols: Sequence[str]) -> list[str]:
    return [c.replace(" ", "_") for c in cols]


class DenseArtifact(FrozenRecord):
    """D-40 (ml-10 §3): P(T2 gross > 0 | x, side) and E[|T0 gross R| | x] / cost(x)."""
    learner: Literal["D-40"] = "D-40"
    columns: tuple[str, ...]
    params: dict[str, Any]
    p_side_text: str
    range_text: str
    n_rows: int

    def columns_for(self, X: np.ndarray, cost: np.ndarray) -> dict[str, np.ndarray]:
        import lightgbm as lgb
        M = _as_matrix(X, len(self.columns))
        p = np.asarray(lgb.Booster(model_str=self.p_side_text).predict(M), dtype=float)
        rng = np.asarray(lgb.Booster(model_str=self.range_text).predict(M), dtype=float)
        c = np.asarray(cost, dtype=float)
        return {"dense_p_side": p, "dense_range_cost": np.where(c > 0, np.maximum(rng, 0.0) / np.where(c > 0, c, 1.0),
                                                                np.nan)}

    def value_row(self, x: np.ndarray, cost: float) -> tuple[float, float]:
        out = self.columns_for(np.asarray(x, dtype=float).reshape(1, -1), np.array([cost]))
        return float(out["dense_p_side"][0]), float(out["dense_range_cost"][0])


def fit_dense(d: TrainData, *, t0_gross: np.ndarray, seed: int = 20261208) -> DenseArtifact:
    """Both heads on the unit's dense rows with the uniqueness weights (gross, cost-free targets: the cost enters as
    the divisor of the range head at use)."""
    import lightgbm as lgb
    base = {"num_leaves": DENSE_PARAMS["num_leaves"], "min_data_in_leaf": DENSE_PARAMS["min_data_in_leaf"],
            "learning_rate": DENSE_PARAMS["learning_rate"], "num_iterations": DENSE_PARAMS["num_iterations"]}
    M = _as_matrix(d.X, len(d.columns))
    w = np.asarray(d.w, dtype=float)
    pc = _lgb_params({**base, "objective": "binary"}, seed=seed)
    pr = _lgb_params({**base, "objective": "regression"}, seed=seed)
    yb = (O.clip_r(d.gross) > 0).astype(float)
    yr = np.abs(O.clip_r(t0_gross))
    names = _safe(d.columns)
    b1 = lgb.train(pc, lgb.Dataset(M, label=yb, weight=w, feature_name=names, params={"verbose": -1}))
    b2 = lgb.train(pr, lgb.Dataset(M, label=yr, weight=w, feature_name=names, params={"verbose": -1}))
    return DenseArtifact(columns=tuple(d.columns), params={"p_side": pc, "range": pr},
                         p_side_text=b1.model_to_string(), range_text=b2.model_to_string(), n_rows=len(yb))


# ---------------------------------------------------------------------------------------------- HB
class HbArtifact(FrozenRecord):
    learner: Literal["HB"] = "HB"
    kind: Literal["gross"] = "gross"
    columns: tuple[str, ...]
    tf_order: tuple[str, ...]
    cells: tuple[str, ...]                           # "tf|class"
    impute: dict[str, tuple[float, ...]]
    center: dict[str, tuple[float, ...]]
    scale: dict[str, tuple[float, ...]]
    intercept: dict[str, float]
    coef: dict[str, tuple[float, ...]]
    coef_sd: dict[str, tuple[float, ...]]
    theta: dict[str, tuple[float, ...]]
    class_of: dict[str, str]                         # instrument -> class (the trainer's map)
    lam0: float
    lam_rw: float
    lam_c: float

    def _cell(self, instrument: str, tf: str) -> str:
        return f"{tf}|{self.class_of.get(str(instrument), instrument_class(str(instrument)))}"

    def value(self, X: np.ndarray, instrument: np.ndarray, tf: np.ndarray) -> np.ndarray:
        """E[gross R | x] per row; NaN for a (tf, class) cell the body was not fitted on. NaN is "no model": a serving
        hook must fail open on it (trading-safety H3), never read it as a refusal."""
        M = _as_matrix(X, len(self.columns))
        out = np.full(len(M), np.nan)
        keys = np.array([self._cell(i, t) for i, t in zip(np.asarray(instrument), np.asarray(tf), strict=True)])
        for cell in np.unique(keys):
            m = keys == cell
            if cell not in self.intercept:
                continue
            Z = np.where(np.isnan(M[m]), np.asarray(self.impute[cell]), M[m])
            Z = (Z - np.asarray(self.center[cell])) / np.asarray(self.scale[cell])
            out[m] = self.intercept[cell] + _rowdot(Z, np.asarray(self.coef[cell]))
        return out

    def value_row(self, x: np.ndarray, instrument: str, tf: str) -> float:
        return float(self.value(np.asarray(x, dtype=float).reshape(1, -1), np.array([instrument]), np.array([tf]))[0])


def fit_hb(moms: Mapping[tuple[str, str], Moments], impute: Mapping[tuple[str, str], np.ndarray],
           columns: Sequence[str], *, tf_order: Sequence[str], lam0: float, lam_rw: float, lam_c: float,
           design_effect: Mapping[tuple[str, str], float], class_of: Mapping[str, str] | None = None) -> HbArtifact:
    """MAP of the tf random walk with class deviations, cells keyed (tf, class). Each cell is standardised with its own
    training moments and its weights normalised to its Kish n_eff / H."""
    p = len(columns)
    tfs = list(tf_order)
    cells = sorted(moms, key=lambda k: (tfs.index(k[0]), k[1]))
    nt, nc = len(tfs), len(cells)
    # variables: θ_t (nt·p), γ_cell (nc·p), b_cell (nc)
    q = nt * p + nc * p + nc
    A, rhs = np.zeros((q, q)), np.zeros(q)

    def th(t: int) -> slice:
        return slice(t * p, (t + 1) * p)

    def ga(j: int) -> slice:
        return slice(nt * p + j * p, nt * p + (j + 1) * p)

    def bi(j: int) -> int:
        return nt * p + nc * p + j
    scal = {}
    for j, cell in enumerate(cells):
        m = moms[cell]
        a = (m.s0 ** 2 / m.sww) / float(design_effect[cell]) / m.s0
        c, s = _scaler(m, p)
        scal[cell] = (c, s)
        s0, zs, zz, sy, zy = _std_blocks(m, c, s, a)
        t = tfs.index(cell[0])
        b = bi(j)
        for u in (th(t), ga(j)):
            for v in (th(t), ga(j)):
                A[u, v] += zz
            A[b, u] += zs
            A[u, b] += zs
            rhs[u] += zy
        A[b, b] += s0
        rhs[b] += sy
    eye = np.eye(p)
    A[th(0), th(0)] += lam0 * eye
    for t in range(1, nt):
        A[th(t), th(t)] += lam_rw * eye
        A[th(t - 1), th(t - 1)] += lam_rw * eye
        A[th(t), th(t - 1)] -= lam_rw * eye
        A[th(t - 1), th(t)] -= lam_rw * eye
    for j in range(nc):
        A[ga(j), ga(j)] += lam_c * eye
    sol = np.linalg.solve(A, rhs)
    cov = np.linalg.inv(A) * RESIDUAL_VAR
    if not np.isfinite(sol).all():
        raise ValueError("HB: the solution is not finite")
    coef, sd, inter = {}, {}, {}
    for j, cell in enumerate(cells):
        t = tfs.index(cell[0])
        key = f"{cell[0]}|{cell[1]}"
        coef[key] = tuple((sol[th(t)] + sol[ga(j)]).tolist())
        idx = np.r_[np.arange(t * p, (t + 1) * p)]
        gidx = np.r_[np.arange(ga(j).start, ga(j).stop)]
        var = np.diag(cov)[idx] + np.diag(cov)[gidx] + 2 * cov[idx, gidx]
        sd[key] = tuple(np.sqrt(np.maximum(var, 0.0)).tolist())
        inter[key] = float(sol[bi(j)])
    keys = [f"{c[0]}|{c[1]}" for c in cells]
    return HbArtifact(columns=tuple(columns), tf_order=tuple(tfs), cells=tuple(keys),
                      impute={f"{c[0]}|{c[1]}": tuple(np.asarray(impute[c], float).tolist()) for c in cells},
                      center={f"{c[0]}|{c[1]}": tuple(scal[c][0].tolist()) for c in cells},
                      scale={f"{c[0]}|{c[1]}": tuple(scal[c][1].tolist()) for c in cells},
                      intercept=inter, coef=coef, coef_sd=sd,
                      theta={t: tuple(sol[th(i)].tolist()) for i, t in enumerate(tfs)},
                      class_of=dict(sorted((class_of or {}).items())), lam0=float(lam0), lam_rw=float(lam_rw),
                      lam_c=float(lam_c))


# ---------------------------------------------------------------------------------------------- calibrators
class PlattCal(FrozenRecord):
    a: float
    b: float

    def apply(self, s: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(self.a + self.b * np.asarray(s, dtype=float))))


class IsotonicCal(FrozenRecord):
    x: tuple[float, ...]             # block lower edges (ascending)
    y: tuple[float, ...]             # fitted value per block

    def apply(self, s: np.ndarray) -> np.ndarray:
        xs, ys = np.asarray(self.x), np.asarray(self.y)
        sv = np.asarray(s, dtype=float)
        # linear interpolation between block values at their edges, clipped outside (as sklearn's out_of_bounds="clip")
        return np.interp(sv, xs, ys)


def fit_platt(s: np.ndarray, y: np.ndarray, w: np.ndarray, *, iters: int = 50) -> PlattCal:
    """Weighted logistic calibration p = σ(a + b·s) by Newton steps (fixed count, no early stop)."""
    sv, yv, wv = (np.asarray(v, dtype=float) for v in (s, y, w))
    X = np.column_stack([np.ones(len(sv)), sv])
    th = np.zeros(2)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ th)))
        g = X.T @ (wv * (yv - p))
        H = (X * (wv * p * (1 - p))[:, None]).T @ X + 1e-12 * np.eye(2)
        th = th + np.linalg.solve(H, g)
    return PlattCal(a=float(th[0]), b=float(th[1]))


def fit_isotonic(s: np.ndarray, y: np.ndarray, w: np.ndarray) -> IsotonicCal:
    """Weighted pool-adjacent-violators on y sorted by s (ties pooled first), as sklearn.isotonic with weights."""
    sv, yv, wv = (np.asarray(v, dtype=float) for v in (s, y, w))
    order = np.lexsort((yv, sv))
    sv, yv, wv = sv[order], yv[order], wv[order]
    ux, inv = np.unique(sv, return_inverse=True)
    W = np.bincount(inv, weights=wv)
    Y = np.bincount(inv, weights=wv * yv) / np.where(W > 0, W, 1)
    vals, wts, starts = [], [], []
    for i in range(len(ux)):
        vals.append(Y[i])
        wts.append(W[i])
        starts.append(i)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2 = vals.pop(), wts.pop()
            starts.pop()
            v1, w1 = vals.pop(), wts.pop()
            tot = w1 + w2
            vals.append((v1 * w1 + v2 * w2) / tot if tot > 0 else (v1 + v2) / 2)
            wts.append(tot)
    starts_arr = np.asarray(starts + [len(ux)])
    fitted = np.empty(len(ux))
    for k in range(len(vals)):
        fitted[starts_arr[k]:starts_arr[k + 1]] = vals[k]
    return IsotonicCal(x=tuple(ux.tolist()), y=tuple(fitted.tolist()))


# ---------------------------------------------------------------------------------------------- AVG
class AvgArtifact(FrozenRecord):
    learner: Literal["AVG"] = "AVG"
    kind: Literal["p"] = "p"
    bases: tuple[str, ...]
    base_kinds: tuple[str, ...]
    base_shas: tuple[str, ...]
    refs: tuple[tuple[float, ...], ...]      # per base: quantiles of its ranked score on the calibration rows
    calibrator: IsotonicCal
    n_rows: int

    def fused(self, values: Mapping[str, np.ndarray], cost: np.ndarray) -> np.ndarray:
        c = np.asarray(cost, dtype=float)
        ranks = []
        for b, kind, ref in zip(self.bases, self.base_kinds, self.refs, strict=True):
            v = np.asarray(values[b], dtype=float)
            s = v - c if kind == "gross" else v
            r = np.asarray(ref)
            ranks.append(np.searchsorted(r, s, side="right") / len(r))
        return np.mean(ranks, axis=0)

    def value(self, values: Mapping[str, np.ndarray], cost: np.ndarray) -> np.ndarray:
        return self.calibrator.apply(self.fused(values, cost))

    def value_row(self, values: Mapping[str, float], cost: float) -> float:
        return float(self.value({k: np.array([v]) for k, v in values.items()}, np.array([cost]))[0])


def fit_avg(values: Mapping[str, np.ndarray], kinds: Mapping[str, str], shas: Mapping[str, str], cost: np.ndarray,
            net: np.ndarray, w: np.ndarray) -> AvgArtifact:
    """On real OOF calibration rows only (R10): each base's CDF, equal weights, a weighted isotonic calibrator to
    p_w with y = 1[net R > 0], weights u × min(|net R|, 10)."""
    bases = tuple(sorted(values))
    c = np.asarray(cost, dtype=float)
    q = np.linspace(0, 1, AVG_REF_POINTS)
    refs = []
    for b in bases:
        v = np.asarray(values[b], dtype=float)
        s = v - c if kinds[b] == "gross" else v
        refs.append(tuple(np.quantile(s, q).tolist()) if len(s) else (0.0,))
    proto = AvgArtifact(bases=bases, base_kinds=tuple(kinds[b] for b in bases), base_shas=tuple(shas[b] for b in bases),
                        refs=tuple(refs), calibrator=IsotonicCal(x=(0.0, 1.0), y=(0.5, 0.5)), n_rows=len(c))
    f = proto.fused(values, c)
    cal = fit_isotonic(f, (np.asarray(net, dtype=float) > 0).astype(float), O.net_logit_weights(net, w))
    return proto.model_copy(update={"calibrator": cal})


# ---------------------------------------------------------------------------------------------- loading
_ARTIFACTS: dict[str, type[FrozenRecord]] = {"L-cw": LcwArtifact, "G-cw": GcwArtifact, "HB": HbArtifact,
                                              "AVG": AvgArtifact, "D-40": DenseArtifact}


def load_artifact(text: str) -> FrozenRecord:
    """JSON only (ADR 0010, A19 fail-closed): an unknown learner or field raises."""
    learner = json.loads(text).get("learner")
    cls = _ARTIFACTS.get(str(learner))
    if cls is None:
        raise ValueError(f"unknown learner artifact {learner!r}")
    return cls.model_validate_json(text)


__all__ = ["AVG_REF_POINTS", "DENSE_COLUMNS", "GCW_PARAMS", "LEARNERS", "RANGE_TO_COST", "AvgArtifact", "DenseArtifact",
           "GcwArtifact", "HbArtifact", "IsotonicCal", "LcwArtifact", "LearnerSpec", "Moments", "NotBuilt", "PlattCal",
           "TrainData", "artifact_json", "artifact_sha", "fit_avg", "fit_dense", "fit_gcw", "fit_hb", "fit_isotonic",
           "fit_lcw", "fit_platt", "fit_slot", "gcw_params", "keep", "lcw_from_moments", "load_artifact", "medians",
           "moments"]

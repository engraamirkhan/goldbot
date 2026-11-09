"""EVAL-2 L2: the empirical-Bayes hierarchical model (prereg 2026-11-09 sections 1 and 4.5; method v6 section 2, L2).

"theta_c = mu_F + a_TF + b_inst + u_c on day-clustered cell means of excess; REML; Student-t; prior fitted
leave-family-out on every cell ever viewed (real + NULL-RAND), de-duplicated by config hash; empirical null from the
controls (Efron). Student-t (df 5, fixed) on the cell random effects only; Gaussian likelihood on day-clustered cell
means." "L2 carry: P(theta_c > 0) >= 0.80 and P(net_c > 0 at 1.5x deciding cost) >= 0.80; at most 3 cells per family by
posterior mean." "L2 cell kill: P(net > 0.05 R) < 0.05." "0.80 raised, never lowered, until the S1 null carry rate is
<= 5 %; thresholds, model and code hash frozen in the card before the L2 run on real cells."

`cells` (one row per cell; `cell_table` builds it from `units`): cell_id, family, tf, inst, mean (the cell's mean
excess R), se (its session-day-clustered SE), n_days, twin_net (the mean twin net R at 1.0x deciding cost); optional
twin_se (SE of twin_net, default 0), config_hash (de-duplication; the first row of a hash is kept) and control (True
for a NULL-RAND control; default: family starts with "NULL").

Fit (`fit_prior`): Gaussian REML for the variance components of family, TF, instrument and cell (intercept the only
fixed effect), the cell term Student-t through its normal scale mixture (u_c | lambda_c ~ N(0, s^2 / lambda_c),
lambda_c ~ Gamma(df/2, df/2)) with lambda_c updated by the EM step lambda_c = (df + 1) / (df + E[u_c^2] / s^2)
(Pinheiro, Liu and Wu 2001) until stable. Posterior (`posteriors`): the held-out family's mu_F ~ N(mu, var(mu) +
tau^2_family), each a_TF / b_inst at its leave-family-out BLUP and conditional variance (N(0, tau^2) for a level the
population never saw), u_c Student-t by the same EM weights; theta_c is Gaussian given the weights (a plug-in, so
its tails are those of the converged scale mixture). Empirical null: the cell likelihood is y - null_mean x se with SE
se x max(1, null_sd) (Efron; never narrowed below the theoretical null).

Net: theta_c + twin_net is the cell's net at 1.0x (net = excess + twin mean, both already paying the deciding cost
once); at 1.5x only the extra 0.5x deciding cost is charged."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.research.catalogue.stats import cluster_se

CARRY_P_MIN = 0.80                  # "0.80 raised, never lowered"
T_DF_FROZEN = 5.0                   # quant 2026-11-09: Student-t df 5 on u_c, fixed
EM_ITERS = 50
EM_TOL = 1e-8
LOG_VAR_BOUNDS = (-30.0, 5.0)       # log variance, R^2 units: e^-30 is 0 for any purpose, e^5 = 148 R^2
EFRON_MIN_CONTROLS = 100       # the median of 100 null z has SE ~0.13: fewer controls shift every cell by noise
IQR_TO_SD = 1.3489795003921634      # IQR of N(0, 1)


class SpecFrozen(RuntimeError):
    """The card already holds a frozen L2 spec: thresholds never change after the freeze."""


@dataclass(frozen=True, slots=True)
class HierSpec:
    terms: tuple[str, ...] = ("family", "tf", "inst", "cell")
    method: Literal["REML"] = "REML"
    t_df: float = 5.0                    # Student-t on the random effects u_c only; fixed, not estimated (quant 2026-11-09)
    t_on: Literal["random_effects"] = "random_effects"   # the likelihood of day-clustered cell means is Gaussian
    carry_p: float = 0.80
    carry_cost_mult: float = 1.5
    kill_net_r: float = 0.05
    kill_p: float = 0.05
    max_carry: int = 3
    prior_population_hash: str = ""
    code_hash: str = ""


@dataclass(frozen=True, slots=True)
class CellPosterior:
    cell_id: str
    mean: float
    sd: float
    p_pos: float
    p_net_pos_gate: float
    p_net_gt_kill: float
    n_days: int


@dataclass(frozen=True, slots=True)
class L2Decision:
    family: str
    carried: tuple[str, ...]
    killed: tuple[str, ...]
    held: tuple[str, ...]


# ------------------------------------------------------------------ freeze
def code_hash() -> str:
    """sha256 of this module's source: the L2 code hash recorded in the card (`HierSpec.code_hash`)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _check_frozen_spec(spec: HierSpec) -> None:
    if spec.carry_p < CARRY_P_MIN:
        raise ValueError(f"carry_p {spec.carry_p} is below {CARRY_P_MIN}: the 0.80 is raised, never lowered")
    if spec.t_df != T_DF_FROZEN or spec.method != "REML" or spec.t_on != "random_effects":
        raise ValueError(f"t_df/method/t_on must be {T_DF_FROZEN}/REML/random_effects (prereg section 1)")
    if (spec.carry_cost_mult, spec.kill_net_r, spec.kill_p, spec.max_carry) != (1.5, 0.05, 0.05, 3):
        raise ValueError("carry_cost_mult/kill_net_r/kill_p/max_carry are frozen at 1.5/0.05/0.05/3")
    if set(spec.terms) != {"family", "tf", "inst", "cell"}:
        raise ValueError(f"terms {spec.terms}: the frozen model is family + tf + inst + cell")
    if not spec.code_hash:
        raise ValueError("code_hash is empty: the code hash is frozen in the card with the thresholds")
    if not spec.prior_population_hash:
        raise ValueError("prior_population_hash is empty: the prior population is frozen in the card")


def freeze_spec(spec: HierSpec, card_path: Path) -> str:
    """Write the L2 spec and its sha256 into the card (`l2_spec: {spec, sha256}`; JSON, or YAML for .yaml/.yml) and
    return the hash. Raises SpecFrozen when the card already holds one, ValueError for a spec off the frozen values
    (prereg section 1: thresholds, model and code hash frozen before the L2 run on real cells)."""
    _check_frozen_spec(spec)
    body = asdict(spec)
    body["terms"] = list(spec.terms)
    h = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = Path(card_path)
    yml = path.suffix in (".yaml", ".yml")
    doc: dict[str, Any] = {}
    if path.exists() and path.read_text().strip():
        if yml:
            import yaml
            doc = yaml.safe_load(path.read_text()) or {}
        else:
            doc = json.loads(path.read_text())
    if "l2_spec" in doc:
        raise SpecFrozen(f"{path} already holds a frozen L2 spec ({doc['l2_spec'].get('sha256')})")
    doc["l2_spec"] = {"spec": body, "sha256": h}
    if yml:
        import yaml
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    else:
        path.write_text(json.dumps(doc, indent=2) + "\n")
    return h


# ------------------------------------------------------------------ cells and the empirical null
def cell_table(units: pd.DataFrame) -> pd.DataFrame:
    """The L2 `cells` frame from `units`: per cell its mean excess, session-day-clustered SE, session days, and the
    mean twin net with its clustered SE (instances with an excess only)."""
    rows = []
    for cell, g in units.groupby("cell_id", sort=True):
        ex = g["excess_r"].to_numpy(float)
        ok = np.isfinite(ex)
        day = g["day"].to_numpy()
        tw = g["twin_mean_net_r"].to_numpy(float)
        rows.append({"cell_id": str(cell), "family": str(g["family"].iloc[0]), "tf": str(g["tf"].iloc[0]),
                     "inst": str(g["inst"].iloc[0]), "mean": float(ex[ok].mean()) if ok.any() else float("nan"),
                     "se": cluster_se(ex, day), "n_days": int(len(np.unique(day[ok]))),
                     "twin_net": float(tw[ok].mean()) if ok.any() else float("nan"),
                     "twin_se": cluster_se(np.where(ok, tw, np.nan), day)})
    return pd.DataFrame(rows, columns=["cell_id", "family", "tf", "inst", "mean", "se", "n_days", "twin_net",
                                       "twin_se"])


def efron_null(control_z: np.ndarray) -> tuple[float, float]:
    """Empirical null (Efron 2004) from the controls' z = cell mean / SE. The controls are null by construction (no
    pi0 to estimate), so the central-matching location and scale are read robustly: (median, IQR / 1.349). Fewer than
    EFRON_MIN_CONTROLS finite controls: the theoretical null (0, 1)."""
    z = np.asarray(control_z, float)
    z = z[np.isfinite(z)]
    if len(z) < EFRON_MIN_CONTROLS:
        return 0.0, 1.0
    q1, med, q3 = np.quantile(z, [0.25, 0.5, 0.75])
    return float(med), float((q3 - q1) / IQR_TO_SD)


def _controls(cells: pd.DataFrame) -> np.ndarray:
    if "control" in cells.columns:
        return cells["control"].fillna(False).to_numpy(bool)
    return cells["family"].astype(str).str.upper().str.startswith("NULL").to_numpy(bool)


def _population(cells: pd.DataFrame, leave_out_family: str) -> pd.DataFrame:
    pop = cells[cells["family"].astype(str) != leave_out_family]
    if "config_hash" in pop.columns:
        pop = pop.drop_duplicates("config_hash", keep="first")
    ok = np.isfinite(pop["mean"].to_numpy(float)) & np.isfinite(pop["se"].to_numpy(float)) & (pop["se"] > 0)
    return pop[ok].reset_index(drop=True)


def _adjusted(cells: pd.DataFrame, null_mean: float, null_sd: float) -> tuple[np.ndarray, np.ndarray]:
    se = cells["se"].to_numpy(float)
    return cells["mean"].to_numpy(float) - null_mean * se, se * max(1.0, null_sd)


# ------------------------------------------------------------------ REML with Student-t cell effects
@dataclass
class _Design:
    """Cells nest in families; TF and instrument cross them. fam: family index per cell (None without the family
    term); fam_sum: the n_fam x k sparse indicator; z2: k x q2 indicators of the TF and instrument levels."""
    fam: np.ndarray | None
    fam_sum: Any
    z2: np.ndarray
    blocks: list[tuple[str, list[str], slice]]


def _design(cells: pd.DataFrame, terms: Sequence[str]) -> _Design:
    from scipy.sparse import csr_matrix
    k = len(cells)
    fam = fam_sum = None
    if "family" in terms:
        _, fam = np.unique(cells["family"].astype(str).to_numpy(), return_inverse=True)
        fam_sum = csr_matrix((np.ones(k), (fam, np.arange(k))), shape=(int(fam.max()) + 1 if k else 0, k))
    cols, blocks, at = [], [], 0
    for t in ("tf", "inst"):
        if t not in terms:
            continue
        lv, idx = np.unique(cells[t].astype(str).to_numpy(), return_inverse=True)
        m = np.zeros((k, len(lv)))
        m[np.arange(k), idx] = 1.0
        cols.append(m)
        blocks.append((t, [str(v) for v in lv], slice(at, at + len(lv))))
        at += len(lv)
    return _Design(fam, fam_sum, np.hstack(cols) if cols else np.zeros((k, 0)), blocks)


class _VInv:
    """V = blockdiag_f(D_f + tau_f 1 1') + Z2 G2 Z2' (D diagonal) and V^-1 by two Woodbury steps: Sherman-Morrison
    within each family, then the q2 TF + instrument levels. O(k q2^2): the population's thousands of families never
    form a dense matrix."""

    def __init__(self, d: np.ndarray, des: _Design, tau_f: float, g2: np.ndarray) -> None:
        self.di = 1.0 / d
        self.des = des
        self.c: np.ndarray | None = None
        logdet = float(np.log(d).sum())
        if des.fam is not None and tau_f > 0:
            s = des.fam_sum @ self.di
            self.c = tau_f / (1.0 + tau_f * s)
            logdet += float(np.log1p(tau_f * s).sum())
        self.q2 = des.z2.shape[1]
        if self.q2:
            self.w = self.vf(des.z2)
            m2 = np.diag(1.0 / g2) + des.z2.T @ self.w
            logdet += float(np.log(g2).sum()) + 2.0 * float(np.log(np.diag(np.linalg.cholesky(m2))).sum())
            self.m2inv = np.linalg.inv(m2)
        self.logdet = logdet

    def vf(self, x: np.ndarray) -> np.ndarray:
        dx = self.di * x if x.ndim == 1 else self.di[:, None] * x
        if self.c is None or self.des.fam is None:
            return dx
        t = self.des.fam_sum @ dx
        corr = (self.c * t)[self.des.fam] if x.ndim == 1 else (self.c[:, None] * t)[self.des.fam]
        return dx - (self.di * corr if x.ndim == 1 else self.di[:, None] * corr)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y = self.vf(x)
        return y - self.w @ (self.m2inv @ (self.w.T @ x)) if self.q2 else y

    def diag(self) -> np.ndarray:
        out = self.di.copy()
        if self.c is not None and self.des.fam is not None:
            out -= self.di ** 2 * self.c[self.des.fam]
        if self.q2:
            out -= np.einsum("ij,jk,ik->i", self.w, self.m2inv, self.w)
        return out


def _components(theta: np.ndarray, des: _Design, use_cell: bool) -> tuple[float, np.ndarray, float]:
    """(tau2_family, the q2 level variances, s2_cell) from the log-variance vector."""
    j = 0
    tau_f = 0.0
    if des.fam is not None:
        tau_f, j = math.exp(theta[0]), 1
    g2 = np.zeros(des.z2.shape[1])
    for t, _, sl in des.blocks:
        g2[sl] = math.exp(theta[j])
        j += 1
    return tau_f, g2, (math.exp(theta[j]) if use_cell else 0.0)


def _reml(y: np.ndarray, vinv: _VInv) -> tuple[float, float, float, np.ndarray]:
    """-2 REML log-likelihood of y = mu + random effects (intercept the only fixed effect): log|V| + log(1'V^-1 1) +
    r'V^-1 r. Returns (-2 ll, mu-hat, var(mu-hat), V^-1 r)."""
    v1 = vinv(np.ones(len(y)))
    xtvx = float(v1.sum())
    mu = float(v1 @ y) / xtvx
    vr = vinv(y - mu)
    return vinv.logdet + math.log(xtvx) + float((y - mu) @ vr), mu, 1.0 / xtvx, vr


def _fit(y: np.ndarray, se: np.ndarray, des: _Design, t_df: float, use_cell: bool
         ) -> tuple[dict[str, float], np.ndarray, np.ndarray]:
    """REML variance components with the EM Student-t weights on u_c (module docstring). Returns (components, BLUPs
    of the TF and instrument levels, their conditional variances)."""
    from scipy.optimize import minimize
    lam = np.ones(len(y))
    n_par = (des.fam is not None) + len(des.blocks) + use_cell
    theta = np.full(n_par, math.log(max(float(np.var(y)), 1e-8) / max(n_par, 1)))
    student = use_cell and np.isfinite(t_df) and t_df <= 1e6
    for _ in range(EM_ITERS if student else 1):
        def nll(th: np.ndarray, lam: np.ndarray = lam) -> float:
            tau_f, g2, s2 = _components(th, des, use_cell)
            return _reml(y, _VInv(se ** 2 + s2 / lam, des, tau_f, g2))[0]
        theta = minimize(nll, theta, method="L-BFGS-B", bounds=[LOG_VAR_BOUNDS] * n_par).x
        tau_f, g2, s2 = _components(theta, des, use_cell)
        vinv = _VInv(se ** 2 + s2 / lam, des, tau_f, g2)
        _, mu, var_mu, vr = _reml(y, vinv)
        if not student:
            break
        su = s2 / lam
        eu2 = (su * vr) ** 2 + np.maximum(su - su ** 2 * vinv.diag(), 0.0)      # E[u_c^2 | y]
        new = (t_df + 1.0) / (t_df + eu2 / max(s2, 1e-300))
        done = float(np.max(np.abs(new - lam))) < EM_TOL
        lam = new
        if done:
            break
    comps = {"mu": mu, "mu_var": var_mu, "s2_cell": s2, "tau2_family": tau_f}
    for t, _, sl in des.blocks:
        comps[f"tau2_{t}"] = float(g2[sl][0])
    if not vinv.q2:
        return comps, np.zeros(0), np.zeros(0)
    # BLUP b2 = G2 Z2' V^-1 r; conditional variance diag(G2 - G2 Z2' V^-1 Z2 G2)
    z2 = des.z2
    a = z2.T @ vinv.w
    ztvz = a - a @ vinv.m2inv @ a
    blup = g2 * (z2.T @ vr)
    bvar = g2 - g2 ** 2 * np.diag(ztvz)
    return comps, blup, np.maximum(bvar, 0.0)


def fit_prior(cells: pd.DataFrame, *, leave_out_family: str, spec: HierSpec) -> Mapping[str, float]:
    """The prior for `leave_out_family`, fitted on every other cell ever viewed (real and NULL-RAND), de-duplicated
    by config hash: mu, mu_var, tau2_family/tf/inst, s2_cell (Student-t scale^2 of u_c), t_df, null_mean/null_sd
    (Efron, from the controls), n_cells, and per TF / instrument level its BLUP and conditional variance
    (`a_tf:<tf>`, `a_tf_var:<tf>`, `b_inst:<inst>`, `b_inst_var:<inst>`)."""
    pop = _population(cells, leave_out_family)
    ctrl = _controls(pop)
    null_mean, null_sd = efron_null((pop["mean"] / pop["se"]).to_numpy(float)[ctrl])
    y, se = _adjusted(pop, null_mean, null_sd)
    des = _design(pop, spec.terms)
    comps, blup, bvar = _fit(y, se, des, spec.t_df, "cell" in spec.terms)
    out: dict[str, float] = {"mu": comps["mu"], "mu_var": comps["mu_var"],
                             "tau2_family": comps.get("tau2_family", 0.0), "tau2_tf": comps.get("tau2_tf", 0.0),
                             "tau2_inst": comps.get("tau2_inst", 0.0), "s2_cell": comps["s2_cell"],
                             "t_df": float(spec.t_df), "null_mean": null_mean, "null_sd": null_sd,
                             "n_cells": float(len(pop))}
    for t, levels, sl in des.blocks:
        key = "a_tf" if t == "tf" else "b_inst"
        for lv, b, v in zip(levels, blup[sl], bvar[sl]):
            out[f"{key}:{lv}"] = float(b)
            out[f"{key}_var:{lv}"] = float(v)
    return out


# ------------------------------------------------------------------ posteriors and the decision
def _norm_sf(x: np.ndarray) -> np.ndarray:
    from scipy.stats import norm
    return np.asarray(norm.sf(x), float)


def posteriors(cells: pd.DataFrame, prior: Mapping[str, float], *, spec: HierSpec,
               deciding_cost_r: Mapping[str, float]) -> list[CellPosterior]:
    """Posterior of theta_c for every row of `cells` (one family's cells) under `prior` (module docstring), in row
    order: mean, sd, P(theta > 0), P(net > 0 at spec.carry_cost_mult x deciding cost), P(net > spec.kill_net_r) at
    1.0x. `deciding_cost_r`: cell_id -> the cell's deciding cost at 1.0x (R); a cell without one raises KeyError."""
    ids = cells["cell_id"].astype(str).tolist()
    dc = np.array([float(deciding_cost_r[c]) for c in ids])
    y, se = _adjusted(cells, float(prior.get("null_mean", 0.0)), float(prior.get("null_sd", 1.0)))
    k = len(ids)
    # beta = [mu_F, a_tf levels, b_inst levels] with independent Gaussian priors
    cols, m0, p0 = [np.ones(k)], [float(prior["mu"])], [float(prior.get("mu_var", 0.0)) + float(prior["tau2_family"])]
    for t, key, tau in (("tf", "a_tf", "tau2_tf"), ("inst", "b_inst", "tau2_inst")):
        if t not in spec.terms:
            continue
        for lv in sorted(cells[t].astype(str).unique()):
            cols.append((cells[t].astype(str) == lv).to_numpy(float))
            m0.append(float(prior.get(f"{key}:{lv}", 0.0)))
            p0.append(float(prior.get(f"{key}_var:{lv}", prior.get(tau, 0.0))))
    z = np.column_stack(cols)
    m0a = np.array(m0)
    p0a = np.maximum(np.array(p0), 1e-12)
    s2 = float(prior["s2_cell"]) if "cell" in spec.terms else 0.0
    lam = np.ones(k)
    t_df = float(spec.t_df)
    for _ in range(EM_ITERS):
        su = s2 / lam
        w = 1.0 / (su + se ** 2)
        sig = np.linalg.inv(np.diag(1.0 / p0a) + z.T @ (z * w[:, None]))
        mb = sig @ (m0a / p0a + z.T @ (w * y))
        kc = su / (su + se ** 2)
        zb = z @ mb
        zsz = np.einsum("ij,jk,ik->i", z, sig, z)
        mean = (1.0 - kc) * zb + kc * y
        var = (1.0 - kc) ** 2 * zsz + kc * se ** 2
        if s2 <= 0 or not np.isfinite(t_df) or t_df > 1e6:
            break
        eu2 = (kc * (y - zb)) ** 2 + kc ** 2 * zsz + kc * se ** 2
        new = (t_df + 1.0) / (t_df + eu2 / s2)
        if float(np.max(np.abs(new - lam))) < EM_TOL:
            lam = new
            break
        lam = new
    sd = np.sqrt(np.maximum(var, 0.0))
    tw = cells["twin_net"].to_numpy(float)
    tw_se = cells["twin_se"].to_numpy(float) if "twin_se" in cells.columns else np.zeros(k)
    tw_se = np.where(np.isfinite(tw_se), tw_se, 0.0)
    net_sd = np.sqrt(sd ** 2 + tw_se ** 2)
    gate = mean + tw - (spec.carry_cost_mult - 1.0) * dc               # 1.0x already inside net: charge the extra
    p_pos = _norm_sf(-mean / np.maximum(sd, 1e-300))
    p_gate = _norm_sf(-gate / np.maximum(net_sd, 1e-300))
    p_kill = _norm_sf(-(mean + tw - spec.kill_net_r) / np.maximum(net_sd, 1e-300))
    n_days = cells["n_days"].to_numpy(np.int64)
    return [CellPosterior(c, float(a), float(b), float(p), float(g), float(q), int(n))
            for c, a, b, p, g, q, n in zip(ids, mean, sd, p_pos, p_gate, p_kill, n_days)]


def l2_decide(posts: Sequence[CellPosterior], *, family: str, spec: HierSpec) -> L2Decision:
    """Kill: P(net > kill_net_r) < kill_p. Carry: of the rest, P(theta > 0) >= carry_p and P(net > 0 at the gate cost)
    >= carry_p, at most `max_carry` by posterior mean (ties by cell id), mechanically. Held: every other cell."""
    killed = [p for p in posts if p.p_net_gt_kill < spec.kill_p]
    kill_ids = {p.cell_id for p in killed}
    ok = [p for p in posts if p.cell_id not in kill_ids and p.p_pos >= spec.carry_p and p.p_net_pos_gate >= spec.carry_p]
    carried = sorted(ok, key=lambda p: (-p.mean, p.cell_id))[: spec.max_carry]
    carry_ids = {p.cell_id for p in carried}
    held = [p.cell_id for p in posts if p.cell_id not in carry_ids and p.cell_id not in kill_ids]
    return L2Decision(family, tuple(p.cell_id for p in carried), tuple(p.cell_id for p in killed), tuple(held))

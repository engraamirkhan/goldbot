"""The obj-v1 diagnostic battery (ML lifecycle plan 2026-11-25: N10 "descriptive and never a selector", N6, A26).

ADR 0098 amendment: "*Unweighted* log-loss, AUC, accuracy and win rate are banned from selection, threshold and gate,
and are reported as diagnostics only." They live here and nowhere else; `objective.GUARDED_MODULES` must never list
this module and no guarded module may import it (tests/test_objective.py).

Reading rules (N10), applied by the card, not by this code:
- calibration slope outside [0.7, 1.3] or BSS / MSE skill <= 0: the score may rank, it may not feed EV thresholds or
  sizing;
- seed SD of ΔS per day >= MIN_EDGE / 2: INCONCLUSIVE;
- a flat learning curve plus a slope near 0 retires "a new learner on the same inputs".

Two translations to the offset form (R-I), recorded in ADR 0102:
- N6's "keep share <= 5 % in every cell whose weighted base rate is below 0.5" was written for the p_w >= 0.5 logit.
  Under the offset form a model fitted to shuffled gross R collapses to its intercept, and intercept − cost(x) > 0 is
  a legitimate cost-only keep. The check is therefore relative to that intercept-only rule (quant-signed), and its
  metric is the FALSE-KEEP share mean(keep & ~keep0) <= 5 % per cell (quant ruling 3 on PR #461: a net excess lets
  noise keeps cancel against noise drops); mean(keep XOR keep0) is reported beside it.
- BSS is for a probability; the offset form's prediction is in R, so `mse_skill` is its analogue.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from goldbot.base import FrozenRecord
from goldbot.research import objective as O

MAX_FALSE_KEEP = 0.05


class Calibration(FrozenRecord):
    """Weighted least squares of the outcome on the prediction: a calibrated prediction has slope 1, intercept 0."""
    slope: float
    intercept: float


class SeedSpread(FrozenRecord):
    sd: float
    n_seeds: int
    inconclusive: bool


class CurvePoint(FrozenRecord):
    n: int
    s: float


class ShuffleCheck(FrozenRecord):
    """Per cell, for the OOF fit on shuffled gross R against the intercept-only rule: the false-keep share
    mean(keep & ~keep0) (the check) and the disagreement share mean(keep XOR keep0) (reported)."""
    false_keep_share: dict[str, float]
    xor_share: dict[str, float]
    kept: dict[str, int]
    passed: bool


class Canary(FrozenRecord):
    """A planted ±delta R signal on a fresh binary column: S of the fitted filter, of the oracle (the true planted
    mean) and of the intercept-only null, all out of fold."""
    delta: float
    s_model: float
    s_oracle: float
    s_null: float
    recovered: bool


# ---------------------------------------------------------------------------------------------- calibration and skill
def weighted_calibration(pred: np.ndarray, outcome: np.ndarray, w: np.ndarray) -> Calibration:
    """WLS outcome ~ a + b · pred with weights w (e.g. obj-v1 weights; outcome clipped net or gross R)."""
    p, y = np.asarray(pred, dtype=float), np.asarray(outcome, dtype=float)
    ww = np.asarray(w, dtype=float)
    ww = ww / ww.sum()
    pm, ym = float(ww @ p), float(ww @ y)
    var = float(ww @ (p - pm) ** 2)
    slope = float(ww @ ((p - pm) * (y - ym))) / var if var > 0 else float("nan")
    return Calibration(slope=slope, intercept=ym - slope * pm)


def _skill(pred: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    ww = np.asarray(w, dtype=float)
    ref = float(ww @ (y - (ww @ y) / ww.sum()) ** 2)
    return 1.0 - float(ww @ (pred - y) ** 2) / ref if ref > 0 else float("nan")


def brier_skill(p: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """Weighted Brier skill score against the weighted base rate."""
    return _skill(np.asarray(p, dtype=float), np.asarray(y, dtype=float), w)


def mse_skill(pred: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """The offset form's BSS: 1 − weighted MSE / weighted variance of the (clipped) outcome."""
    return _skill(np.asarray(pred, dtype=float), O.clip_r(y), w)


def within_arm_auc(score: np.ndarray, y: np.ndarray, arm: np.ndarray) -> float:
    """Row-weighted mean over arms of the AUC inside each arm (never pooled: about half of pooled AUC is arm
    identity, S9B). Arms with one class are skipped; none left: NaN. Diagnostic only."""
    s, yy, a = np.asarray(score, dtype=float), np.asarray(y, dtype=bool), np.asarray(arm).astype(str)
    num = den = 0.0
    for g in np.unique(a):
        m = a == g
        pos, n = int(yy[m].sum()), int(m.sum())
        if pos == 0 or pos == n:
            continue
        ranks = stats.rankdata(s[m])
        auc = (float(ranks[yy[m]].sum()) - pos * (pos + 1) / 2) / (pos * (n - pos))
        num += n * auc
        den += n
    return num / den if den else float("nan")


def score_deciles(score: np.ndarray, net_r: np.ndarray) -> pd.DataFrame:
    """Score decile x (n, win rate, mean clipped net R) (A26). Descriptive."""
    d = pd.DataFrame({"q": pd.qcut(pd.Series(score).rank(method="first"), 10, labels=False),
                      "net": O.clip_r(net_r)})
    g = d.groupby("q")["net"]
    return pd.DataFrame({"n": g.size(), "win_rate": g.apply(lambda x: float((x > 0).mean())),
                         "mean_net_r": g.mean()})


# ---------------------------------------------------------------------------------------------- stability
def seed_spread(values: Sequence[float], *, min_edge: float = O.MIN_S_R) -> SeedSpread:
    """SD over seeds of ΔS per day (or per candidate, in the units of min_edge); INCONCLUSIVE at SD >= min_edge / 2."""
    v = np.asarray(values, dtype=float)
    sd = float(v.std(ddof=1)) if len(v) > 1 else float("nan")
    return SeedSpread(sd=sd, n_seeds=len(v), inconclusive=not (sd < min_edge / 2))


def learning_curve(s_at: Callable[[int], float], *, sizes: Sequence[int] = (1_000, 3_000, 10_000, 30_000, 100_000),
                   n_available: int) -> list[CurvePoint]:
    """S at each training size, the last one capped at the rows available (each size once, ascending)."""
    out: list[CurvePoint] = []
    for n in sorted(sizes):
        m = min(int(n), int(n_available))
        if out and out[-1].n == m:
            break
        out.append(CurvePoint(n=m, s=float(s_at(m))))
    return out


# ---------------------------------------------------------------------------------------------- canaries
def _folds(day: np.ndarray) -> np.ndarray:
    """Two folds by session day (alternate days), so no day is split across folds."""
    _, inv = np.unique(np.asarray(day).astype(str), return_inverse=True)
    return (inv % 2).astype(bool)


def _oof(X: np.ndarray, y: np.ndarray, w: np.ndarray, day: np.ndarray, lam0: float,
         design_effect: float) -> tuple[np.ndarray, np.ndarray]:
    """(OOF predicted gross R, OOF intercept-only prediction) over the two day folds."""
    f = _folds(day)
    pred, base = np.empty(len(y)), np.empty(len(y))
    for tr in (f, ~f):
        m = O.fit_offset(X[tr], y[tr], w[tr], lam0=lam0, design_effect=design_effect)
        pred[~tr] = m.predict_gross(X[~tr])
        base[~tr] = float(np.average(O.clip_r(y[tr]), weights=w[tr]))
    return pred, base


def shuffled_keep_check(X: np.ndarray, gross_r: np.ndarray, cost: np.ndarray, w: np.ndarray, day: np.ndarray,
                        cells: np.ndarray, *, lam0: float, design_effect: float, seed: int = O.SEED,
                        min_kept: int = O.MIN_KEPT) -> ShuffleCheck:
    """N6: shuffle gross R (seeded), fit through the full offset pipeline out of fold, keep iff pred − cost > 0. Per
    cell the share of rows kept that the intercept-only rule drops must be <= 5 %, or the cell keeps fewer than
    `min_kept` rows (it would ABSTAIN). Otherwise the penalty is too loose."""
    M = np.asarray(X, dtype=float)
    y = np.random.default_rng(seed).permutation(np.asarray(gross_r, dtype=float))
    c = np.asarray(cost, dtype=float)
    pred, base = _oof(M, y, np.asarray(w, dtype=float), day, lam0, design_effect)
    keep, keep0 = O.keep_rule(pred, c), O.keep_rule(base, c)
    cl = np.asarray(cells).astype(str)
    false, xor, kept = {}, {}, {}
    for g in np.unique(cl):
        m = cl == g
        false[g] = float((keep[m] & ~keep0[m]).mean())
        xor[g] = float((keep[m] ^ keep0[m]).mean())
        kept[g] = int(keep[m].sum())
    ok = all(false[g] <= MAX_FALSE_KEEP or kept[g] < min_kept for g in false)
    return ShuffleCheck(false_keep_share=false, xor_share=xor, kept=kept, passed=ok)


def oracle_canary(X: np.ndarray, gross_r: np.ndarray, cost: np.ndarray, w: np.ndarray, day: np.ndarray, *,
                  delta: float, lam0: float, design_effect: float, seed: int = O.SEED) -> Canary:
    """Plant gross + delta · (2z − 1) on a fresh fair coin z, add z as an input, fit out of fold and read S. Recovered
    iff the oracle beats the null and the model closes at least half of the oracle's gap over the null."""
    rng = np.random.default_rng(seed)
    g = np.asarray(gross_r, dtype=float)
    z = rng.integers(0, 2, len(g)).astype(float)
    gc = g + delta * (2 * z - 1)
    c = np.asarray(cost, dtype=float)
    net = gc - c
    pred, base = _oof(np.column_stack([np.asarray(X, dtype=float), z]), gc, np.asarray(w, dtype=float), day, lam0,
                      design_effect)
    oracle = float(np.average(O.clip_r(g), weights=w)) + delta * (2 * z - 1)

    def s(keep: np.ndarray) -> float:
        return O.read(net, gc, keep, day, boot=0).s

    s_m, s_o, s_n = s(O.keep_rule(pred, c)), s(O.keep_rule(oracle, c)), s(O.keep_rule(base, c))
    return Canary(delta=delta, s_model=s_m, s_oracle=s_o, s_null=s_n,
                  recovered=s_o > s_n and (s_m - s_n) >= 0.5 * (s_o - s_n))

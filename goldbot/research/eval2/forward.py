"""EVAL-2 L4 forward demo conformance and the S5 execution read (docs/research/eval2-method-2026-11-09.md section 2,
L4 row, and section 6; signatures frozen in prereg-2026-11-09-eval2.md section 4.9).

The rule, as written: "L4 forward demo (conformance, not discovery). Shadow replay of every signal (taken or not) +
demo fills. (i) Execution e-process: realised cost <= modelled + 0.02 R, paired fill vs replay v2 (section 5), per
broker and leg. Kill-only; sd ~ 0.05-0.1 R, detects in 30-100 fills. (ii) Exit-mix check: stop/target/time shares vs
the history Dirichlet, ~ 50 trades. (iii) Futility e-process: e >= 10 against H1: excess >= max(0.05 R, 1/2 x L3
shrunk estimate), after >= 100 signals, gives KILLED. (iv) Edge e-process vs H0: excess <= 0. Reported. e >= 20 =
'proven forward' (rare inside a year). ON TRACK = (i)-(iii) green after >= 50 fills per family." "`ForwardState` reads
count only when `s5_passed` is true for the broker and TF band."

The e-processes. Each is a one-sided test of H0: E[x] <= 0 against E[x] > 0 on a stream x_1, x_2, ...:
* execution: x = (replay - fill) - 0.02 R, i.e. the realised cost over the modelled one beyond the tolerance;
* futility:  x = H1 - excess (the shortfall from H1);
* edge:      x = excess.
Wealth E_t = prod_i exp(l_i y_i - psi_i): y_i = min(x_i, b_i) is x winsorised from above at b_i = 3 sigma_i (only ever
lowering the mean, so H0 still holds for y), l_i the predictable plug-in bet (Waudby-Smith and Ramdas 2024, "PrPl")
l_i = min(sqrt(2 log(1/0.05) / (sigma_i^2 i log(i + 1))), 1 / b_i), and psi_i = sigma_i^2 (e^(l_i b_i) - 1 - l_i b_i)
/ b_i^2 Bennett's bound for a variable bounded above by b_i with second moment sigma_i^2. sigma_i^2 is the second
moment of y_1..y_(i-1) shrunk towards a prior sd (one pseudo-observation), so every term uses only the past; no bet is
placed on the first BURN_IN values. Under H0
E_t is a supermartingale up to the plug-in second moment, so Ville gives P(sup_t E_t >= 1/a) <= a; the plug-in makes
that approximate, and the tests check the crossing rate by simulation (Gaussian and Student-t3 noise at the H0
boundary). Each function returns the running maximum of E_t (a crossing is final: kill-only).

Thresholds: KILLED by futility at e >= 10 (method), by execution at e >= 20 (Ville <= 5 %, the method's "Ville bound
<= 5 %"); "proven forward" at e >= 20. The exit mix: a Dirichlet-multinomial chi-square against the history prior,
judged from 50 fills at p >= 0.01. The S5 band split and its 50-pair floor and the exit-mix threshold are this lane's,
proposed in the method's v7 section for the quant's sign-off.

S5 (method section 6): fill vs replay v2 per broker and timeframe band, gold only, |bias| <= 0.02 R. Quant ruling
2026-11-09 (EVAL-2 v7): "S5 on vantage is read on the entry leg (fill vs replay, spread) only. Its exit term is not read
on demo ... Vantage S5 cannot be green on the exit leg from demo data."
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy import stats

from goldbot.execution.costs import DEMO_EXIT_AT_LEVEL

ALPHA_BET = 0.05                 # the level the plug-in bet is tuned for
WINSOR_SD = 3.0                  # y = min(x, 3 sigma): conservative under H0
BURN_IN = 5                      # no bet on the first 5 values: sigma comes from data, not only the prior (an sd 10x the
                                 # prior crossed 8 % without it, 3 % with it; power at 100 fills 99.7 % -> 99.6 %)
SD_EXEC_R = 0.1                  # prior sd of fill - replay (method: sd ~ 0.05-0.1 R)
SD_EXCESS_R = 1.3                # prior sd of an instance's excess (method section 2: the pooled 1.3 R)
TOL_EXEC_R = 0.02
H1_FLOOR_R = 0.05
E_KILL_EXEC = 20.0
E_KILL_FUTILITY = 10.0
E_PROVEN = 20.0
MIN_SIGNALS_FUTILITY = 100
MIN_FILLS_ON_TRACK = 50
EXIT_MIX_MIN = 50
EXIT_MIX_P = 0.01

# S5 bands (the S5 decomposition read 1m/5m, 15m and 1h/4h; 30m joins 15m, 1d stands alone)
TF_BAND: dict[str, str] = {"1m": "1m-5m", "5m": "1m-5m", "15m": "15m-30m", "30m": "15m-30m", "1h": "1h-4h",
                           "4h": "1h-4h", "1d": "1d"}
TF_BANDS: tuple[str, ...] = ("1m-5m", "15m-30m", "1h-4h", "1d")
S5_TOL_R = 0.02
S5_MIN_PAIRS = 50

Status = Literal["ON TRACK", "DRIFTING", "KILLED", "WAITING"]


@dataclass(frozen=True, slots=True)
class ForwardState:
    family: str
    n_signals: int
    n_fills: int
    e_exec: float
    exit_mix_ok: bool
    e_futility: float
    e_edge: float
    status: Status


@dataclass(frozen=True, slots=True)
class S5Read:
    broker: str
    tf_band: str
    legs: Literal["all", "entry"]       # vantage: the entry leg only (quant ruling 2026-11-09)
    n: int
    bias_r: float                       # mean fill - replay v2, + = the fill did better than the replay
    se_r: float                         # clustered by session day
    passed: bool
    exit_leg_read: bool                 # False: this broker's exit leg cannot be read from demo
    reason: str


# ---------------------------------------------------------------------------------------------- e-processes
def _wealth(x: np.ndarray, *, sd0: float) -> np.ndarray:
    """E_t of the one-sided test (module docstring) for each row of `x` (paths x time), running, not maximised."""
    x = np.atleast_2d(np.asarray(x, dtype=float))
    paths, n = x.shape
    log_e = np.zeros(paths)
    sum_sq = np.zeros(paths)
    out = np.empty((paths, n))
    c = 2.0 * math.log(1.0 / ALPHA_BET)
    for i in range(n):
        m2 = (sd0 ** 2 + sum_sq) / (1.0 + i)          # second moment, shrunk to the prior by one pseudo-observation
        sd = np.sqrt(m2)
        b = WINSOR_SD * sd
        y = np.minimum(x[:, i], b)
        lam = np.minimum(np.sqrt(c / (m2 * (i + 1) * math.log(i + 2))), 1.0 / b) if i >= BURN_IN else 0.0 * b
        u = lam * b
        log_e += lam * y - m2 * (np.expm1(u) - u) / b ** 2
        sum_sq += y ** 2
        out[:, i] = log_e
    return np.exp(out)


def _sup(x: np.ndarray, *, sd0: float, start: int = 0) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) <= start:
        return 1.0
    return float(max(1.0, _wealth(x, sd0=sd0)[0, start:].max()))


def e_execution(fill_minus_replay_r: np.ndarray, *, tol_r: float = TOL_EXEC_R) -> float:
    """L4(i), kill-only: evidence that the realised cost exceeds the modelled one by more than `tol_r`, from paired
    fill - replay v2 labels (R; + = the fill did better). The running maximum of the e-process."""
    d = np.asarray(fill_minus_replay_r, dtype=float)
    return _sup(-d - tol_r, sd0=SD_EXEC_R)


def exit_mix_ok(counts: Mapping[str, int], prior_alpha: Mapping[str, float]) -> bool:
    """L4(ii): the fills' exit kinds (stop/target/time) against the history Dirichlet(`prior_alpha`). Not judged
    (True) below EXIT_MIX_MIN fills; else a Dirichlet-multinomial chi-square (expected N alpha_k / A, variance inflated
    by (N + A) / (1 + A)) must keep p >= EXIT_MIX_P. A kind the history never had is refused (ValueError)."""
    if unknown := set(counts) - set(prior_alpha):
        raise ValueError(f"exit kinds {sorted(unknown)} have no history prior")
    keys = sorted(prior_alpha)
    alpha = np.array([float(prior_alpha[k]) for k in keys])
    if np.any(alpha <= 0) or not np.all(np.isfinite(alpha)):
        raise ValueError("the history prior needs a positive alpha per exit kind")
    obs = np.array([float(counts.get(k, 0)) for k in keys])
    n, a = float(obs.sum()), float(alpha.sum())
    if n < EXIT_MIX_MIN:
        return True
    expected = n * alpha / a
    chi2 = float(np.sum((obs - expected) ** 2 / expected) / ((n + a) / (1.0 + a)))
    return bool(stats.chi2.sf(chi2, len(keys) - 1) >= EXIT_MIX_P)


def e_futility(excess: np.ndarray, *, h1_r: float, min_signals: int = MIN_SIGNALS_FUTILITY) -> float:
    """L4(iii): evidence against H1 (excess >= `h1_r`); KILLED at >= E_KILL_FUTILITY. Counts only from the
    `min_signals`-th signal (1.0 before): the running maximum from there."""
    x = np.asarray(excess, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < min_signals:
        return 1.0
    return _sup(h1_r - x, sd0=SD_EXCESS_R, start=min_signals - 1)


def e_edge(excess: np.ndarray) -> float:
    """L4(iv), reported: evidence against H0 (excess <= 0); E_PROVEN = "proven forward"."""
    return _sup(np.asarray(excess, dtype=float), sd0=SD_EXCESS_R)


def forward_state(units: pd.DataFrame, *, family: str, l3_shrunk_r: float, s5_passed: bool,
                  exit_prior: Mapping[str, float] | None = None) -> ForwardState:
    """One family's L4 read. `units`: the frozen columns (prereg 4.3) of every forward signal, taken or not, plus for
    the taken ones `fill_minus_replay_r` (R, + = the fill better than replay v2) and `exit_kind` (stop/target/time).
    `exit_prior`: the history Dirichlet of the exit kinds (without one the family is never ON TRACK).

    WAITING until S5 passes for the broker and band (no forward read counts before it). Then KILLED when futility
    (after MIN_SIGNALS_FUTILITY signals) or execution crosses its level; WAITING below MIN_FILLS_ON_TRACK fills or
    without a prior; DRIFTING when the exit mix is off the history; else ON TRACK."""
    u = units[units["family"] == family].sort_values("decision_ns", kind="stable")
    excess = u["excess_r"].to_numpy(dtype=float)
    gap = (u["fill_minus_replay_r"] if "fill_minus_replay_r" in u else pd.Series(np.nan, index=u.index)).astype(float)
    filled = u[gap.notna()]
    kinds: dict[str, int] = ({str(k): int(v) for k, v in filled["exit_kind"].dropna().astype(str).value_counts().items()}
                             if "exit_kind" in filled else {})
    mix = exit_mix_ok(kinds, exit_prior) if exit_prior is not None else False
    e_ex = e_execution(gap.dropna().to_numpy(dtype=float))
    e_fut = e_futility(excess, h1_r=max(H1_FLOOR_R, 0.5 * float(l3_shrunk_r)))
    status: Status
    if not s5_passed:
        status = "WAITING"
    elif e_fut >= E_KILL_FUTILITY or e_ex >= E_KILL_EXEC:
        status = "KILLED"
    elif len(filled) < MIN_FILLS_ON_TRACK or exit_prior is None:
        status = "WAITING"
    elif not mix:
        status = "DRIFTING"
    else:
        status = "ON TRACK"
    return ForwardState(family=family, n_signals=len(u), n_fills=len(filled), e_exec=e_ex, exit_mix_ok=mix,
                        e_futility=e_fut, e_edge=e_edge(excess), status=status)


# ---------------------------------------------------------------------------------------------- S5
def _day_se(x: np.ndarray, day: np.ndarray) -> float:
    _, inv = np.unique(day, return_inverse=True)
    g = int(inv.max()) + 1 if len(inv) else 0
    if g < 2:
        return float("inf")
    sums = np.bincount(inv, weights=x - x.mean())
    return float(np.sqrt(g / (g - 1) * np.sum(sums ** 2)) / len(x))


def s5_read(pairs: pd.DataFrame, *, broker: str, tf_band: str, min_n: int = S5_MIN_PAIRS) -> S5Read:
    """S5 for one broker and band on `exploration_data.fill_replay_pairs` rows (gold, replay v2, not void): passes when
    at least `min_n` pairs and |mean fill - replay| <= S5_TOL_R. A broker in `costs.DEMO_EXIT_AT_LEVEL` (vantage) reads
    the entry leg only and its exit leg is never read from demo."""
    if tf_band not in TF_BANDS:
        raise ValueError(f"unknown TF band {tf_band!r}: one of {TF_BANDS}")
    entry_only = broker in DEMO_EXIT_AT_LEVEL
    p = pairs[(pairs["broker"] == broker) & (pairs["tf"].map(TF_BAND) == tf_band)]
    gap = (p["entry_fill_minus_replay_r"] if entry_only else p["fill_r"] - p["replay_r"]).astype(float)
    ok = gap.notna().to_numpy()
    x = gap.to_numpy()[ok]
    day = (p["session_day"] if "session_day" in p else pd.Series(range(len(p)), index=p.index)).astype(str)
    n = len(x)
    bias = float(x.mean()) if n else float("nan")
    se = _day_se(x, day.to_numpy()[ok]) if n else float("inf")
    if n < min_n:
        passed, reason = False, f"insufficient: {n} pairs < {min_n}"
    else:
        passed = abs(bias) <= S5_TOL_R
        reason = f"|bias| {abs(bias):.4f} R {'<=' if passed else '>'} {S5_TOL_R} R"
    if entry_only:
        reason += "; entry leg only (demo exits fill at the level: exit leg not read)"
    return S5Read(broker=broker, tf_band=tf_band, legs="entry" if entry_only else "all", n=n, bias_r=bias, se_r=se,
                  passed=passed, exit_leg_read=not entry_only, reason=reason)


__all__ = ["E_KILL_EXEC", "E_KILL_FUTILITY", "E_PROVEN", "TF_BAND", "TF_BANDS", "ForwardState", "S5Read", "e_edge",
           "e_execution", "e_futility", "exit_mix_ok", "forward_state", "s5_read"]

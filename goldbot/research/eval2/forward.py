"""EVAL-2 L4 forward demo conformance and the S5 execution read (docs/research/eval2-method-2026-11-09.md section 2,
L4 row, section 6 and v7 section (e); signatures frozen in prereg-2026-11-09-eval2.md section 4.9, with the quant's
conditions C1-C10 of the review of 8b2345c4).

The rule, as written: "L4 forward demo (conformance, not discovery). Shadow replay of every signal (taken or not) +
demo fills. (i) Execution e-process: realised cost <= modelled + 0.02 R, paired fill vs replay v2 (section 5), per
broker and leg. Kill-only. (ii) Exit-mix check: stop/target/time shares vs the history Dirichlet, ~ 50 trades. (iii)
Futility e-process: e >= 10 against H1: excess >= max(0.05 R, 1/2 x L3 shrunk estimate), after >= 100 signals, gives
KILLED. (iv) Edge e-process vs H0: excess <= 0. Reported. e >= 20 = 'proven forward' (rare inside a year). ON TRACK =
(i)-(iii) green after >= 50 fills per family." "`ForwardState` reads count only when `s5_passed` is true for the
broker and TF band."

The e-processes. Each is a one-sided test of H0: E[x] <= 0 against E[x] > 0, with ONE TERM PER SESSION DAY, the day's
mean of x (quant C2: fills and signals cluster within a day; per-fill terms crossed far above their level when days
share a shock):
* execution: x = (replay - fill) - 0.02 R per (broker, leg), the realised cost over the modelled one beyond the
  tolerance;
* futility:  x = H1 - excess (the shortfall from H1);
* edge:      x = excess.
Wealth E_t = prod_d exp(l_d y_d - psi_d): y_d = min(x_d, b_d) is the day mean winsorised from above at b_d = 3 sigma_d
(only ever lowering the mean, so H0 still holds for y), l_d the predictable plug-in bet (Waudby-Smith and Ramdas 2024,
"PrPl") l_d = min(sqrt(2 log(1/0.05) / (sigma_d^2 d log(d + 1))), 1 / b_d), and psi_d = sigma_d^2 (e^(l_d b_d) - 1 -
l_d b_d) / b_d^2 Bennett's bound for a variable bounded above by b_d with second moment sigma_d^2. sigma_d^2 is the
second moment of the earlier day terms shrunk towards a prior sd (one pseudo-observation), so every term uses only the
past; no bet is placed on the first BURN_IN days. Ville then gives P(sup E >= 1/a) <= a up to the plug-in second
moment; the tests check the crossing rate by simulation, day-clustered included. Each function returns the running
maximum (a crossing is final: kill-only).

Thresholds: futility KILLED at e >= 10 (method); execution (quant C3) runs one stream per (broker, leg in {entry,
exit}), never an exit stream for a broker in `costs.DEMO_EXIT_AT_LEVEL`, and KILLS when any stream's running maximum
reaches 20 x m (m = the streams formed; `ForwardState.e_exec` = that maximum / m); "proven forward" at e >= 20. The
exit mix: a Dirichlet-multinomial chi-square against the family's frozen history prior (quant C9: from its r2 shadow
labels decided before forward start, hashed in the card), judged from 50 fills at p >= 0.01. A family is never ON
TRACK before 100 signals (C9). Forward units carry broker, tf and `s5_ok` (`apply_s5`, C8): no fill column may hold a
value for a (broker, band) whose S5 has not passed. With per-day terms the execution e-process needs about 300 fills
(about 60 session days) to catch a 0.05 R overrun (C2).

S5 (method section 6 and the prereg amendment of 2026-11-09): "S5 passes per broker and TF band on at least 50 pairs
over at least 10 session days, with |mean fill - replay v2| <= 0.02 R and a lower one-sided 95 % day-clustered bound
>= -0.02 R." The 1m-5m band is informational only (C4). Vantage: "S5 on vantage is read on the entry leg (fill vs
replay, spread) only ... Vantage S5 cannot be green on the exit leg from demo data. If 95 % or more of vantage entries
fill within $0.005 of the quote, its entry leg is treated the same way and vantage S5 reads NOT AVAILABLE."
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from scipy import stats

from goldbot.execution.costs import DEMO_EXIT_AT_LEVEL, ENTRY_AT_QUOTE_SHARE

ALPHA_BET = 0.05                 # the level the plug-in bet is tuned for
WINSOR_SD = 3.0                  # y = min(x, 3 sigma): conservative under H0
BURN_IN = 5                      # no bet on the first 5 day terms: sigma comes from data, not only the prior
SD_EXEC_R = 0.1                  # prior sd of fill - replay (method: sd ~ 0.05-0.1 R)
SD_EXCESS_R = 1.3                # prior sd of an instance's excess (method section 2: the pooled 1.3 R)
TOL_EXEC_R = 0.02
H1_FLOOR_R = 0.05
E_KILL_EXEC = 20.0               # per stream: x m streams (Bonferroni over the streams formed, C3)
E_KILL_FUTILITY = 10.0
E_PROVEN = 20.0
MIN_SIGNALS_FUTILITY = 100
MIN_SIGNALS_ON_TRACK = 100       # C9
MIN_FILLS_ON_TRACK = 50
EXIT_MIX_MIN = 50
EXIT_MIX_P = 0.01
EXIT_KINDS: tuple[str, ...] = ("stop", "target", "time")
FILL_COLS: tuple[str, ...] = ("fill_minus_replay_r", "entry_fill_minus_replay_r", "exit_kind")

# S5 bands (the S5 decomposition read 1m/5m, 15m and 1h/4h; 30m joins 15m, 1d stands alone). 1m-5m is informational
TF_BAND: dict[str, str] = {"1m": "1m-5m", "5m": "1m-5m", "15m": "15m-30m", "30m": "15m-30m", "1h": "1h-4h",
                           "4h": "1h-4h", "1d": "1d"}
TF_BANDS: tuple[str, ...] = ("1m-5m", "15m-30m", "1h-4h", "1d")
INFORMATIONAL_BANDS: frozenset[str] = frozenset({"1m-5m"})
S5_TOL_R = 0.02
S5_MIN_PAIRS = 50
S5_MIN_DAYS = 10
Z_ONE_SIDED_95 = 1.6448536269514722

Status = Literal["ON TRACK", "DRIFTING", "KILLED", "WAITING"]
S5Status = Literal["PASS", "FAIL", "INSUFFICIENT", "INFORMATIONAL", "NOT AVAILABLE"]


@dataclass(frozen=True, slots=True)
class ForwardState:
    family: str
    n_signals: int
    n_fills: int
    e_exec: float                       # the largest stream's running maximum / the streams formed (C3)
    exit_mix_ok: bool
    e_futility: float
    e_edge: float
    status: Status
    exec_streams: Mapping[str, float] = field(default_factory=dict)    # "<broker>:<entry|exit>" -> running max


@dataclass(frozen=True, slots=True)
class S5Read:
    broker: str
    tf_band: str
    legs: Literal["all", "entry"]       # vantage: the entry leg only (quant ruling 2026-11-09)
    n: int
    n_days: int
    bias_r: float                       # mean fill - replay v2, + = the fill did better than the replay
    se_r: float                         # clustered by session day
    lb95_r: float                       # lower one-sided 95 % bound of the mean
    passed: bool
    exit_leg_read: bool                 # False: this broker's exit leg cannot be read from demo
    status: S5Status
    reason: str
    bias_by_exit_kind: Mapping[str, float] = field(default_factory=dict)   # C10
    unknown_arm_labels: int | None = None                                  # C10: paired rows on arms the spec omits


# ---------------------------------------------------------------------------------------------- e-processes
def _wealth(x: np.ndarray, *, sd0: float) -> np.ndarray:
    """E_t of the one-sided test (module docstring) for each row of `x` (paths x terms), running, not maximised."""
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


def day_terms(x: np.ndarray, day: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """(the mean of x per session day, the values per day), days in order of first appearance; non-finite x dropped.
    Without `day` every value is its own day."""
    x = np.asarray(x, dtype=float)
    d = np.arange(len(x)) if day is None else np.asarray(day)
    ok = np.isfinite(x)
    x, d = x[ok], d[ok]
    if not len(x):
        return np.array([], dtype=float), np.array([], dtype=int)
    codes, _ = pd.factorize(pd.Series(d), sort=False)
    sums = np.bincount(codes, weights=x)
    cnt = np.bincount(codes)
    return sums / cnt, cnt


def _sup(terms: np.ndarray, *, sd0: float, start: int = 0) -> float:
    if len(terms) <= start:
        return 1.0
    return float(max(1.0, _wealth(terms, sd0=sd0)[0, start:].max()))


def e_execution(fill_minus_replay_r: np.ndarray, *, tol_r: float = TOL_EXEC_R, day: np.ndarray | None = None) -> float:
    """L4(i), kill-only, one stream: evidence that the realised cost exceeds the modelled one by more than `tol_r`,
    from paired fill - replay v2 labels (R; + = the fill did better), one term per session `day`. Running maximum."""
    d = np.asarray(fill_minus_replay_r, dtype=float)
    terms, _ = day_terms(-d - tol_r, day)
    return _sup(terms, sd0=SD_EXEC_R)


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


def prior_hash(prior: Mapping[str, float]) -> str:
    """sha256 of an exit prior's canonical JSON: what the card records (C9)."""
    return hashlib.sha256(json.dumps({str(k): float(v) for k, v in prior.items()}, sort_keys=True).encode()).hexdigest()


def exit_prior_from_shadow(labels: pd.DataFrame, *, forward_start: pd.Timestamp) -> tuple[dict[str, float], str]:
    """Quant C9: the family's history Dirichlet from its r2 shadow labels (`label_barrier`, `decision_utc`) decided
    before `forward_start`: alpha = count + 1/2 per exit kind (Jeffreys), with its hash for the card."""
    dec = pd.to_datetime(labels["decision_utc"], utc=True)
    kinds = labels.loc[dec < forward_start, "label_barrier"].astype(str)
    prior = {k: float((kinds == k).sum()) + 0.5 for k in EXIT_KINDS}
    return prior, prior_hash(prior)


def e_futility(excess: np.ndarray, *, h1_r: float, min_signals: int = MIN_SIGNALS_FUTILITY,
               day: np.ndarray | None = None) -> float:
    """L4(iii): evidence against H1 (excess >= `h1_r`), one term per session `day`; KILLED at >= E_KILL_FUTILITY.
    Counts only from the day on which the `min_signals`-th signal falls (1.0 before): the running maximum from there."""
    x = np.asarray(excess, dtype=float)
    terms, cnt = day_terms(h1_r - x, day)
    if int(cnt.sum()) < min_signals:
        return 1.0
    start = int(np.searchsorted(np.cumsum(cnt), min_signals))
    return _sup(terms, sd0=SD_EXCESS_R, start=start)


def e_edge(excess: np.ndarray, *, day: np.ndarray | None = None) -> float:
    """L4(iv), reported: evidence against H0 (excess <= 0), one term per session `day`; E_PROVEN = "proven forward"."""
    terms, _ = day_terms(np.asarray(excess, dtype=float), day)
    return _sup(terms, sd0=SD_EXCESS_R)


def execution_streams(fills: pd.DataFrame) -> dict[str, float]:
    """C3: one execution e-process per (broker, leg): `entry` on entry_fill_minus_replay_r, `exit` on the rest of
    fill_minus_replay_r, never `exit` for a broker in DEMO_EXIT_AT_LEVEL. Only streams with a value are formed."""
    out: dict[str, float] = {}
    for broker, g in fills.groupby("broker", sort=True):
        entry = g["entry_fill_minus_replay_r"].astype(float)
        streams = {"entry": entry}
        if str(broker) not in DEMO_EXIT_AT_LEVEL:
            streams["exit"] = g["fill_minus_replay_r"].astype(float) - entry
        for leg, v in streams.items():
            ok = v.notna().to_numpy()
            if ok.any():
                out[f"{broker}:{leg}"] = e_execution(v.to_numpy()[ok], day=g["day"].to_numpy()[ok])
    return out


def apply_s5(units: pd.DataFrame, passed: Mapping[tuple[str, str], bool]) -> pd.DataFrame:
    """C8: the units with `s5_ok` per row (S5 passed for its (broker, TF band)) and every fill column blanked where it
    has not, so no forward read uses a fill S5 has not cleared."""
    out = units.copy()
    ok = np.array([bool(passed.get((str(b), TF_BAND.get(str(t), "")), False))
                   for b, t in zip(out["broker"], out["tf"], strict=True)], dtype=bool)
    out["s5_ok"] = ok
    for c in FILL_COLS:
        if c in out:
            out[c] = out[c].astype(object).where(ok, None) if c == "exit_kind" else out[c].astype(float).where(ok)
    return out


def _check_units(u: pd.DataFrame) -> None:
    missing = {"broker", "tf", "day", "s5_ok"} - set(u.columns)
    if missing:
        raise ValueError(f"forward units need {sorted(missing)} (broker, tf and `apply_s5`'s s5_ok: quant C8)")
    bad = ~u["s5_ok"].astype(bool)
    for c in FILL_COLS:
        if c in u and u.loc[bad, c].notna().any():
            raise ValueError(f"forward units carry {c} where S5 has not passed for the (broker, band): use apply_s5")


def forward_state(units: pd.DataFrame, *, family: str, l3_shrunk_r: float, s5_passed: bool,
                  exit_prior: Mapping[str, float] | None = None, exit_prior_hash: str | None = None) -> ForwardState:
    """One family's L4 read. `units`: the frozen columns (prereg 4.3) of every forward signal, taken or not, plus
    `broker` and `s5_ok` (`apply_s5`) and, for the taken ones, `fill_minus_replay_r` and `entry_fill_minus_replay_r`
    (R, + = the fill better than replay v2) and `exit_kind`. `exit_prior` and `exit_prior_hash`: the card's frozen
    history Dirichlet and its hash (`exit_prior_from_shadow`); without them the family is never ON TRACK.

    WAITING until S5 passes (no forward read counts before it). Then KILLED when futility (after
    MIN_SIGNALS_FUTILITY signals) or any execution stream crosses; WAITING below MIN_SIGNALS_ON_TRACK signals or
    MIN_FILLS_ON_TRACK fills, or without a prior; DRIFTING when the exit mix is off the history; else ON TRACK."""
    _check_units(units)
    if exit_prior is not None and (exit_prior_hash is None or prior_hash(exit_prior) != exit_prior_hash):
        raise ValueError("the exit prior does not match the hash frozen in the card")
    u = units[units["family"] == family].sort_values("decision_ns", kind="stable")
    excess = u["excess_r"].to_numpy(dtype=float)
    day = u["day"].astype(str).to_numpy()
    gap = (u["fill_minus_replay_r"] if "fill_minus_replay_r" in u else pd.Series(np.nan, index=u.index)).astype(float)
    filled = u[gap.notna()]
    if not filled.empty and "entry_fill_minus_replay_r" not in filled:
        raise ValueError("forward fills need entry_fill_minus_replay_r (one execution stream per leg, quant C3)")
    kinds: dict[str, int] = ({str(k): int(v) for k, v in filled["exit_kind"].dropna().astype(str).value_counts().items()}
                             if "exit_kind" in filled else {})
    mix = exit_mix_ok(kinds, exit_prior) if exit_prior is not None else False
    streams = execution_streams(filled) if not filled.empty else {}
    e_ex = max(streams.values()) / len(streams) if streams else 1.0
    e_fut = e_futility(excess, h1_r=max(H1_FLOOR_R, 0.5 * float(l3_shrunk_r)), day=day)
    status: Status
    if not s5_passed:
        status = "WAITING"
    elif e_fut >= E_KILL_FUTILITY or e_ex >= E_KILL_EXEC:
        status = "KILLED"
    elif len(u) < MIN_SIGNALS_ON_TRACK or len(filled) < MIN_FILLS_ON_TRACK or exit_prior is None:
        status = "WAITING"
    elif not mix:
        status = "DRIFTING"
    else:
        status = "ON TRACK"
    return ForwardState(family=family, n_signals=len(u), n_fills=len(filled), e_exec=e_ex, exit_mix_ok=mix,
                        e_futility=e_fut, e_edge=e_edge(excess, day=day), status=status, exec_streams=streams)


# ---------------------------------------------------------------------------------------------- S5
def _day_se(x: np.ndarray, day: np.ndarray) -> float:
    _, inv = np.unique(day, return_inverse=True)
    g = int(inv.max()) + 1 if len(inv) else 0
    if g < 2:
        return float("inf")
    sums = np.bincount(inv, weights=x - x.mean())
    return float(np.sqrt(g / (g - 1) * np.sum(sums ** 2)) / len(x))


def s5_read(pairs: pd.DataFrame, *, broker: str, tf_band: str, min_n: int = S5_MIN_PAIRS,
            min_days: int = S5_MIN_DAYS, entry_at_quote_share: float | None = None) -> S5Read:
    """S5 for one broker and band on `exploration_data.fill_replay_pairs` rows (gold, replay v2, not void), by the
    prereg amendment of 2026-11-09 (module docstring). A broker in `costs.DEMO_EXIT_AT_LEVEL` reads the entry leg only,
    never its exit leg, and reads NOT AVAILABLE when `entry_at_quote_share` (`ReplayCostsV2.entry_at_quote_share`) is
    ENTRY_AT_QUOTE_SHARE or more. The 1m-5m band is INFORMATIONAL (never passes). Reports the bias by the fill's exit
    kind and the paired rows on arms the spec does not name (`pairs.attrs["unknown_arm_labels"]`)."""
    if tf_band not in TF_BANDS:
        raise ValueError(f"unknown TF band {tf_band!r}: one of {TF_BANDS}")
    unknown = pairs.attrs.get("unknown_arm_labels")
    entry_only = broker in DEMO_EXIT_AT_LEVEL
    p = pairs[(pairs["broker"] == broker) & (pairs["tf"].map(TF_BAND) == tf_band)]
    gap = (p["entry_fill_minus_replay_r"] if entry_only else p["fill_r"] - p["replay_r"]).astype(float)
    ok = gap.notna().to_numpy()
    x = gap.to_numpy()[ok]
    day = (p["session_day"] if "session_day" in p else pd.Series(range(len(p)), index=p.index)).astype(str).to_numpy()[ok]
    n, n_days = len(x), len(set(day))
    bias = float(x.mean()) if n else float("nan")
    se = _day_se(x, day) if n else float("inf")
    lb = bias - Z_ONE_SIDED_95 * se if n else float("nan")
    by_kind: dict[str, float] = {}
    if "exit_kind" in p and n:
        kinds = p["exit_kind"].astype(str).to_numpy()[ok]
        by_kind = {str(k): float(x[kinds == k].mean()) for k in sorted(set(kinds))}
    status: S5Status
    if entry_only and entry_at_quote_share is not None and entry_at_quote_share >= ENTRY_AT_QUOTE_SHARE:
        status = "NOT AVAILABLE"
        reason = f"{entry_at_quote_share:.1%} of entries fill within $0.005 of the quote: entry leg not evidence"
    elif n < min_n or n_days < min_days:
        status, reason = "INSUFFICIENT", f"insufficient: {n} pairs over {n_days} session days (needs {min_n} / {min_days})"
    else:
        good = abs(bias) <= S5_TOL_R and lb >= -S5_TOL_R
        reason = (f"|bias| {abs(bias):.4f} R, lower 95 % bound {lb:+.4f} R: "
                  f"{'within' if good else 'outside'} +-{S5_TOL_R} R")
        status = "PASS" if good else "FAIL"
        if tf_band in INFORMATIONAL_BANDS:
            status, reason = "INFORMATIONAL", reason + "; 1m-5m is informational only"
    if entry_only:
        reason += "; entry leg only (demo exits fill at the level: exit leg not read)"
    return S5Read(broker=broker, tf_band=tf_band, legs="entry" if entry_only else "all", n=n, n_days=n_days,
                  bias_r=bias, se_r=se, lb95_r=lb, passed=status == "PASS", exit_leg_read=not entry_only,
                  status=status, reason=reason, bias_by_exit_kind=by_kind,
                  unknown_arm_labels=int(unknown) if unknown is not None else None)


__all__ = ["E_KILL_EXEC", "E_KILL_FUTILITY", "E_PROVEN", "TF_BAND", "TF_BANDS", "ForwardState", "S5Read", "apply_s5",
           "day_terms", "e_edge", "e_execution", "e_futility", "execution_streams", "exit_mix_ok",
           "exit_prior_from_shadow", "forward_state", "prior_hash", "s5_read"]

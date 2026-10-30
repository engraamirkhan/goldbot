"""The search-level honesty set of autonomous discovery (ADR 0058 phase 2, design section 3.2 steps 6-10).

Pure functions over trial result arrays. The batch statistics read one matrix of DAILY excess net R, one column per
spec that passed the floors: the net R of the spec's events minus the random-entry drift baseline of the same hour and
side (`excess_daily`), summed per session day (0 on a day without an event). Excess over the drift baseline is the
performance, so gold's long drift cannot make a long-biased rule look clever and the benchmark below is a plain zero.

- `reality_check`: White (2000) reality check and Hansen (2005) SPA over the whole logged batch, with the stationary
  bootstrap of Politis and Romano (1994), mean block 5 days, 1,000 draws. The RC p-value is what a batch of many weak
  specs is judged by; the SPA (consistent version: only clearly poor specs, below -sqrt(2 log log n) standard errors,
  keep their own mean under the null) is not diluted by them. Design 3.2 step 8: "the batch must reject at 10% before
  any member advances" (`batch_passes`, on the SPA).
- `batch_pbo`: the probability of backtest overfitting (CSCV, `cpcv.pbo`) of the batch's `PBO_TOP` (50) best by fitness
  over `PBO_GROUPS` contiguous time groups. Step 9: "PBO <= 0.30, otherwise nothing from the batch advances".
- `batch_dsr`: the deflated Sharpe ratio (Bailey and Lopez de Prado 2014, `metrics.deflated_sharpe`) of each named spec
  at K from the ledger (`SearchLedger.k_search`, the raw count of distinct specs; K_eff of the design is not built, so
  this DSR is the conservative one), with the spec's own skew and kurtosis and the cross-trial variance of the batch's
  Sharpe ratios. Step 10: "informational at this stage; binding at confirm": the confirm bar is the gates' 0.95
  (`DSR_CONFIRM_BAR`), read at the cumulative K_search of every night (ADR 0058 amendment, 2026-10-30).
- `circular_shift_null`: the spec's signal rotated against the returns by random offsets of WHOLE WEEKS in calendar
  time (whole days when the sample spans fewer than `CS_MIN_WEEKS` weeks), so a shifted event keeps its UTC hour and
  weekday, and scored in excess R over the drift baseline of its hour and side (quant review F1, 2026-10-30: a shift by
  any bar offset moved a time-leaf signal onto other hours and scored raw R, so a spec riding an hour or session drift
  beat its own null). The signal keeps its clustering and density, only its alignment with the outcomes is broken;
  p = (1 + #{shifted >= true}) / (1 + shifts).
- `plateau_check`: step 6, "at least 60% of the spec's grid neighbours (each parameter moved +-1 step) have a positive
  net fitness", and also the neighbours' mean fitness at least `PLATEAU_MIN_RATIO` of the peak (a knife-edge optimum
  with barely positive neighbours fails).

An output here is a recorded fact about a batch, never evidence of edge."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import stats as sps

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns
from goldbot.research import gates
from goldbot.research.autodisc.evaluate import PLATEAU_MIN_SHARE
from goldbot.research.autodisc.outcomes import OutcomeTensor
from goldbot.research.cpcv import pbo
from goldbot.research.metrics import DsrInputs

FloatArr = NDArray[np.float64]
IntArr = NDArray[np.int64]

BOOT_DRAWS = 1000
MEAN_BLOCK_DAYS = 5
RC_ALPHA = 0.10
PBO_TOP = 50
PBO_GROUPS = 12
PBO_MAX = 0.30
CS_SHIFTS = 199              # p resolution 1/200
CS_ALPHA = 0.05
CS_MIN_WEEKS = 40            # whole-week shifts need >= ~2/CS_ALPHA admissible offsets; a shorter sample uses days
ShiftUnit = Literal["week", "day"]
_UNIT_NS: dict[str, int] = {"week": 7 * 86_400_000_000_000, "day": 86_400_000_000_000}
PLATEAU_MIN_RATIO = 0.5
DSR_CONFIRM_BAR = gates.DSR_BAR      # the gates' 0.95: what advancing to confirm requires, at the cumulative K
_CHUNK = 1000                # specs per bootstrap matrix product (memory: draws x chunk floats)


class RealityCheck(FrozenRecord):
    rc_p: float
    spa_p: float
    n_days: int
    n_specs: int
    draws: int


class PBOResult(FrozenRecord):
    pbo: float
    n_configs: int
    n_combinations: int
    logit_median: float


class ShiftNull(FrozenRecord):
    true_mean_r: float                  # mean excess R over the drift baseline (raw R when no baseline is given)
    p_value: float
    n_events: int
    null_q95: float
    unit: ShiftUnit
    shifts: tuple[int, ...]             # in whole `unit`s


class Plateau(FrozenRecord):
    peak: float | None
    n_neighbours: int
    share_positive: float
    neighbour_mean: float | None
    ratio: float | None
    passed: bool


def excess_daily(t: OutcomeTensor, events_r: FloatArr, ev_idx: IntArr, ev_side_i: IntArr, exit_i: int,
                 day_id: IntArr, n_days: int) -> FloatArr:
    """Daily excess net R of one spec: each event's R less the drift baseline of its (side, UTC hour), per session day.
    `events_r` / `ev_idx` / `ev_side_i` are the kept events' R, bar positions and side indices (`outcomes.Events`)."""
    tab = t.drift_table(exit_i)
    base = np.nan_to_num(tab[ev_side_i, t.ts_utc.hour.to_numpy()[ev_idx]], nan=0.0)
    return np.bincount(day_id[ev_idx], weights=np.asarray(events_r, dtype=float) - base, minlength=n_days).astype(float)


def stationary_bootstrap_indices(n: int, draws: int, mean_block: float, seed: int) -> IntArr:
    """(draws, n) day indices of the stationary bootstrap: each draw walks forward one day at a time and, with
    probability 1 / mean_block, jumps to a uniformly random day (wrapping at the end), so blocks have a geometric length
    with that mean. Deterministic in `seed`."""
    rng = np.random.default_rng(seed)
    restart = rng.random((draws, n)) < 1.0 / mean_block
    restart[:, 0] = True
    jump = rng.integers(0, n, (draws, n))
    pos = np.arange(n)
    last = np.maximum.accumulate(np.where(restart, pos, 0), axis=1)          # position of the latest restart
    return np.asarray((np.take_along_axis(jump, last, axis=1) + (pos - last)) % n, dtype=np.int64)


def stationary_bootstrap_weights(n: int, draws: int, mean_block: float, seed: int) -> FloatArr:
    """(draws, n) resampling weights (counts / n) of `stationary_bootstrap_indices`: `w @ x` is each draw's mean."""
    idx = stationary_bootstrap_indices(n, draws, mean_block, seed)
    rows = np.repeat(np.arange(draws), n)
    counts = np.bincount(rows * n + idx.ravel(), minlength=draws * n).reshape(draws, n)
    return counts / float(n)


def reality_check(d: FloatArr, *, draws: int = BOOT_DRAWS, mean_block: float = MEAN_BLOCK_DAYS, seed: int = 0
                  ) -> RealityCheck:
    """White RC and Hansen SPA p-values of `d` (days x specs, daily excess R against a zero benchmark). A large p: the
    best spec is what the best of this many weak specs looks like by chance. NaN is refused, never dropped."""
    d = np.asarray(d, dtype=float)
    n, k = d.shape
    if n < 20 or k < 1:
        raise ValueError(f"reality check needs >= 20 days and >= 1 spec, got {n} x {k}")
    if not np.isfinite(d).all():
        raise ValueError("reality check needs finite daily series")
    dbar = d.mean(axis=0)
    w = stationary_bootstrap_weights(n, draws, mean_block, seed)
    root = np.sqrt(n)
    boot_rc = np.full(draws, -np.inf)
    omega = np.empty(k)
    means: list[FloatArr] = []
    for lo in range(0, k, _CHUNK):
        dstar = w @ d[:, lo:lo + _CHUNK]                                    # (draws, chunk) bootstrap means
        boot_rc = np.maximum(boot_rc, (root * (dstar - dbar[lo:lo + _CHUNK])).max(axis=1))
        omega[lo:lo + _CHUNK] = (root * dstar).std(axis=0)
        means.append(dstar)
    omega = np.maximum(omega, 1e-12)
    # Hansen's SPA_c: a spec keeps its own (negative) mean under the null only when it is clearly poor
    poor = root * dbar / omega < -np.sqrt(2.0 * np.log(np.log(n)))
    g = np.where(poor, dbar, 0.0)
    spa_stat = max(float((root * dbar / omega).max()), 0.0)
    boot_spa = np.zeros(draws)
    for j, dstar in enumerate(means):
        sl = slice(j * _CHUNK, j * _CHUNK + dstar.shape[1])
        boot_spa = np.maximum(boot_spa, (root * (dstar - dbar[sl] + g[sl]) / omega[sl]).max(axis=1))
    return RealityCheck(rc_p=float(np.mean(boot_rc >= root * dbar.max())), spa_p=float(np.mean(boot_spa >= spa_stat)),
                        n_days=n, n_specs=k, draws=draws)


def batch_pbo(d: FloatArr, fitness: Sequence[float] | FloatArr, *, top: int = PBO_TOP, groups: int = PBO_GROUPS
              ) -> PBOResult | None:
    """CSCV PBO of the `top` best specs by `fitness` (columns of `d`, days x specs), each spec's daily excess R summed
    over `groups` contiguous time groups. None below two specs or fewer days than groups (a missing PBO never passes)."""
    d = np.asarray(d, dtype=float)
    n, k = d.shape
    if k < 2 or n < groups:
        return None
    order = np.argsort(-np.asarray(fitness, dtype=float), kind="stable")[:top]
    perf = np.stack([np.array([c.sum() for c in np.array_split(d[:, j], groups)]) for j in order])
    out = pbo(perf)
    return None if out is None else PBOResult(**out)


def batch_dsr_inputs(d: FloatArr, columns: Sequence[int]) -> dict[int, DsrInputs]:
    """The DSR inputs of each listed column of `d` (daily excess R): the per-day Sharpe ratio, the days, the column's
    own skew and (non-excess) kurtosis, and the variance of the Sharpe ratios across the batch's columns. A column
    with no variance has Sharpe 0. Stored with each survivor so the DSR can be re-read at a later K (quant F2)."""
    d = np.asarray(d, dtype=float)
    n = d.shape[0]
    sd = d.std(axis=0)
    sr = np.divide(d.mean(axis=0), sd, out=np.zeros(d.shape[1]), where=sd > 0)
    var_sr = float(sr.var()) if len(sr) > 1 else 0.0
    out = {}
    for c in columns:
        x = d[:, c]
        skew = float(sps.skew(x)) if sd[c] > 0 else 0.0
        kurt = float(sps.kurtosis(x, fisher=False)) if sd[c] > 0 else 3.0
        out[c] = DsrInputs(sr=float(sr[c]), n=max(n, 1), skew=skew, kurtosis=kurt, var_sr_trials=var_sr)
    return out


def batch_dsr(d: FloatArr, columns: Sequence[int], k_search: int) -> dict[int, float]:
    """Deflated Sharpe probability of each listed column of `d` (daily excess R) at K = `k_search`: the per-day Sharpe
    ratio against the expected best of K unskilled trials (`batch_dsr_inputs`)."""
    return {c: inp.dsr(max(k_search, 1)) for c, inp in batch_dsr_inputs(d, columns).items()}


def _side_mean(side: NDArray[np.int8], r_long: FloatArr, r_short: FloatArr) -> tuple[float, int]:
    r = np.where(side > 0, r_long, np.where(side < 0, r_short, np.nan))
    ok = np.isfinite(r)
    return (float(r[ok].mean()) if ok.any() else float("nan")), int(ok.sum())


def drift_per_bar(ts_utc: pd.DatetimeIndex, r_long: FloatArr, r_short: FloatArr) -> tuple[FloatArr, FloatArr]:
    """The drift baseline of every bar for each side: the mean R of all bars of the same UTC hour on that side (as
    `OutcomeTensor.drift_table`), 0 where an hour has no outcome. The shift null scores R less this."""
    hour = pd.DatetimeIndex(ts_utc).hour.to_numpy()
    out = []
    for r in (np.asarray(r_long, dtype=float), np.asarray(r_short, dtype=float)):
        ok = np.isfinite(r)
        tot = np.bincount(hour[ok], weights=r[ok], minlength=24)
        cnt = np.bincount(hour[ok], minlength=24)
        tab = np.divide(tot, cnt, out=np.zeros(24), where=cnt > 0)
        out.append(tab[hour])
    return out[0], out[1]


def shift_unit(ts_utc: pd.DatetimeIndex) -> ShiftUnit:
    """Whole weeks when the sample spans at least `CS_MIN_WEEKS`, else whole days (module docstring)."""
    ns = epoch_ns(ts_utc)
    return "week" if len(ns) and ns[-1] - ns[0] >= CS_MIN_WEEKS * _UNIT_NS["week"] else "day"


def _n_units(ns: IntArr, unit: ShiftUnit) -> int:
    return int(-(-(int(ns[-1]) - int(ns[0]) + 1) // _UNIT_NS[unit]))           # ceil: the period is whole units


def shift_signal(side: NDArray[np.int8], ts_utc: pd.DatetimeIndex, k: int, unit: ShiftUnit) -> NDArray[np.int8]:
    """`side` moved `k` whole units later in calendar time, wrapping over a period of whole units from the first bar,
    so every event keeps its UTC hour (and, for weeks, its weekday). An event whose moved time has no bar (a holiday,
    a gap) is dropped, never moved to a neighbouring bar."""
    side = np.asarray(side, dtype=np.int8)
    ns = epoch_ns(ts_utc)
    period = _n_units(ns, unit) * _UNIT_NS[unit]
    ev = np.flatnonzero(side != 0)
    moved = ns[0] + (ns[ev] - ns[0] + k * _UNIT_NS[unit]) % period
    pos = np.minimum(np.searchsorted(ns, moved), len(ns) - 1)
    ok = ns[pos] == moved
    out = np.zeros(len(side), dtype=np.int8)
    out[pos[ok]] = side[ev[ok]]
    return out


def circular_shift_null(side: NDArray[np.int8], r_long: FloatArr, r_short: FloatArr, ts_utc: pd.DatetimeIndex, *,
                        base_long: FloatArr | None = None, base_short: FloatArr | None = None,
                        n_shifts: int = CS_SHIFTS, min_shift: int | None = None, seed: int = 0) -> ShiftNull:
    """Mean excess R of the signal (`side` per bar: +1 long, -1 short, 0 none; outcome per bar `r_long` / `r_short`,
    NaN where there is none; drift baseline per bar `base_long` / `base_short`, default 0) against the same signal moved
    by `n_shifts` distinct whole-unit offsets (`shift_signal`; unit by `shift_unit`) in [min_shift, units - min_shift]
    (default units // 20, so a shift never leaves the signal near its own alignment). Overlapping events are all
    counted: this tests the alignment of signal and outcome, the evaluator's one-at-a-time book is read elsewhere.
    A rule that is a pure calendar pattern maps onto itself under whole-week shifts and cannot pass: its timing alone
    is not alignment evidence (quant review F1, 2026-10-30) (whole-week mode; the day fallback is for synthetic/short
    samples only)."""
    side = np.asarray(side, dtype=np.int8)
    n = len(side)
    ts = pd.DatetimeIndex(ts_utc)
    if len(ts) != n:
        raise ValueError("ts_utc must have one timestamp per bar")
    zero = np.zeros(n)
    e_long = np.asarray(r_long, dtype=float) - (zero if base_long is None else np.asarray(base_long, dtype=float))
    e_short = np.asarray(r_short, dtype=float) - (zero if base_short is None else np.asarray(base_short, dtype=float))
    unit = shift_unit(ts)
    units = _n_units(epoch_ns(ts), unit)
    lo = max(1, units // 20 if min_shift is None else min_shift)
    pool = np.arange(lo, units - lo + 1)
    if len(pool) < 1:
        raise ValueError(f"no admissible shift for {units} {unit}s at min_shift {lo}")
    shifts = np.sort(np.random.default_rng(seed).choice(pool, size=min(n_shifts, len(pool)), replace=False))
    true, n_ev = _side_mean(side, e_long, e_short)
    null = np.array([_side_mean(shift_signal(side, ts, int(k), unit), e_long, e_short)[0] for k in shifts])
    null = np.where(np.isfinite(null), null, -np.inf)
    p = (1 + int((null >= true).sum())) / (1 + len(shifts)) if np.isfinite(true) else 1.0
    return ShiftNull(true_mean_r=true, p_value=float(p), n_events=n_ev, unit=unit,
                     null_q95=float(np.quantile(null, 0.95, method="higher")), shifts=tuple(int(k) for k in shifts))


def plateau_check(peak: float | None, neighbours: Sequence[float | None], *, min_share: float = PLATEAU_MIN_SHARE,
                  min_ratio: float = PLATEAU_MIN_RATIO) -> Plateau:
    """Neighbour fitness (None: unreadable or failed floors, counted as not positive and as zero in the mean) against
    the peak: passes when the peak is positive, at least `min_share` of the neighbours are positive and their mean is
    at least `min_ratio` x the peak. No neighbours: fails (nothing shows a plateau)."""
    m = len(neighbours)
    vals = np.array([v if v is not None and np.isfinite(v) else 0.0 for v in neighbours], dtype=float)
    share = float(sum(1 for v in neighbours if v is not None and np.isfinite(v) and v > 0) / m) if m else 0.0
    mean = float(vals.mean()) if m else None
    ratio = mean / peak if (mean is not None and peak is not None and peak > 0) else None
    passed = bool(m and peak is not None and peak > 0 and share >= min_share and ratio is not None and ratio >= min_ratio)
    return Plateau(peak=peak, n_neighbours=m, share_positive=share, neighbour_mean=mean, ratio=ratio, passed=passed)


def batch_passes(rc: RealityCheck | None, pbo_info: PBOResult | None) -> tuple[bool, list[str]]:
    """(advance?, reasons it does not): the SPA must reject at `RC_ALPHA` and PBO must be <= `PBO_MAX`. A missing
    statistic (too few specs or days to compute it) is a failure, never a pass."""
    why = []
    if rc is None:
        why.append("reality check not computed")
    elif rc.spa_p > RC_ALPHA:
        why.append(f"SPA p {rc.spa_p:.3f} > {RC_ALPHA:.2f}")
    if pbo_info is None:
        why.append("PBO not computed")
    elif pbo_info.pbo > PBO_MAX:
        why.append(f"PBO {pbo_info.pbo:.2f} > {PBO_MAX:.2f}")
    return not why, why

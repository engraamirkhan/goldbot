"""The explore-stage evaluator of autonomous discovery (ADR 0058, design section 3.2 steps 3-6 and 11), run on the outcome
tensor (`outcomes.OutcomeTensor`). It reads nothing outside the explore window: the tensor is built on explore bars only.

Per spec (a boolean entry mask, a side rule and an exit template of the tensor):

- **Floors** (no return read): events >= 300 on 15m/1h, 150 on 4h, 100 on 1d (retire-only, as `screen.py`); time in
  market <= 40% of bars.
- **Fitness:** the minimum over `N_FOLDS` (4) purged, embargoed time folds of the net mean-R t-statistic clustered by
  session day (UTC date of the entry). Purge: an event whose life crosses its fold's end is dropped. Embargo: events that
  enter within the timeframe's walk-forward `embargo_days` after a fold's start (not the first fold's) are dropped.
  Positions are one at a time over the whole window first, so the folds partition one position sequence.
- **Side split:** long-only and short-only net R of the same events (`screen.rule_only_split`'s reading).
- **Drift baseline:** excess net R over the random-entry baseline of the same timing (UTC hour) and side: the mean R of
  every bar at that hour and side (`OutcomeTensor.drift_table`), averaged over the spec's events. Gold's drift cannot
  make a long-biased rule look clever.
- **Plateau:** `plateau_test` on the fitness of the spec's grid neighbours (each parameter +-1 step; the generator
  evaluates the neighbours and passes their fitness): at least 60% positive.
- **Cost stress:** net mean R at `STRESS_MULT` (1.5) x the conservative cost must be > 0.

Search-level checks (null percentile, White RC/SPA, PBO, DSR, novelty) are other slices and are not decided here.
An evaluation is a recorded fact, never evidence of edge.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns
from goldbot.research.autodisc.ledger import LedgerError, SearchLedger
from goldbot.research.autodisc.outcomes import Events, OutcomeTensor
from goldbot.research.walkforward import WINDOWS

N_FOLDS = 4
STRESS_MULT = 1.5
MAX_TIME_IN_MARKET = 0.40
PLATEAU_MIN_SHARE = 0.60
MIN_EVENTS = {"15m": 300, "1h": 300, "4h": 150, "1d": 100}


class Stat(FrozenRecord):
    n: int
    mean_r: float | None = None
    t_stat: float | None = None


class Evaluation(FrozenRecord):
    spec_hash: str | None
    exit_name: str
    n_events: int
    mean_r: float | None
    t_stat: float | None                # clustered by session day
    time_in_market: float
    floors_passed: bool
    floor_failures: list[str]
    long: Stat
    short: Stat
    fold_t: list[float | None]          # per fold; None: the fold has too few events to read
    fitness: float | None               # min of fold_t (None when any fold is unreadable)
    drift_baseline_r: float | None
    excess_r: float | None
    stress_mean_r: float | None         # net mean R at STRESS_MULT x cost
    stress_passed: bool
    input_digest: str

    @property
    def explore_checks_passed(self) -> bool:
        """The checks this module owns (floors, positive fitness, positive excess over the drift baseline, cost stress);
        the plateau test and the search-level checks are read separately."""
        return bool(self.floors_passed and self.fitness is not None and self.fitness > 0
                    and self.excess_r is not None and self.excess_r > 0 and self.stress_passed)


def clustered_t(r: np.ndarray, day: np.ndarray) -> float | None:
    """t-statistic of the mean of `r` with standard errors clustered by `day` (session day): the cluster-robust se of a
    mean is sqrt(sum over days (sum of (r - mean))^2) / n. None below two events or two days, or a zero se."""
    n = len(r)
    if n < 2:
        return None
    m = float(r.mean())
    codes = pd.factorize(day)[0]
    if codes.max() < 1:
        return None
    resid_sums = np.bincount(codes, weights=r - m)
    se = float(np.sqrt((resid_sums ** 2).sum())) / n
    return m / se if se > 0 else None


def fold_edges(ts_utc: pd.DatetimeIndex, n_folds: int = N_FOLDS) -> np.ndarray:
    """Bar positions where each of `n_folds` equal-calendar-time folds starts, plus the end (length n_folds + 1)."""
    ns = np.asarray(epoch_ns(ts_utc), dtype="int64")
    t0, t1 = int(ns[0]), int(ns[-1])
    cuts = [t0 + (t1 - t0) * k // n_folds for k in range(n_folds)]
    pos = np.searchsorted(ns, cuts)
    return np.append(pos, len(ts_utc))


def fold_fitness(ev: Events, tensor: OutcomeTensor, timeframe: str, n_folds: int = N_FOLDS) -> list[float | None]:
    """Clustered net-R t-statistic per purged, embargoed time fold (module docstring)."""
    edges = fold_edges(tensor.ts_utc, n_folds)
    embargo = pd.Timedelta(days=WINDOWS[timeframe]["embargo_days"])
    r = ev.r()
    day = ev.ts_utc.normalize().to_numpy()
    out: list[float | None] = []
    for k in range(n_folds):
        lo, hi = edges[k], edges[k + 1]
        ok = (ev.t_entry >= lo) & (ev.t_entry < hi) & (ev.t_exit < hi)               # in the fold, and purged at its end
        if k > 0:
            ok &= ev.ts_utc >= tensor.ts_utc[lo] + embargo                            # embargo after the fold's start
        out.append(clustered_t(r[ok], day[ok]) if ok.sum() >= 2 else None)
    return out


def _stat(r: np.ndarray, day: np.ndarray) -> Stat:
    if len(r) == 0:
        return Stat(n=0)
    return Stat(n=len(r), mean_r=float(r.mean()), t_stat=clustered_t(r, day))


def evaluate(tensor: OutcomeTensor, mask: np.ndarray, side: np.ndarray | int, exit_i: int, *, timeframe: str,
             spec_hash: str | None = None, ledger: SearchLedger | None = None, null: bool = False, min_events: int | None = None,
             stress_mult: float = STRESS_MULT) -> Evaluation:
    """Evaluate one spec on the tensor. With a `ledger`, `spec_hash` must be recorded in it first (design 3.1), else
    `ledger.LedgerError`. `min_events` overrides the timeframe's floor."""
    if ledger is not None:
        if spec_hash is None:
            raise LedgerError("a ledger was given without the spec hash")
        ledger.require_logged(spec_hash, null=null)       # LedgerError: log the spec before evaluating (design 3.1)
    ev = tensor.select(mask, side, exit_i)
    n = len(ev)
    floor = MIN_EVENTS[timeframe] if min_events is None else min_events
    held = float((ev.t_exit - ev.t_entry + 1).sum()) / max(tensor.n_bars, 1)
    failures = []
    if n < floor:
        failures.append(f"events {n} < {floor}")
    if held > MAX_TIME_IN_MARKET:
        failures.append(f"time in market {held:.0%} > {MAX_TIME_IN_MARKET:.0%}")
    if n == 0:
        return Evaluation(spec_hash=spec_hash, exit_name=tensor.exits[exit_i].name, n_events=0, mean_r=None, t_stat=None,
                          time_in_market=0.0, floors_passed=False, floor_failures=failures, long=Stat(n=0),
                          short=Stat(n=0), fold_t=[None] * N_FOLDS, fitness=None, drift_baseline_r=None, excess_r=None,
                          stress_mean_r=None, stress_passed=False, input_digest=tensor.digest)
    r = ev.r()
    day = ev.ts_utc.normalize().to_numpy()
    folds = fold_fitness(ev, tensor, timeframe)
    tab = tensor.drift_table(exit_i)
    base = tab[ev.side_i, ev.ts_utc.hour.to_numpy()]
    base_r = float(np.nanmean(base)) if np.isfinite(base).any() else None
    stress = float(ev.r(stress_mult).mean())
    return Evaluation(
        spec_hash=spec_hash, exit_name=tensor.exits[exit_i].name, n_events=n, mean_r=float(r.mean()),
        t_stat=clustered_t(r, day), time_in_market=held, floors_passed=not failures, floor_failures=failures,
        long=_stat(r[ev.side_i == 0], day[ev.side_i == 0]), short=_stat(r[ev.side_i == 1], day[ev.side_i == 1]),
        fold_t=folds, fitness=None if any(f is None for f in folds) else float(min(f for f in folds if f is not None)),
        drift_baseline_r=base_r, excess_r=None if base_r is None else float(r.mean()) - base_r, stress_mean_r=stress,
        stress_passed=stress > 0, input_digest=tensor.digest)


def plateau_test(neighbour_fitness: Sequence[float | None], min_share: float = PLATEAU_MIN_SHARE) -> bool:
    """At least `min_share` of the grid neighbours have a positive net fitness (an unreadable neighbour counts as not
    positive); no neighbours: False (a spec with no grid neighbours has no plateau to show)."""
    if not neighbour_fitness:
        return False
    return sum(1 for f in neighbour_fitness if f is not None and f > 0) / len(neighbour_fitness) >= min_share

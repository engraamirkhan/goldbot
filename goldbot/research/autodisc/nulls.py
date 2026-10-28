"""G0, the random-side null (ADR 0058 section 3.2 step 7): the SAME search run on data with no edge. The timing of
every candidate is kept; the side of each bar is drawn at random (a deterministic hash of bar and seed, as
`arm_rules.hash_side` does for the controls) and the outcome is the random side's. The 95th percentile of the
best-of-search fitness over the replications is the bar a real search must beat ("reality check for an adaptive
search"). Null specs are counted in K_null, never in K_search.

With R replications the 95th percentile is taken with `method="higher"`, i.e. the maximum when R = 20: a real
search then clears it by chance with probability 1/(R+1) = 4.8% (exchangeability), inside the stated 5%. The size
test in tests/research/autodisc/test_nulls.py measures that on pure noise; the injected-edge test checks power.

Also here: the funnel (ledger-first generation, search, null, survivors per check), synthetic noise/edge builders
for the tests, and `search_specs`, the loop that evaluates specs against any tensor. Backends other staff own
(spec, ledger, outcomes, evaluate, primitives) come in through `contracts` interfaces."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from goldbot.research.autodisc.contracts import (
    EXIT_MENU,
    N_FOLDS,
    BoolArr,
    EvalResult,
    IntArr,
    Ledger,
    MaskProvider,
    OutcomeTensor,
    SpecLike,
    stub_evaluate,
)

Evaluate = Callable[[OutcomeTensor, BoolArr, IntArr, int, int], EvalResult]
NULL_REPS = 20
NULL_QUANTILE = 0.95
_M = np.uint64(0xFFFFFFFFFFFFFFFF)


def _splitmix(x: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        x = (x + np.uint64(0x9E3779B97F4A7C15)) & _M
        x = ((x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)) & _M
        x = ((x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)) & _M
        return x ^ (x >> np.uint64(31))


def hash_sides(n: int, seed: int) -> IntArr:
    """Deterministic random side (0 long / 1 short) per bar, a function of (bar, seed) only."""
    k = np.arange(n, dtype=np.uint64) ^ _splitmix(np.array([seed], dtype=np.uint64))
    return (_splitmix(k) & np.uint64(1)).astype(np.int64)


def null_tensor(t: OutcomeTensor, seed: int) -> OutcomeTensor:
    """Same bars, folds, days and costs; every spec's outcome at a bar is the randomly drawn side's outcome."""
    side = hash_sides(t.n_bars, seed)
    picked = np.take_along_axis(t.net_r, side[:, None, None], axis=1)        # (n, 1, E)
    return replace(t, net_r=np.broadcast_to(picked, t.net_r.shape).copy())


@dataclass(frozen=True)
class NullDistribution:
    best: tuple[float, ...]          # best-of-search fitness per replication (-inf: nothing passed the floors)
    k_null: int                      # null spec evaluations, counted apart from K_search

    def threshold(self, q: float = NULL_QUANTILE) -> float:
        return float(np.quantile(np.array(self.best), q, method="higher"))

    def percentile_of(self, value: float) -> float:
        """Share of null replications whose best-of-search is at or below `value` (the report's null percentile)."""
        return float(np.mean(np.array(self.best) <= value))


def run_null(t: OutcomeTensor, search_best: Callable[[OutcomeTensor], float], reps: int = NULL_REPS,
             seed: int = 0, n_specs: int = 0) -> NullDistribution:
    """`reps` replications of the same search on random-side data. `search_best(tensor)` returns the best fitness."""
    best = tuple(search_best(null_tensor(t, seed * 1_000_003 + r + 1)) for r in range(reps))
    return NullDistribution(best, reps * n_specs)


def is_survivor(fitness: float, null: NullDistribution) -> bool:
    return fitness > max(null.threshold(), 0.0)


# ---------------------------------------------------------------- search over specs

def exit_index(spec: SpecLike) -> int:
    return EXIT_MENU.index(spec.exit_template)


class MaskCache:
    """Entry masks per spec, cached by (family, timeframe) and (filter, timeframe)."""

    def __init__(self, provider: MaskProvider) -> None:
        self.p = provider
        self._fam: dict[tuple[str, str], tuple[BoolArr, IntArr]] = {}
        self._flt: dict[tuple[str, str], BoolArr] = {}

    def entries(self, spec: SpecLike) -> tuple[BoolArr, IntArr]:
        key = (spec.entry.name, spec.timeframe)
        if key not in self._fam:
            self._fam[key] = self.p.family_entries(*key)
        mask, side = self._fam[key]
        if spec.filter is None:
            return mask, side
        fk = (spec.filter.canonical(), spec.timeframe)
        if fk not in self._flt:
            self._flt[fk] = self.p.leaf_mask(spec.filter, spec.timeframe)
        return mask & self._flt[fk], side


def search_specs(t: OutcomeTensor, specs: Sequence[SpecLike], cache: MaskCache,
                 evaluate: Evaluate | None = None, min_events: int = 300) -> list[EvalResult]:
    ev = evaluate or stub_evaluate
    out = []
    for s in specs:
        mask, side = cache.entries(s)
        out.append(ev(t, mask, side, exit_index(s), min_events))
    return out


def best_fitness(results: Iterable[EvalResult]) -> float:
    return max((r.fitness for r in results if r.ok), default=float("-inf"))


# ---------------------------------------------------------------- the funnel (generate -> log -> evaluate -> null)

@dataclass
class FunnelResult:
    k_search: int
    k_null: int
    evaluated: int
    passed_floors: int
    beat_null: int
    passed_stress: int
    null_threshold: float
    real_best_null_percentile: float
    survivors: list[str] = field(default_factory=list)       # spec hashes (explore survivors, not edges)
    real_best: float = float("-inf")


def run_funnel(t: OutcomeTensor, specs: Sequence[SpecLike], provider: MaskProvider, ledger: Ledger,
               generator: str, version: str, evaluate: Evaluate | None = None, reps: int = NULL_REPS,
               seed: int = 0, min_events: int = 300) -> FunnelResult:
    """Ledger row first for every spec (a duplicate hash is evaluated once and adds nothing to K), then the real
    search, then the same search on `reps` null replications; survivors beat the null's 95th percentile of
    best-of-search, have positive net mean R at 1.5x cost, and pass the event floor."""
    fresh: list[SpecLike] = []
    for s in specs:
        if ledger.append(s.spec_hash(), s.canonical(), generator, version, (s.entry.name,)):
            fresh.append(s)
    cache = MaskCache(provider)
    real = search_specs(t, fresh, cache, evaluate, min_events)
    null = run_null(t, lambda nt: best_fitness(search_specs(nt, fresh, cache, evaluate, min_events)), reps, seed,
                    len(fresh))
    thr = max(null.threshold(), 0.0)
    beat = [(s, r) for s, r in zip(fresh, real, strict=True) if r.ok and r.fitness > thr]
    kept = [s.spec_hash() for s, r in beat if r.stress_mean_r > 0]
    rb = best_fitness(real)
    return FunnelResult(ledger.k_search(), null.k_null, len(fresh), sum(r.ok for r in real), len(beat), len(kept),
                        thr, null.percentile_of(rb), kept, rb)


# ---------------------------------------------------------------- synthetic data for the size / power tests

def make_noise_tensor(n_bars: int = 4000, n_days: int = 400, seed: int = 0, cost_r: float = 0.08,
                      timeframe: str = "15m") -> OutcomeTensor:
    """Pure noise: no side, family or filter has any edge. Exits are correlated, a small day shock makes the day
    clustering matter, and short = mirror of long (like a symmetric barrier pair) less costs."""
    rng = np.random.default_rng(seed)
    day = np.sort(rng.integers(0, n_days, n_bars)).astype(np.int64)
    shock = rng.normal(0, 0.3, n_days)[day]
    base = rng.normal(0, 1, n_bars) + shock
    z = base[:, None] + rng.normal(0, 0.5, (n_bars, len(EXIT_MENU)))
    net = np.stack([z - cost_r, -z - cost_r], axis=1)
    fold = np.minimum(day * N_FOLDS // n_days, N_FOLDS - 1).astype(np.int64)
    return OutcomeTensor(timeframe, net, np.full(n_bars, cost_r), day, fold)


def inject_edge(t: OutcomeTensor, mask: BoolArr, side: IntArr, exit_idx: int | None = None,
                edge_r: float = 0.15) -> OutcomeTensor:
    """Add +edge_r net R to the chosen side's outcome on the masked bars (all exits unless one is named)."""
    net = t.net_r.copy()
    idx = np.flatnonzero(mask)
    if exit_idx is None:
        net[idx, side[idx], :] += edge_r
    else:
        net[idx, side[idx], exit_idx] += edge_r
    return replace(t, net_r=net)

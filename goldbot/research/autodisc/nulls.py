"""G0, the random-side null (ADR 0058 section 3.2 step 7): the SAME search run on data with no edge. The timing of
every candidate is kept (its entry mask, its exit, its clustering); only the side of each bar is replaced by a
deterministic random draw (a hash of bar and seed, as `arm_rules.hash_side` does for the controls) and the outcome is
that side's, read from the same outcome tensor. The 95th percentile of the best-of-search fitness over the replications
is the bar a real search must beat ("reality check for an adaptive search"). Null specs are logged with `null=True`
(K_null, apart from K_search).

With R replications the 95th percentile is taken with `method="higher"`, i.e. the maximum when R = 20: a real search
then clears it by chance with probability 1/(R+1) = 4.8% (exchangeability), inside the stated 5%. The size test in
tests/research/autodisc/test_nulls.py measures that on pure noise; the injected-edge test checks power.

Also here: `run_funnel` (ledger-first generation, search, null, survivors per check) and the synthetic noise tensor,
noise provider and edge injection the tests and `scripts/autodisc.py --synthetic` use. The evaluator, tensor, spec and
ledger are the real modules; only the mask provider (`contracts.MaskProvider`, `autodisc.primitives`) is a stand-in.

Not in phase 1 (design section 8): White RC / Hansen SPA, ONC K_eff, batch PBO and DSR are phase 2 (`stats.py`)."""
from __future__ import annotations

import json
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from goldbot.research.autodisc.contracts import BoolArr, MaskProvider, SideArr
from goldbot.research.autodisc.evaluate import Evaluation, evaluate
from goldbot.research.autodisc.gen_combine import MAX_BARS
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.outcomes import OutcomeTensor, barrier_exit, default_menu
from goldbot.research.autodisc.spec import Predicate, StrategySpec, canonical_json, spec_hash

NULL_REPS = 20
NULL_QUANTILE = 0.95
_M = np.uint64(0xFFFFFFFFFFFFFFFF)
NEG_INF = float("-inf")


def _splitmix(x: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        x = (x + np.uint64(0x9E3779B97F4A7C15)) & _M
        x = ((x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)) & _M
        x = ((x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)) & _M
        return x ^ (x >> np.uint64(31))


def hash_sides(n: int, seed: int) -> SideArr:
    """Deterministic random side (+1 long / -1 short) per bar, a function of (bar, seed) only."""
    k = np.arange(n, dtype=np.uint64) ^ _splitmix(np.array([seed], dtype=np.uint64))
    return np.where((_splitmix(k) & np.uint64(1)) == 1, -1, 1).astype(np.int8)


@dataclass(frozen=True)
class NullDistribution:
    best: tuple[float, ...]          # best-of-search fitness per replication (-inf: nothing passed the floors)
    k_null: int                      # distinct null specs (ledger.k_null)

    def threshold(self, q: float = NULL_QUANTILE) -> float:
        return float(np.quantile(np.array(self.best), q, method="higher"))

    def percentile_of(self, value: float) -> float:
        """Share of null replications whose best-of-search is at or below `value` (the report's null percentile)."""
        return float(np.mean(np.array(self.best) <= value))


def run_null(search_best: Callable[[SideArr], float], n_bars: int, reps: int = NULL_REPS, seed: int = 0
             ) -> tuple[float, ...]:
    """`reps` replications of the same search with random sides; `search_best(side)` returns best-of-search fitness."""
    return tuple(search_best(hash_sides(n_bars, seed * 1_000_003 + r + 1)) for r in range(reps))


# ---------------------------------------------------------------- search over specs

def exit_index(tensor: OutcomeTensor, spec: StrategySpec) -> int:
    """The tensor column of the spec's exit (a structural stop is not in the tensor: ValueError)."""
    return tensor.exit_index(barrier_exit(spec.exit, MAX_BARS[spec.timeframe]).name)


class MaskCache:
    """Entry masks per spec, cached by (family, timeframe) and (filter, timeframe). G1 specs name their family in
    `spec.family`; the provider resolves it to the family's entry bars and side."""

    def __init__(self, provider: MaskProvider) -> None:
        self.p = provider
        self._fam: dict[tuple[str, str], tuple[BoolArr, SideArr]] = {}
        self._flt: dict[tuple[str, str], BoolArr] = {}

    def entries(self, spec: StrategySpec) -> tuple[BoolArr, SideArr]:
        if spec.family is None:
            raise ValueError("only family-based (G1) specs are resolved by this cache")
        key = (spec.family, spec.timeframe)
        if key not in self._fam:
            self._fam[key] = self.p.family_entries(*key)
        mask, side = self._fam[key]
        if spec.filter is None:
            return mask, side
        fk = (canonical_json(spec.model_copy(update={"entry": spec.filter, "filter": None, "family": None,
                                                     "side_rule": "long", "side_primitive": None})), spec.timeframe)
        if fk not in self._flt:
            self._flt[fk] = self.p.predicate_mask(spec.filter, spec.timeframe)
        return mask & self._flt[fk], side


def search_specs(t: OutcomeTensor, specs: Sequence[StrategySpec], cache: MaskCache, *,
                 side_override: SideArr | None = None, ledger: SearchLedger | None = None, null: bool = False,
                 min_events: int | None = None) -> list[Evaluation]:
    """Evaluate every spec with the real evaluator. `side_override` is the null's random side. With a ledger, the
    evaluator itself refuses a spec that is not logged first (`require_logged`)."""
    out = []
    for s in specs:
        mask, side = cache.entries(s)
        out.append(evaluate(t, mask, side if side_override is None else side_override, exit_index(t, s),
                            timeframe=s.timeframe, spec_hash=spec_hash(s), ledger=ledger, null=null,
                            min_events=min_events))
    return out


def fitness_of(e: Evaluation) -> float:
    return e.fitness if (e.floors_passed and e.fitness is not None) else NEG_INF


def best_fitness(results: Iterable[Evaluation]) -> float:
    return max((fitness_of(e) for e in results), default=NEG_INF)


# ---------------------------------------------------------------- the funnel (generate -> log -> evaluate -> null)

@dataclass
class FunnelResult:
    k_search: int
    k_null: int
    evaluated: int
    passed_floors: int
    beat_null: int
    passed_explore_checks: int         # beat the null AND positive excess over drift AND cost stress (evaluate's own)
    null_threshold: float
    real_best_null_percentile: float
    survivors: list[str] = field(default_factory=list)       # spec hashes (explore survivors, not edges)
    real_best: float = NEG_INF


def _num(x: float | None) -> float | None:
    return x if x is not None and np.isfinite(x) else None


def run_funnel(t: OutcomeTensor, specs: Sequence[StrategySpec], provider: MaskProvider, ledger: SearchLedger,
               generator: str, version: str, reps: int = NULL_REPS, seed: int = 0, min_events: int | None = None
               ) -> FunnelResult:
    """Log every spec FIRST (`log_spec`; a hash already logged is not evaluated again and adds nothing to K), evaluate
    with the ledger so an unlogged hash is refused, then run the same search on `reps` random-side replications (specs
    logged null=True: K_null). Survivors beat the null's 95th percentile of best-of-search and pass the evaluator's
    own explore checks (floors, positive fitness and excess over the drift baseline, 1.5x cost stress).
    Plateau (needs numeric grid neighbours) and the search-level RC/SPA/PBO/DSR are not applied here (phase 2)."""
    fresh: list[StrategySpec] = []
    for s in specs:
        if not ledger.is_logged(spec_hash(s)):
            ledger.log_spec(s, generator, version, parents=(s.family,) if s.family else ())
            fresh.append(s)
    cache = MaskCache(provider)
    real = search_specs(t, fresh, cache, ledger=ledger, min_events=min_events)
    for s, e in zip(fresh, real, strict=True):
        ledger.record_result(spec_hash(s), {"n_events": e.n_events, "mean_r": _num(e.mean_r),
                                            "fitness": _num(e.fitness), "excess_r": _num(e.excess_r),
                                            "stress_mean_r": _num(e.stress_mean_r), "floors": e.floors_passed})
    for s in fresh:
        ledger.log_spec(s, generator, version, parents=(s.family,) if s.family else (), null=True)
    per_rep: list[list[Evaluation]] = []
    for r in range(reps):
        side = hash_sides(t.n_bars, seed * 1_000_003 + r + 1)
        per_rep.append(search_specs(t, fresh, cache, side_override=side, ledger=ledger, null=True,
                                    min_events=min_events))
    for j, s in enumerate(fresh):
        ledger.record_result(spec_hash(s), {"null_fitness": [_num(fitness_of(per_rep[i][j])) for i in range(reps)]},
                             null=True)
    null = NullDistribution(tuple(best_fitness(rep) for rep in per_rep), ledger.k_null())
    thr = max(null.threshold(), 0.0)
    beat = [(s, e) for s, e in zip(fresh, real, strict=True) if fitness_of(e) > thr]
    kept = [spec_hash(s) for s, e in beat if e.explore_checks_passed]
    rb = best_fitness(real)
    return FunnelResult(ledger.k_search(), null.k_null, len(fresh), sum(e.floors_passed for e in real), len(beat),
                        len(kept), thr, null.percentile_of(rb), kept, rb)


# ---------------------------------------------------------------- synthetic data for the size / power tests

def make_noise_tensor(n_bars: int = 4000, seed: int = 0, cost_r: float = 0.08, timeframe: str = "15m",
                      max_hold: int = 5) -> OutcomeTensor:
    """A pure-noise outcome tensor in the real layout: no side, family or filter has any edge. Exits share a common
    shock, short = mirror of long less costs (as a symmetric barrier pair), entry the next bar, exit 1..max_hold
    bars later. Bars are spaced 15 min, or thinner if that would run past the explore window."""
    rng = np.random.default_rng(seed)
    menu = default_menu(MAX_BARS[timeframe])
    step = min(pd.Timedelta(minutes=15), pd.Timedelta(days=2900) / n_bars)
    ts = pd.DatetimeIndex(pd.Timestamp("2010-01-04", tz="UTC") + np.arange(n_bars) * step)
    z = rng.normal(0, 1, (n_bars, 1)) + rng.normal(0, 0.5, (n_bars, len(menu)))
    risk = np.full((n_bars, 2, len(menu)), 0.002)
    ret = np.stack([z - cost_r, -z - cost_r], axis=1) * risk
    cost = np.full_like(risk, cost_r * 0.002)
    t_entry = np.minimum(np.arange(n_bars) + 1, n_bars - 1)
    t_exit = np.minimum(t_entry + rng.integers(1, max_hold + 1, n_bars), n_bars - 1)
    te = np.broadcast_to(t_entry[:, None, None], risk.shape).astype(np.int32).copy()
    tx = np.broadcast_to(t_exit[:, None, None], risk.shape).astype(np.int32).copy()
    ret[-1] = np.nan
    te[-1] = tx[-1] = -1
    return OutcomeTensor(ts_utc=ts, exits=menu, ret=ret, risk=risk, cost=cost, t_entry=te, t_exit=tx,
                         digest="synthetic")


def inject_edge(t: OutcomeTensor, mask: BoolArr, side: SideArr, edge_r: float = 0.15) -> OutcomeTensor:
    """Add +edge_r net R to the chosen side's outcome on the masked bars, for every exit."""
    ret = t.ret.copy()
    idx = np.flatnonzero(mask & (side != 0))
    k = (side[idx] < 0).astype(int)
    ret[idx, k, :] += edge_r * t.risk[idx, k, :]
    return OutcomeTensor(ts_utc=t.ts_utc, exits=t.exits, ret=ret, risk=t.risk, cost=t.cost, t_entry=t.t_entry,
                         t_exit=t.t_exit, digest=t.digest)


class SyntheticProvider:
    """STAND-IN for `autodisc.primitives` on synthetic data: every family's entries and every filter's mask are
    seeded random draws, independent of the outcomes (so any survivor on a noise tensor is a false discovery)."""

    def __init__(self, n_bars: int, seed: int = 0, density: float = 0.1) -> None:
        self.n, self.seed, self.density = n_bars, seed, density

    def _rng(self, *key: str) -> np.random.Generator:
        return np.random.default_rng([self.seed, zlib.crc32("|".join(key).encode())])

    def family_entries(self, family: str, timeframe: str) -> tuple[BoolArr, SideArr]:
        rng = self._rng("fam", family, timeframe)
        side = np.where(rng.integers(0, 2, self.n) == 1, 1, -1).astype(np.int8)
        return rng.random(self.n) < self.density, side

    def predicate_mask(self, pred: Predicate, timeframe: str) -> BoolArr:
        key = json.dumps(pred.model_dump(mode="json"), sort_keys=True)
        return self._rng("flt", key, timeframe).random(self.n) < 0.5

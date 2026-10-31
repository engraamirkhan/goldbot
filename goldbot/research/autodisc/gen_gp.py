"""G2 Grammar / GP (ADR 0058 phase 2, design section 2): "strongly-typed genetic programming over the language. NSGA-II
objectives: min-fold net t across purged CV folds inside 2010-2017; tree complexity; turnover. Structural primitives
(sessions, levels, time, events) are weighted 2:1 over pure oscillators."

The genome is a `spec.StrategySpec` (typed by construction: predicates are and/or trees of `Leaf`s, depth <= 3 and
<= 4 leaves, checked by the spec itself). The leaf catalogue is exactly what `primitives.PrimitiveProvider` computes
(family rules, time, regime terciles): no leaf is offered that cannot be evaluated, so K is never inflated by one.
Structure and time leaves draw at twice the weight of regime leaves.

Honest counting (design 3.1): every candidate the search evaluates is logged in the `SearchLedger` BEFORE it is
evaluated (the evaluator refuses an unlogged hash), its result recorded after, never only the survivors. A child
equal to a spec already evaluated in this run is not evaluated again; one already in the ledger adds nothing to K.
The null (`gp_null`) reruns the WHOLE search, selection included, on random sides with the timing kept (G0 extended to
the adaptive search: design 3.2 step 7); its specs are logged null=True and count in K_null only.

Bounded: at most population x (generations + 1) evaluations a run. Deterministic under `GPConfig.seed`.
Selection is NSGA-II (non-dominated fronts, crowding distance, (mu + lambda) survival) on (fitness, -leaves,
-turnover); fitness (`gp_fitness`) is the evaluator's min-fold clustered t of EXCESS net R over the drift baseline
(-inf below the floors): the G0 null's random sides carry no drift, so a fitness on raw net R would count gold's long
drift as skill against it (quant review F4, 2026-10-30)."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, ValidationError, model_validator

from goldbot.base import FrozenRecord
from goldbot.research.autodisc.contracts import BoolArr, MaskProvider, SideArr
from goldbot.research.autodisc.evaluate import Evaluation, evaluate
from goldbot.research.autodisc.gen_combine import MAX_BARS, exit_menu
from goldbot.research.autodisc.grammar_v2 import (
    R4H,
    V2_LADDERS,
    V2_MACRO,
    V2_REGIME,
    V2_TARGET_LADDER,
    V2_TARGETS,
    V2_TIME,
)
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.nulls import NEG_INF, NULL_REPS, NullDistribution, exit_index, hash_sides
from goldbot.research.autodisc.outcomes import OutcomeTensor
from goldbot.research.autodisc.spec import (
    MAX_DEPTH,
    ExitTemplate,
    Group,
    Leaf,
    Predicate,
    SessionWindow,
    StrategySpec,
    Timeframe,
    canonicalise_predicate,
    leaves,
    spec_hash,
)

GENERATOR = "G2"
VERSION = "1"
VERSION_OF = {"v1": "1", "v2": "2"}      # the ledger's generator version per grammar (ADR 0058 amendment 2026-11-02)
Grammar = Literal["v1", "v2"]
TIME_LEAVES = ("asia", "london", "ny", "london_ny_overlap", "month_turn", "mon", "tue", "wed", "thu", "fri")
# ordinal ladders: a neighbour moves one rung (design 3.2 step 6, "each parameter moved +-1 step")
LADDERS: tuple[tuple[str, ...], ...] = (("rvol_q_lo", "rvol_q_mid", "rvol_q_hi"),
                                        ("trend_strength_lo", "trend_strength_mid", "trend_strength_hi"),
                                        ("hurst_mean_revert", "hurst_mid", "hurst_trend"))
REGIME_LEAVES = tuple(n for ladder in LADDERS for n in ladder)
STOP_LADDER = ("atr_1.0", "atr_1.5")
TARGET_LADDER = ("1R", "2R")
SESSIONS: tuple[SessionWindow, ...] = ("all", "asia", "london", "ny", "london_ny")
STRUCTURAL_WEIGHT = 2.0
REGIME_WEIGHT = 1.0
_TRIES = 50
_NEG = -1e9                    # objective value of a candidate below the floors (fitness -inf)


class GPConfig(FrozenRecord):
    timeframe: Timeframe = "15m"
    population: int = Field(24, ge=4, le=1000)
    generations: int = Field(6, ge=1, le=200)
    tournament: int = Field(2, ge=2, le=8)
    p_crossover: float = Field(0.7, ge=0, le=1)
    p_mutation: float = Field(0.4, ge=0, le=1)
    p_filter: float = Field(0.5, ge=0, le=1)        # a random spec carries a filter with this probability
    seed: int = 0
    min_events: int | None = Field(None, ge=1)      # None: the evaluator's per-timeframe floor
    families: tuple[str, ...] = ()                  # family rules offered as structure leaves and side primitives
    grammar: Grammar = "v1"                         # v2: grammar_v2's gate leaves, the r4h side and the exit list of 5

    @model_validator(mode="after")
    def _tournament_fits(self) -> "GPConfig":
        if self.tournament > self.population:
            raise ValueError(f"tournament {self.tournament} > population {self.population}")
        return self


class GPCandidate(FrozenRecord):
    spec: StrategySpec
    spec_hash: str
    parents: tuple[str, ...]
    generation: int
    evaluation: Evaluation


class GPRun(FrozenRecord):
    evaluated: tuple[GPCandidate, ...]               # every evaluated candidate, in evaluation order
    best: float                                      # best fitness of the run (-inf: nothing passed the floors)
    best_by_generation: tuple[float, ...]


# ---------------------------------------------------------------- grammar

def catalogue(families: Sequence[str], grammar: Grammar = "v1") -> tuple[tuple[Leaf, float], ...]:
    """(leaf, sampling weight): the family rules and time leaves at `STRUCTURAL_WEIGHT`, regime terciles at
    `REGIME_WEIGHT`. Exactly the vocabulary `primitives.PrimitiveProvider.supports` accepts (v2: plus the gate leaves
    of `grammar_v2`, which `PrimitiveProviderV2.supports` accepts; the event day is a time leaf, the HAR band, NR4/NR7
    and the macro gates draw at the regime weight; r4h is never an entry leaf)."""
    fam = tuple((Leaf(kind="structure", name="family_rule", params={"rule": f}), STRUCTURAL_WEIGHT)
                for f in families if f != R4H)
    time = tuple((Leaf(kind="time", name=n), STRUCTURAL_WEIGHT) for n in TIME_LEAVES)
    regime = tuple((Leaf(kind="regime", name=n), REGIME_WEIGHT) for n in REGIME_LEAVES)
    if grammar == "v1":
        return fam + time + regime
    time2 = tuple((Leaf(kind="time", name=n), STRUCTURAL_WEIGHT) for n in V2_TIME)
    regime2 = tuple((Leaf(kind="regime", name=n), REGIME_WEIGHT) for n in V2_REGIME)
    macro = tuple((Leaf(kind="macro", name=n), REGIME_WEIGHT) for n in V2_MACRO)
    return fam + time + time2 + regime + regime2 + macro


def side_families(cfg: GPConfig) -> tuple[str, ...]:
    """The side primitives a `with`/`against` spec may follow: the families (v2: and the r4h sign)."""
    return tuple(cfg.families) + ((R4H,) if cfg.grammar == "v2" else ())


def v2_exit_menu(timeframe: str) -> tuple[ExitTemplate, ...]:
    """Grammar v2's closed list of 5 (`outcomes.v2_menu`): stop 1.0 ATR x {time, 1R, 2R, 3R, be_trail}."""
    return tuple(ExitTemplate(stop="atr_1.0", target=t, time_bars=MAX_BARS[timeframe] if t == "time" else None)  # type: ignore[arg-type]
                 for t in V2_TARGETS)


def menu_for(cfg: GPConfig) -> tuple[ExitTemplate, ...]:
    return v2_exit_menu(cfg.timeframe) if cfg.grammar == "v2" else exit_menu(cfg.timeframe)


def _leaf(rng: np.random.Generator, cat: Sequence[tuple[Leaf, float]]) -> Leaf:
    w = np.array([x[1] for x in cat], dtype=float)
    return cat[int(rng.choice(len(cat), p=w / w.sum()))][0]


def _grow(rng: np.random.Generator, depth: int, cat: Sequence[tuple[Leaf, float]]) -> Predicate:
    """A random tree of at most `depth` levels (binary groups: <= 2^(depth-1) leaves, so depth 3 keeps <= 4)."""
    if depth <= 1 or rng.random() < 0.5:
        return _leaf(rng, cat)
    return Group(op="and" if rng.random() < 0.5 else "or", children=(_grow(rng, depth - 1, cat), _grow(rng, depth - 1, cat)))


def _family_leaf(rng: np.random.Generator, families: Sequence[str]) -> Leaf:
    return Leaf(kind="structure", name="family_rule", params={"rule": families[int(rng.integers(len(families)))]})


def _side(rng: np.random.Generator, families: Sequence[str]) -> tuple[str, Leaf | None]:
    rules = ("long", "short", "with", "against") if families else ("long", "short")
    rule = rules[int(rng.integers(len(rules)))]
    return rule, (_family_leaf(rng, families) if rule in ("with", "against") else None)


def _spec(base: StrategySpec | None, **kw: object) -> StrategySpec | None:
    """A validated spec (None when the change breaks the grammar bounds)."""
    try:
        if base is None:
            return StrategySpec(**kw)  # type: ignore[arg-type]
        return base.model_validate({**dict(base), **kw})
    except (ValidationError, ValueError):
        return None


def random_spec(rng: np.random.Generator, cfg: GPConfig, cat: Sequence[tuple[Leaf, float]]) -> StrategySpec:
    menu = menu_for(cfg)
    for _ in range(_TRIES):
        rule, prim = _side(rng, side_families(cfg))
        s = _spec(None, timeframe=cfg.timeframe, session_window=SESSIONS[int(rng.integers(len(SESSIONS)))],
                  side_rule=rule, side_primitive=prim, entry=_grow(rng, MAX_DEPTH, cat),
                  filter=_grow(rng, MAX_DEPTH - 1, cat) if rng.random() < cfg.p_filter else None,
                  exit=menu[int(rng.integers(len(menu)))])
        if s is not None:
            return s
    raise RuntimeError("could not draw a valid spec")      # unreachable: a single leaf entry is always valid


def _paths(p: Predicate, path: tuple[int, ...] = ()) -> Iterator[tuple[tuple[int, ...], Predicate]]:
    yield path, p
    if isinstance(p, Group):
        for i, c in enumerate(p.children):
            yield from _paths(c, path + (i,))


def _put(p: Predicate, path: tuple[int, ...], new: Predicate) -> Predicate:
    if not path:
        return new
    assert isinstance(p, Group)
    kids = list(p.children)
    kids[path[0]] = _put(kids[path[0]], path[1:], new)
    return Group(op=p.op, children=tuple(kids))


def _pick(rng: np.random.Generator, p: Predicate) -> tuple[tuple[int, ...], Predicate]:
    nodes = list(_paths(p))
    return nodes[int(rng.integers(len(nodes)))]


def mutate(s: StrategySpec, rng: np.random.Generator, cfg: GPConfig, cat: Sequence[tuple[Leaf, float]]
           ) -> StrategySpec:
    """One random change inside the grammar: a subtree of the entry or filter regrown, the filter toggled, the session
    window, the side rule or the exit redrawn. A change that breaks the bounds is redrawn; after `_TRIES` the parent."""
    menu = menu_for(cfg)
    for _ in range(_TRIES):
        op = int(rng.integers(6))
        out: StrategySpec | None
        if op <= 1:
            path, _ = _pick(rng, s.entry)
            out = _spec(s, entry=_put(s.entry, path, _grow(rng, MAX_DEPTH - len(path), cat)))
        elif op == 2:
            if s.filter is None:
                out = _spec(s, filter=_grow(rng, MAX_DEPTH - 1, cat))
            elif rng.random() < 0.5:
                out = _spec(s, filter=None)
            else:
                path, _ = _pick(rng, s.filter)
                out = _spec(s, filter=_put(s.filter, path, _grow(rng, MAX_DEPTH - len(path), cat)))
        elif op == 3:
            out = _spec(s, session_window=SESSIONS[int(rng.integers(len(SESSIONS)))])
        elif op == 4:
            rule, prim = _side(rng, side_families(cfg))
            out = _spec(s, side_rule=rule, side_primitive=prim)
        else:
            out = _spec(s, exit=menu[int(rng.integers(len(menu)))])
        if out is not None and spec_hash(out) != spec_hash(s):
            return out
    return s


def crossover(a: StrategySpec, b: StrategySpec, rng: np.random.Generator) -> StrategySpec:
    """Subtree crossover: a random subtree of b's entry (or filter) replaces a random node of a's entry; a child over
    the bounds is redrawn, after `_TRIES` the first parent."""
    for _ in range(_TRIES):
        donor = b.filter if (b.filter is not None and rng.random() < 0.3) else b.entry
        _, sub = _pick(rng, donor)
        path, _ = _pick(rng, a.entry)
        out = _spec(a, entry=_put(a.entry, path, sub))
        if out is not None:
            return out
    return a


def _step(name: str, ladder: Sequence[str]) -> list[str]:
    i = ladder.index(name)
    return [ladder[j] for j in (i - 1, i + 1) if 0 <= j < len(ladder)]


def neighbours(s: StrategySpec, grammar: Grammar = "v1") -> list[StrategySpec]:
    """Grid neighbours: one ordinal parameter moved one rung (a regime tercile of any leaf of the entry or filter, the
    stop 1.0 <-> 1.5 ATR, the target 1R <-> 2R); distinct, never the spec itself, each inside the grammar. v2: the
    v2 ladders too (HAR band, NR4 <-> NR7, GVZ tercile), no stop ladder (every v2 exit stops at 1.0 ATR) and the target
    ladder 1R <-> 2R <-> 3R."""
    ladders = LADDERS + (V2_LADDERS if grammar == "v2" else ())
    stops = STOP_LADDER if grammar == "v1" else ()
    targets = TARGET_LADDER if grammar == "v1" else V2_TARGET_LADDER
    out: dict[str, StrategySpec] = {}
    own = spec_hash(s)

    def add(x: StrategySpec | None) -> None:
        if x is not None and (h := spec_hash(x)) != own:
            out.setdefault(h, x)

    for field_name in ("entry", "filter"):
        p: Predicate | None = getattr(s, field_name)
        if p is None:
            continue
        for path, node in _paths(p):
            if isinstance(node, Leaf) and node.kind in ("regime", "macro") and any(node.name in lad for lad in ladders):
                ladder = next(lad for lad in ladders if node.name in lad)
                for nb in _step(node.name, ladder):
                    add(_spec(s, **{field_name: _put(p, path, Leaf(kind=node.kind, name=nb))}))
    if s.exit.stop in stops:
        for st in _step(s.exit.stop, stops):
            add(_spec(s, exit=s.exit.model_copy(update={"stop": st})))
    if s.exit.target in targets:
        for tg in _step(s.exit.target, targets):
            add(_spec(s, exit=s.exit.model_copy(update={"target": tg})))
    return list(out.values())


# ---------------------------------------------------------------- masks

class Resolver:
    """Entry mask and side of any spec over a `MaskProvider`: entry AND filter AND session window, on the bars where
    the side rule gives a direction (`with` / `against` a family: its own side, 0 where it has no signal)."""

    def __init__(self, provider: MaskProvider) -> None:
        self.p = provider
        self._leaf: dict[tuple[str, str], BoolArr] = {}

    def mask(self, pred: Predicate, timeframe: str) -> BoolArr:
        if isinstance(pred, Group):
            ms = [self.mask(c, timeframe) for c in pred.children]
            return np.logical_and.reduce(ms) if pred.op == "and" else np.logical_or.reduce(ms)
        key = (pred.model_dump_json(), timeframe)
        if key not in self._leaf:
            if pred.kind == "structure" and pred.name == "family_rule":
                self._leaf[key] = np.asarray(self.p.family_entries(str(pred.params["rule"]), timeframe)[0], dtype=bool)
            else:
                self._leaf[key] = np.asarray(self.p.predicate_mask(pred, timeframe), dtype=bool)
        return self._leaf[key]

    def session(self, window: SessionWindow, timeframe: str, n: int) -> BoolArr:
        if window == "all":
            return np.ones(n, dtype=bool)
        if window == "london_ny":
            return self.mask(Leaf(kind="time", name="london"), timeframe) | self.mask(Leaf(kind="time", name="ny"), timeframe)
        return self.mask(Leaf(kind="time", name=window), timeframe)

    def entries(self, s: StrategySpec) -> tuple[BoolArr, SideArr]:
        m = self.mask(s.entry, s.timeframe)
        if s.filter is not None:
            m = m & self.mask(s.filter, s.timeframe)
        m = m & self.session(s.session_window, s.timeframe, len(m))
        if s.side_rule in ("long", "short"):
            side = np.full(len(m), 1 if s.side_rule == "long" else -1, dtype=np.int8)
        else:                       # a family's direction exists only on its own signal bars
            assert s.side_primitive is not None
            fam_mask, fam_side = self.p.family_entries(str(s.side_primitive.params["rule"]), s.timeframe)
            m = m & np.asarray(fam_mask, dtype=bool)
            fam = np.asarray(fam_side, dtype=np.int8)
            side = fam if s.side_rule == "with" else (-fam).astype(np.int8)
        return m, side


def null_view(mask: BoolArr, side: SideArr, random_side: SideArr) -> tuple[BoolArr, SideArr]:
    """G0: the spec's timing (bars where it would trade, its side rule's gaps included) with the random side."""
    return mask & (side != 0), random_side


# ---------------------------------------------------------------- NSGA-II

def gp_fitness(e: Evaluation) -> float:
    """G2's fitness: the min-fold excess-R t (module docstring); -inf below the floors or with an unreadable fold."""
    return e.excess_fitness if (e.floors_passed and e.excess_fitness is not None) else NEG_INF


def nondominated_fronts(obj: NDArray[np.float64]) -> list[list[int]]:
    """Fronts of rows of `obj` (every column maximised), best first (Deb et al. 2002, fast non-dominated sort)."""
    m = len(obj)
    ge = np.asarray((obj[:, None, :] >= obj[None, :, :]).all(axis=2), dtype=bool)
    gt = np.asarray((obj[:, None, :] > obj[None, :, :]).any(axis=2), dtype=bool)
    dom = ge & gt                                                  # dom[i, j]: i dominates j
    count = dom.sum(axis=0)
    fronts: list[list[int]] = []
    current = [i for i in range(m) if count[i] == 0]
    while current:
        fronts.append(current)
        nxt = []
        for i in current:
            for j in np.flatnonzero(dom[i]):
                count[j] -= 1
                if count[j] == 0:
                    nxt.append(int(j))
        current = sorted(nxt)
    return fronts


def crowding(obj: NDArray[np.float64], front: Sequence[int]) -> dict[int, float]:
    d = {i: 0.0 for i in front}
    if len(front) <= 2:
        return {i: float("inf") for i in front}
    f = np.asarray(front)
    for k in range(obj.shape[1]):
        v = obj[f, k]
        order = f[np.argsort(v, kind="stable")]
        lo, hi = obj[order[0], k], obj[order[-1], k]
        d[int(order[0])] = d[int(order[-1])] = float("inf")
        if hi > lo:
            for a, i, b in zip(order[:-2], order[1:-1], order[2:], strict=True):
                d[int(i)] += (obj[b, k] - obj[a, k]) / (hi - lo)
    return d


def _objectives(cands: Sequence[GPCandidate], n_bars: int) -> NDArray[np.float64]:
    rows = []
    for c in cands:
        f = gp_fitness(c.evaluation)
        n_leaves = len(leaves(canonicalise_predicate(c.spec.entry))) + (
            len(leaves(canonicalise_predicate(c.spec.filter))) if c.spec.filter is not None else 0)
        rows.append([f if np.isfinite(f) else _NEG, -float(n_leaves), -c.evaluation.n_events / max(n_bars, 1)])
    return np.array(rows, dtype=float)


def _rank(cands: Sequence[GPCandidate], n_bars: int) -> list[tuple[int, float]]:
    """(front, -crowding) per candidate: smaller is better."""
    obj = _objectives(cands, n_bars)
    out: list[tuple[int, float]] = [(0, 0.0)] * len(cands)
    for r, front in enumerate(nondominated_fronts(obj)):
        cd = crowding(obj, front)
        for i in front:
            out[i] = (r, -cd[i])
    return out


# ---------------------------------------------------------------- the search

def _num(x: float | None) -> float | None:
    return x if x is not None and np.isfinite(x) else None


def evaluate_logged(t: OutcomeTensor, res: Resolver, s: StrategySpec, ledger: SearchLedger, *,
                    parents: tuple[str, ...] = (), generation: int = 0, min_events: int | None = None,
                    side_override: SideArr | None = None, null: bool = False, version: str = VERSION) -> GPCandidate:
    """Log the spec (ledger first, design 3.1), evaluate it (the evaluator refuses an unlogged hash), record its result.
    The result carries the search fitness (`gp_fitness`, what the search ranked by; null below the floors) and its
    components (per-fold t of net and of excess R, the clustered t, time in market, floor failures), so the top specs
    of a night can be ranked and read afterwards (first real nightly runs, 2026-10-30)."""
    h = spec_hash(s)
    ledger.log_spec(s, GENERATOR, version, parents=parents, null=null)
    mask, side = res.entries(s)
    if side_override is not None:
        mask, side = null_view(mask, side, side_override)
    e = evaluate(t, mask, side, exit_index(t, s), timeframe=s.timeframe, spec_hash=h, ledger=ledger, null=null,
                 min_events=min_events)
    ledger.record_result(h, {"n_events": e.n_events, "mean_r": _num(e.mean_r), "fitness": _num(e.fitness),
                             "excess_r": _num(e.excess_r), "excess_fitness": _num(e.excess_fitness),
                             "stress_mean_r": _num(e.stress_mean_r),
                             "floors": e.floors_passed, "generation": generation,
                             "search_fitness": _num(gp_fitness(e)), "t_stat": _num(e.t_stat),
                             "fold_t": [_num(x) for x in e.fold_t], "excess_fold_t": [_num(x) for x in e.excess_fold_t],
                             "time_in_market": e.time_in_market, "floor_failures": list(e.floor_failures)}, null=null)
    return GPCandidate(spec=s, spec_hash=h, parents=parents, generation=generation, evaluation=e)


def run_gp(t: OutcomeTensor, provider: MaskProvider, cfg: GPConfig, ledger: SearchLedger, *,
           side_override: SideArr | None = None, null: bool = False, resolver: Resolver | None = None) -> GPRun:
    """One bounded G2 search on the tensor (module docstring). `side_override` (with null=True) is G0's random side."""
    rng = np.random.default_rng(cfg.seed)
    cat = catalogue(cfg.families, cfg.grammar)
    res = resolver or Resolver(provider)
    done: dict[str, GPCandidate] = {}
    order: list[GPCandidate] = []

    def run_one(s: StrategySpec, parents: tuple[str, ...], gen: int) -> GPCandidate:
        h = spec_hash(s)
        if h not in done:
            done[h] = evaluate_logged(t, res, s, ledger, parents=parents, generation=gen, min_events=cfg.min_events,
                                      side_override=side_override, null=null, version=VERSION_OF[cfg.grammar])
            order.append(done[h])
        return done[h]

    seeds: dict[str, StrategySpec] = {}
    for _ in range(cfg.population * _TRIES):
        if len(seeds) == cfg.population:
            break
        s = random_spec(rng, cfg, cat)
        seeds.setdefault(spec_hash(s), s)
    pop = [run_one(s, (), 0) for s in seeds.values()]
    best_by_gen = [max((gp_fitness(c.evaluation) for c in pop), default=float("-inf"))]
    for gen in range(1, cfg.generations + 1):
        rank = _rank(pop, t.n_bars)

        def pick() -> int:
            ids = rng.choice(len(pop), size=min(cfg.tournament, len(pop)), replace=False)   # pop may be short
            return int(min(ids, key=lambda i: rank[int(i)]))

        kids: dict[str, tuple[StrategySpec, tuple[str, ...]]] = {}
        for _ in range(cfg.population * _TRIES):
            if len(kids) == cfg.population:
                break
            a = pop[pick()]
            if rng.random() < cfg.p_crossover:
                b = pop[pick()]
                child, parents = crossover(a.spec, b.spec, rng), tuple(sorted({a.spec_hash, b.spec_hash}))
            else:
                child, parents = a.spec, (a.spec_hash,)
            if rng.random() < cfg.p_mutation or spec_hash(child) in done:
                child = mutate(child, rng, cfg, cat)
            h = spec_hash(child)
            if h not in done and h not in kids:
                kids[h] = (child, parents)
        if not kids:
            break                                                   # the reachable space is exhausted
        offspring = [run_one(s, p, gen) for s, p in kids.values()]
        merged = pop + offspring
        r = _rank(merged, t.n_bars)
        pop = [merged[i] for i in sorted(range(len(merged)), key=lambda i: (r[i], i))[: cfg.population]]
        best_by_gen.append(max(best_by_gen[-1], max(gp_fitness(c.evaluation) for c in offspring)))
    return GPRun(evaluated=tuple(order), best=max(best_by_gen), best_by_generation=tuple(best_by_gen))


def null_seed(seed: int, rep: int) -> int:
    return seed * 1_000_003 + rep + 1


def gp_null(t: OutcomeTensor, provider: MaskProvider, cfg: GPConfig, ledger: SearchLedger, reps: int = NULL_REPS,
            resolver: Resolver | None = None) -> NullDistribution:
    """`reps` replications of the whole G2 search on random sides (timing kept); best-of-search per replication."""
    res = resolver or Resolver(provider)
    best = []
    for r in range(reps):
        sd = null_seed(cfg.seed, r)
        run = run_gp(t, provider, cfg.model_copy(update={"seed": sd}), ledger, side_override=hash_sides(t.n_bars, sd),
                     null=True, resolver=res)
        best.append(run.best)
    return NullDistribution(tuple(best), ledger.k_null())

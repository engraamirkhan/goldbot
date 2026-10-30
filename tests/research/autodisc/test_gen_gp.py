"""G2, typed genetic programming over the phase-1 primitive grammar (ADR 0058 phase 2, design section 2)."""
import numpy as np
import pytest

from goldbot.research.autodisc import SearchLedger, spec_hash
from goldbot.research.autodisc import gen_gp as G
from goldbot.research.autodisc.nulls import SyntheticProvider, inject_edge, make_noise_tensor
from goldbot.research.autodisc.primitives import PrimitiveProvider
from goldbot.research.autodisc.spec import MAX_DEPTH, MAX_LEAVES, Leaf, _depth, canonicalise, leaves

FAMS = ("tsmom", "box_break_close", "session_open", "nr4_break")
N = 3000
MIN_EV = 60


def cfg(**kw):
    base = dict(timeframe="15m", population=12, generations=3, seed=0, min_events=MIN_EV, families=FAMS)
    return G.GPConfig(**{**base, **kw})


def run(t, tmp_path, tag="g", prov=None, **kw):
    led = SearchLedger(tmp_path / f"{tag}.db", mode="synthetic")
    return G.run_gp(t, prov or SyntheticProvider(N), cfg(**kw), led), led


def _bounded(s):
    c = canonicalise(s)
    for p in (c.entry, c.filter):
        if p is not None:
            assert _depth(p) <= MAX_DEPTH and len(leaves(p)) <= MAX_LEAVES


def test_every_catalogue_leaf_is_one_the_primitive_provider_computes():
    prov = PrimitiveProvider.__new__(PrimitiveProvider)          # supports() reads no bars
    cat = G.catalogue(FAMS)
    assert cat and all(prov.supports(leaf) for leaf, _ in cat)
    kinds = {leaf.kind for leaf, _ in cat}
    assert kinds == {"structure", "time", "regime"}


def test_structural_and_time_leaves_are_weighted_two_to_one_over_regime_leaves():
    w = {leaf.kind: wt for leaf, wt in G.catalogue(FAMS)}
    assert w["structure"] == w["time"] == 2 * w["regime"]


def test_random_mutated_and_crossed_specs_stay_inside_the_grammar_bounds():
    rng = np.random.default_rng(1)
    c = cfg()
    cat = G.catalogue(FAMS)
    pop = [G.random_spec(rng, c, cat) for _ in range(200)]
    kids = [G.mutate(s, rng, c, cat) for s in pop] + [G.crossover(a, b, rng) for a, b in zip(pop, pop[1:], strict=False)]
    for s in pop + kids:
        _bounded(s)
        assert s.timeframe == "15m" and s.family is None
        if s.side_rule in ("with", "against"):
            assert s.side_primitive is not None and s.side_primitive.name == "family_rule"
    assert len({spec_hash(s) for s in pop}) > 150                         # the space is explored, not one spec


def test_the_search_is_deterministic_under_its_seed(tmp_path):
    t = make_noise_tensor(N, seed=2)
    a, _ = run(t, tmp_path, "a", seed=5)
    b, _ = run(t, tmp_path, "b", seed=5)
    c, _ = run(t, tmp_path, "c", seed=6)
    ha = [x.spec_hash for x in a.evaluated]
    assert ha == [x.spec_hash for x in b.evaluated] and ha != [x.spec_hash for x in c.evaluated]
    assert a.best == b.best


def test_every_evaluated_candidate_is_ledgered_first_and_counted_once_in_k(tmp_path):
    t = make_noise_tensor(N, seed=3)
    res, led = run(t, tmp_path)
    hashes = [x.spec_hash for x in res.evaluated]
    assert len(hashes) == len(set(hashes)) and len(hashes) <= 12 * (3 + 1)
    assert led.k_search() == len(hashes) and led.k_null() == 0
    assert all(led.result(h) is not None for h in hashes)
    kinds = [(e.kind, e.spec_hash) for e in led.entries()]
    for h in hashes:                                   # the spec row precedes its result row
        assert kinds.index(("spec", h)) < kinds.index(("result", h))
    assert led.verify() == len(led.entries())
    gen0 = [x for x in res.evaluated if x.generation == 0]
    assert all(x.parents == () for x in gen0) and any(x.parents for x in res.evaluated if x.generation > 0)


def test_a_rerun_on_the_same_ledger_adds_nothing_to_k(tmp_path):
    t = make_noise_tensor(N, seed=3)
    led = SearchLedger(tmp_path / "same.db", mode="synthetic")
    G.run_gp(t, SyntheticProvider(N), cfg(), led)
    k = led.k_search()
    G.run_gp(t, SyntheticProvider(N), cfg(), led)
    assert led.k_search() == k


def test_the_null_search_reruns_the_whole_gp_on_random_sides_and_counts_only_in_k_null(tmp_path):
    t = make_noise_tensor(N, seed=4)
    led = SearchLedger(tmp_path / "n.db", mode="synthetic")
    real = G.run_gp(t, SyntheticProvider(N), cfg(), led)
    k = led.k_search()
    null = G.gp_null(t, SyntheticProvider(N), cfg(), led, reps=3)
    assert len(null.best) == 3 and led.k_search() == k and led.k_null() > 0
    assert null.k_null == led.k_null() and real.best > float("-inf")


def test_the_null_keeps_the_timing_of_a_with_family_spec():
    prov = SyntheticProvider(N)
    side_rule = Leaf(kind="structure", name="family_rule", params={"rule": "tsmom"})
    s = G.random_spec(np.random.default_rng(0), cfg(), G.catalogue(FAMS)).model_copy(
        update={"side_rule": "with", "side_primitive": side_rule, "filter": None, "session_window": "all",
                "entry": Leaf(kind="time", name="mon")})
    res = G.Resolver(prov)
    mask, side = res.entries(s)
    fam_mask, _ = prov.family_entries("tsmom", "15m")
    nm, ns = G.null_view(mask, side, np.ones(N, dtype=np.int8))
    assert np.array_equal(nm, mask & fam_mask & (side != 0)) and set(np.unique(ns[nm])) == {1}


def test_gp_recovers_an_injected_edge_above_its_own_null(tmp_path):
    t0 = make_noise_tensor(N, seed=7)
    prov = SyntheticProvider(N, seed=0)
    mask, side = prov.family_entries("tsmom", "15m")
    # 0.6 R: the excess-R fitness reads the drift baseline from ~125 bars per (hour, side) cell on 3,000 synthetic bars,
    # which costs this small search some power against 0.4 R (8 years of 15m bars hold ~60x more per cell)
    t = inject_edge(t0, mask, side, edge_r=0.6)
    led = SearchLedger(tmp_path / "e.db", mode="synthetic")
    c = cfg(population=16, generations=4, seed=1, families=("tsmom", "nr4_break"))
    real = G.run_gp(t, prov, c, led)
    null = G.gp_null(t, prov, c, led, reps=9)
    assert real.best > null.threshold()
    top = max(real.evaluated, key=lambda x: G.gp_fitness(x.evaluation))
    assert "tsmom" in top.spec.model_dump_json()


@pytest.mark.integration          # ~20 s: 10 searches x 10 null searches through the real ledger and evaluator
def test_gp_size_on_pure_noise_stays_near_the_null_rate(tmp_path):
    hits = 0
    for s in range(10):
        t = make_noise_tensor(N, seed=100 + s)
        led = SearchLedger(tmp_path / f"s{s}.db", mode="synthetic")
        c = cfg(seed=s)
        real = G.run_gp(t, SyntheticProvider(N, seed=s), c, led)
        null = G.gp_null(t, SyntheticProvider(N, seed=s), c, led, reps=9)
        hits += real.best > max(null.threshold(), 0.0)
    assert hits <= 3                       # 1/10 per run by exchangeability; 4+ of 10 has probability 1.3%


def test_nondominated_fronts_and_crowding_follow_nsga2():
    obj = np.array([[3.0, 0.0], [2.0, 1.0], [1.0, 2.0], [1.0, 1.0], [0.0, 0.0]])
    assert G.nondominated_fronts(obj) == [[0, 1, 2], [3], [4]]
    d = G.crowding(obj, [0, 1, 2])
    assert d[0] == d[2] == float("inf") and np.isfinite(d[1])


def test_neighbours_step_one_ordinal_parameter_at_a_time():
    s = G.random_spec(np.random.default_rng(0), cfg(), G.catalogue(FAMS)).model_copy(update={
        "entry": Leaf(kind="regime", name="rvol_q_mid"), "filter": Leaf(kind="regime", name="hurst_trend"),
        "side_rule": "long", "side_primitive": None})
    s = s.model_copy(update={"exit": G.exit_menu("15m")[0]})                 # atr_1.0 x 1R
    ns = G.neighbours(s)
    names = sorted(n.model_dump_json() for n in ns)
    assert len(ns) == len(names) == 5 and spec_hash(s) not in {spec_hash(n) for n in ns}
    entries = {n.entry.name for n in ns if isinstance(n.entry, Leaf)}
    assert {"rvol_q_lo", "rvol_q_hi"} <= entries
    assert any(n.filter == Leaf(kind="regime", name="hurst_mid") for n in ns)
    assert any(n.exit.stop == "atr_1.5" for n in ns) and any(n.exit.target == "2R" for n in ns)


def test_config_bounds_the_search():
    with pytest.raises(ValueError):
        G.GPConfig(timeframe="15m", population=2)
    with pytest.raises(ValueError):
        G.GPConfig(timeframe="15m", generations=0)


def test_a_tournament_larger_than_the_population_is_refused_by_the_config():
    with pytest.raises(ValueError):
        G.GPConfig(timeframe="15m", population=4, tournament=8)
    assert G.GPConfig(timeframe="15m", population=4, tournament=4).tournament == 4


def test_selection_survives_a_population_smaller_than_the_tournament(tmp_path):
    t = make_noise_tensor(N, seed=2)
    c = G.GPConfig.model_construct(**{**dict(cfg()), "population": 4, "tournament": 8, "generations": 2})
    out = G.run_gp(t, SyntheticProvider(N), c, SearchLedger(tmp_path / "s.db", mode="synthetic"))
    assert len(out.best_by_generation) >= 2


def drift_only_tensor():
    """Every long outcome carries +0.5 R at every hour (gold's long drift), with no alignment edge anywhere."""
    t = make_noise_tensor(N, seed=5)
    return inject_edge(t, np.ones(N, dtype=bool), np.ones(N, dtype=np.int8), edge_r=0.5)


def test_gp_fitness_is_the_excess_r_t_so_a_long_drift_is_not_skill(tmp_path):
    """Quant review F4: the fitness is the min-fold t of excess R over the drift baseline, like the G0 null's."""
    t = drift_only_tensor()
    prov = SyntheticProvider(N)
    long_spec = G.StrategySpec(timeframe="15m", side_rule="long", entry=Leaf(kind="time", name="london"),
                               exit=G.exit_menu("15m")[0])
    led = SearchLedger(tmp_path / "x.db", mode="synthetic")
    c = G.evaluate_logged(t, G.Resolver(prov), long_spec, led, min_events=MIN_EV)
    assert c.evaluation.fitness is not None and c.evaluation.fitness > 2.0          # the net-R t sees the drift
    assert c.evaluation.excess_fitness is not None and abs(c.evaluation.excess_fitness) < 1.5   # excess-R t: no
    out, _ = run(t, tmp_path, tag="drift")
    assert out.best < 1.0 and out.best == max(G.gp_fitness(x.evaluation) for x in out.evaluated)

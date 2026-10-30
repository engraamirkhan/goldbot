import numpy as np
import pytest

from goldbot.research.autodisc import SearchLedger, spec_hash
from goldbot.research.autodisc.evaluate import evaluate
from goldbot.research.autodisc.gen_combine import exit_menu, family_spec, filters_for
from goldbot.research.autodisc.ledger import LedgerError
from goldbot.research.autodisc.nulls import (
    NULL_REPS,
    MaskCache,
    SyntheticProvider,
    hash_sides,
    inject_edge,
    make_noise_tensor,
    run_funnel,
)

FAMS = ["tsmom", "box_break_close", "session_open", "nr4_break"]
N = 3000
MIN_EV = 60


def specs(tf="15m", n_filters=3):
    flts = [None, *filters_for(tf)[:n_filters]]
    return [family_spec(f, tf, fl, e) for f in FAMS for fl in flts for e in exit_menu(tf)[:2]]


def funnel(t, tmp_path, tag, reps=NULL_REPS, seed=0, sp=None, n=N):
    led = SearchLedger(tmp_path / f"{tag}.db")
    r = run_funnel(t, sp or specs(), SyntheticProvider(n, seed=seed), led, "G1", "1", reps=reps, seed=seed,
                   min_events=MIN_EV)
    return r, led


def test_random_sides_are_balanced_deterministic_and_vary_with_seed():
    s = hash_sides(N, 7)
    assert set(np.unique(s)) == {-1, 1} and abs((s > 0).mean() - 0.5) < 0.03
    assert np.array_equal(s, hash_sides(N, 7)) and not np.array_equal(s, hash_sides(N, 8))


def test_null_keeps_the_spec_timing_and_only_changes_the_side():
    t = make_noise_tensor(N, seed=1)
    mask, side = SyntheticProvider(N).family_entries("tsmom", "15m")
    a = evaluate(t, mask, side, 0, timeframe="15m", min_events=MIN_EV)
    b = evaluate(t, mask, hash_sides(N, 3), 0, timeframe="15m", min_events=MIN_EV)
    assert a.input_digest == b.input_digest and a.n_events > MIN_EV
    assert a.mean_r != b.mean_r


@pytest.mark.parametrize("chunk", range(4))
def test_pure_noise_survivor_rate_within_stated_rate(tmp_path, chunk):
    # Stated rate with 20 reps and the 95th pct taken as 'higher' (the max): 1/21 = 4.8%. 4 chunks x 15 noise runs;
    # P(>= 4 hits in 15 at 5%) = 0.5%. The aggregate over 300 runs is measured in the hand-back.
    runs = 15
    hits = sum(bool(funnel(make_noise_tensor(N, seed=100 * chunk + i), tmp_path, f"n{i}", seed=i,
                           sp=specs(n_filters=1))[0].survivors) for i in range(runs))
    assert hits <= 3


def test_injected_edge_recovered(tmp_path):
    n = 20_000        # a +0.15 R edge needs the sample of a real explore window to clear a best-of-search null
    t = make_noise_tensor(n, seed=5)
    mask, side = SyntheticProvider(n).family_entries("tsmom", "15m")
    t = inject_edge(t, mask, side, edge_r=0.15)
    r, _ = funnel(t, tmp_path, "edge", n=n, sp=specs(n_filters=0))
    victims = {spec_hash(family_spec("tsmom", "15m", None, e)) for e in exit_menu("15m")[:2]}
    assert victims & set(r.survivors)
    assert r.real_best_null_percentile == 1.0
    others = {spec_hash(family_spec(f, "15m", None, e)) for f in FAMS[1:] for e in exit_menu("15m")[:2]}
    assert not others & set(r.survivors)


def test_ledger_first_k_counts_distinct_and_chain_verifies(tmp_path):
    t = make_noise_tensor(N, seed=2)
    r, led = funnel(t, tmp_path, "k", reps=3)
    assert r.k_search == len(set(map(spec_hash, specs())))
    assert led.k_null() == r.k_null == r.k_search
    assert led.verify() > 0
    again = run_funnel(t, specs(), SyntheticProvider(N), led, "G1", "1", reps=3, min_events=MIN_EV)
    assert again.evaluated == 0 and led.k_search() == r.k_search


def test_unlogged_spec_is_refused_by_the_evaluator(tmp_path):
    t = make_noise_tensor(N, seed=2)
    s = specs()[0]
    from goldbot.research.autodisc.nulls import search_specs
    with pytest.raises(LedgerError):
        search_specs(t, [s], MaskCache(SyntheticProvider(N)), ledger=SearchLedger(tmp_path / "x.db"))


def test_mask_cache_rejects_unlabelled_spec():
    s = specs()[0].model_copy(update={"family": None})
    with pytest.raises(ValueError):
        MaskCache(SyntheticProvider(10)).entries(s)

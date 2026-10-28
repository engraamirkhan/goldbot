import numpy as np
import pytest

from goldbot.research.autodisc import SearchLedger, spec_hash
from goldbot.research.autodisc.contracts import EXIT_MENU
from goldbot.research.autodisc.gen_combine import family_spec, filters_for
from goldbot.research.autodisc.nulls import (
    NULL_REPS,
    MaskCache,
    SyntheticProvider,
    hash_sides,
    inject_edge,
    make_noise_tensor,
    null_tensor,
    run_funnel,
)

FAMS = ["tsmom", "box_break_close", "session_open", "nr4_break"]
N = 4000


def specs(tf="15m"):
    flts = [None, *filters_for(tf)[:6]]
    return [family_spec(f, tf, fl, e) for f in FAMS for fl in flts for e in EXIT_MENU[:2]]


def funnel(t, tmp_path, tag, reps=NULL_REPS, seed=0):
    led = SearchLedger(tmp_path / f"{tag}.db")
    return run_funnel(t, specs(), SyntheticProvider(N, seed=seed), led, "G1", "1", reps=reps, seed=seed,
                      min_events=100), led


def test_null_keeps_timing_and_randomises_side():
    t = make_noise_tensor(N, seed=1)
    nt = null_tensor(t, 7)
    assert np.array_equal(nt.day_id, t.day_id) and np.array_equal(nt.cost_r, t.cost_r)
    side = hash_sides(N, 7)
    assert abs(side.mean() - 0.5) < 0.03
    assert np.array_equal(side, hash_sides(N, 7)) and not np.array_equal(side, hash_sides(N, 8))
    assert np.array_equal(nt.net_r[:, 0, :], nt.net_r[:, 1, :])


def test_pure_noise_survivor_rate_within_stated_rate(tmp_path):
    # stated rate with 20 reps and the 95th pct taken as 'higher' (the max): 1/21 = 4.8%.
    runs, hits = 120, 0
    for i in range(runs):
        r, _ = funnel(make_noise_tensor(N, seed=100 + i), tmp_path, f"n{i}", seed=i)
        hits += bool(r.survivors)
    assert hits / runs <= 0.05 + 2.5 * np.sqrt(0.05 * 0.95 / runs)      # binomial upper band at the 5% rate


def test_injected_edge_recovered(tmp_path):
    t = make_noise_tensor(N, seed=5)
    victim = family_spec("tsmom", "15m", None, EXIT_MENU[0])
    mask, side = SyntheticProvider(N).family_entries("tsmom", "15m")
    t = inject_edge(t, mask, side, edge_r=0.15)
    r, _ = funnel(t, tmp_path, "edge")
    assert spec_hash(victim) in r.survivors
    assert r.real_best_null_percentile == 1.0
    others = {spec_hash(family_spec(f, "15m", None, e)) for f in FAMS[1:] for e in EXIT_MENU[:2]}
    assert not others & set(r.survivors)


def test_ledger_written_before_result_and_k_counts_distinct(tmp_path):
    t = make_noise_tensor(N, seed=2)
    r, led = funnel(t, tmp_path, "k", reps=3)
    assert r.k_search == len(set(map(spec_hash, specs())))
    assert led.k_null() == r.k_null == r.k_search
    assert led.verify() > 0
    again = run_funnel(t, specs(), SyntheticProvider(N), led, "G1", "1", reps=3, min_events=100)
    assert again.evaluated == 0 and led.k_search() == r.k_search


def test_mask_cache_rejects_unlabelled_spec():
    s = family_spec("tsmom", "15m", None, EXIT_MENU[0]).model_copy(update={"family": None})
    with pytest.raises(ValueError):
        MaskCache(SyntheticProvider(10)).entries(s)

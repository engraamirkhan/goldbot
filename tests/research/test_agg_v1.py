"""agg-v1 AdaHedge over frozen model scores (goldbot/research/agg_v1.py; SYNTH-ml R7(b), item 15): the forward
"reinforce" step on fixtures. Weights, the mixability gap, the regret bound, scale invariance, the two fixed experts
("keep all", "abstain"), day losses from frozen keeps, the futility record."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research import agg_v1 as A

EXPERTS = ("seq1", "gbt", A.KEEP_ALL, A.ABSTAIN)


def test_a_new_aggregator_needs_keep_all_and_abstain_and_starts_uniform():
    with pytest.raises(ValueError, match="abstain"):
        A.init(("seq1", "gbt"))
    s = A.init(EXPERTS)
    np.testing.assert_allclose(A.weights(s), 0.25)
    assert s.delta == 0 and s.rounds == 0


def test_equal_losses_keep_the_weights_uniform_and_add_no_mixability_gap():
    s = A.init(EXPERTS)
    for _ in range(20):
        s = A.step(s, np.full(4, 0.3))
    np.testing.assert_allclose(A.weights(s), 0.25)
    assert s.delta == pytest.approx(0.0, abs=1e-12)


def test_the_weight_concentrates_on_a_dominant_expert():
    rng = np.random.default_rng(0)
    s = A.init(EXPERTS)
    for _ in range(400):
        loss = rng.normal(0, 1, 4)
        loss[1] -= 0.5                              # gbt is better by half a unit every round
        s = A.step(s, loss)
    w = A.weights(s)
    assert w.argmax() == 1 and w[1] > 0.9


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_regret_against_the_best_expert_is_at_most_twice_the_mixability_gap(seed: int):
    rng = np.random.default_rng(seed)
    s = A.init(EXPERTS)
    L = rng.uniform(-1, 1, (1_000, 4))
    for t in range(len(L)):
        s = A.step(s, L[t])
    regret = s.mix_loss - L.sum(axis=0).min()
    assert regret <= 2 * s.delta + 1e-9
    assert regret / len(L) < 0.1                   # sublinear: about sqrt(T ln K) on bounded losses


def test_adahedge_is_scale_free():
    rng = np.random.default_rng(4)
    L = rng.normal(0, 1, (100, 4))
    a, b = A.init(EXPERTS), A.init(EXPERTS)
    for t in range(len(L)):
        a, b = A.step(a, L[t]), A.step(b, 250.0 * L[t])
    np.testing.assert_allclose(A.weights(a), A.weights(b), atol=1e-9)


def test_an_alternating_sequence_that_breaks_follow_the_leader_stays_sublinear():
    s = A.init(("a", "b", A.KEEP_ALL, A.ABSTAIN))
    T = 2_000
    for t in range(T):
        x = 0.5 if t == 0 else (1.0 if t % 2 else -1.0)
        s = A.step(s, np.array([x, -x, 0.0, 0.0]))
    assert s.mix_loss / T < 0.1


def test_day_losses_come_from_frozen_keeps_with_keep_all_and_abstain_fixed():
    net = np.array([0.5, -1.0, 2.0, -0.2])
    day = np.array(["d1", "d1", "d2", "d2"])
    keeps = {"seq1": np.array([True, False, True, False]), "gbt": np.array([True, True, False, False])}
    L = A.day_losses(net, day, keeps, experts=EXPERTS)
    assert list(L.columns) == list(EXPERTS) and list(L.index) == ["d1", "d2"]
    np.testing.assert_allclose(L.loc["d1"].to_numpy(), [-0.5, 0.5, 0.5, 0.0])
    np.testing.assert_allclose(L.loc["d2"].to_numpy(), [-2.0, 0.0, -1.8, 0.0])


def test_the_aggregate_keep_is_the_weighted_vote_of_the_experts():
    s = A.init(EXPERTS)
    for _ in range(50):
        s = A.step(s, np.array([-1.0, 1.0, 1.0, 1.0]))    # seq1 wins every round
    keeps = {"seq1": np.array([True, False]), "gbt": np.array([False, True])}
    assert A.mix_keep(s, keeps).tolist() == [True, False]


def test_a_warm_start_replays_a_history_year_and_the_state_has_a_sha():
    L = pd.DataFrame(np.random.default_rng(5).normal(0, 1, (30, 4)), columns=list(EXPERTS))
    s = A.warm_start(EXPERTS, L)
    assert s.rounds == 30 and s.top_by_run == ()
    assert s.sha256() == A.warm_start(EXPERTS, L).sha256()
    with pytest.raises(ValueError, match="experts"):
        A.warm_start(EXPERTS, L[list(reversed(EXPERTS))])


def test_four_runs_in_a_row_with_abstain_on_top_is_a_futility_record():
    s = A.init(EXPERTS)
    for top in ("seq1", A.ABSTAIN, A.ABSTAIN, A.ABSTAIN):
        s = s.model_copy(update={"top_by_run": (*s.top_by_run, top)})
    assert not A.futile(s)
    s = s.model_copy(update={"top_by_run": (*s.top_by_run, A.ABSTAIN)})
    assert A.futile(s)


def test_closing_a_run_records_its_top_expert():
    s = A.init(EXPERTS)
    for _ in range(10):
        s = A.step(s, np.array([1.0, 1.0, 1.0, -1.0]))
    s = A.close_run(s)
    assert s.top_by_run == (A.ABSTAIN,)

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
    dec = pd.to_datetime(["2016-01-04 10:00", "2016-01-04 12:00", "2016-01-05 10:00", "2016-01-05 11:00"], utc=True)
    end = dec + pd.to_timedelta([24, 30, 24, 2], unit="h")
    L = A.day_losses(net, day, keeps, experts=EXPERTS, decision_utc=dec, horizon_end_utc=end)
    assert list(L.columns) == [*EXPERTS, A.OPEN, A.CLOSE] and list(L.index) == ["d1", "d2"]
    np.testing.assert_allclose(L.loc["d1", list(EXPERTS)].to_numpy(dtype=float), [-0.5, 0.5, 0.5, 0.0])
    np.testing.assert_allclose(L.loc["d2", list(EXPERTS)].to_numpy(dtype=float), [-2.0, 0.0, -1.8, 0.0])
    assert L.loc["d1", A.CLOSE] == end[1] and L.loc["d2", A.OPEN] == dec[2]


def test_a_round_counts_only_once_its_last_horizon_has_closed_before_the_next_round_opens():
    t0 = pd.Timestamp("2016-01-04 10:00", tz="UTC")
    rounds = pd.DataFrame({"seq1": [-10.0, 0.0, 0.0], "gbt": [0.0, 0.0, 0.0], A.KEEP_ALL: [0.0, 0.0, 0.0],
                           A.ABSTAIN: [0.0, 0.0, 0.0],
                           A.OPEN: [t0, t0 + pd.Timedelta(days=1), t0 + pd.Timedelta(days=4)],
                           A.CLOSE: [t0 + pd.Timedelta(days=3), t0 + pd.Timedelta(days=1, hours=1),
                                     t0 + pd.Timedelta(days=4, hours=1)]}, index=["d1", "d2", "d3"])
    used, final = A.replay(EXPERTS, rounds)
    np.testing.assert_allclose(used[0], 0.25)
    np.testing.assert_allclose(used[1], 0.25)            # d1 straddles d2's open: not yet a closed round
    assert used[2].argmax() == 0 and used[2][0] > 0.5    # by d3's open d1 has closed: seq1 leads
    assert final.rounds == 3


def test_history_run_refuses_a_non_oof_keep():
    gap = pd.Timedelta(days=1)
    dec = pd.date_range("2016-01-04 10:00", periods=4, freq="6h", tz="UTC")
    oof = pd.DataFrame({"decision_utc": dec, "horizon_end_utc": dec + pd.Timedelta(hours=24), "day": dec.normalize(),
                        "net_r": [0.5, -1.0, 2.0, -0.2], "fold_train_end": pd.Timestamp("2015-12-30", tz="UTC"),
                        "k_seq1": [True, False, True, False], "k_gbt": [True, True, False, False]})
    L = A.history_rounds(oof, experts=EXPERTS, gap=gap)
    assert list(L.columns) == [*EXPERTS, A.OPEN, A.CLOSE]
    late = oof.assign(fold_train_end=oof["fold_train_end"].where(oof.index != 2, dec[2] - pd.Timedelta(hours=1)))
    with pytest.raises(ValueError, match="out of fold"):
        A.history_rounds(late, experts=EXPERTS, gap=gap)
    with pytest.raises(ValueError, match="k_"):
        A.history_rounds(oof.rename(columns={"k_gbt": "gbt"}), experts=EXPERTS, gap=gap)


def test_the_aggregate_keep_of_the_fixed_experts_alone_takes_its_length_explicitly():
    s = A.init((A.KEEP_ALL, A.ABSTAIN))
    assert A.mix_keep(s, {}, n=3).tolist() == [True, True, True]


def test_the_aggregate_keep_is_the_weighted_vote_of_the_experts():
    s = A.init(EXPERTS)
    for _ in range(50):
        s = A.step(s, np.array([-1.0, 1.0, 1.0, 1.0]))    # seq1 wins every round
    keeps = {"seq1": np.array([True, False]), "gbt": np.array([False, True])}
    assert A.mix_keep(s, keeps, n=2).tolist() == [True, False]


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

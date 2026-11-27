"""The sr-v3 / zoo-v2 learner framework (goldbot/research/learners.py; SYNTH-ml R2, R4, items 3 and 10): one interface,
L-cw (the sr-v3 ridge body), G-cw (GBT-slow), HB (tf random walk + class deviations), AVG (equal-weight rank fusion),
the D-40 dense prior, weighted calibrators, keep rules, registered slots, deterministic artifacts, row parity."""
from __future__ import annotations

import numpy as np
import pytest

from goldbot.research import learners as L
from goldbot.research import objective as O
from goldbot.research import sr_v3 as V


def _data(n: int = 4_000, p: int = 3, seed: int = 1, insts: tuple[str, ...] = ("XAUUSD",)) -> L.TrainData:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, p))
    X[rng.random((n, p)) < 0.02] = np.nan
    beta = np.array([0.3, -0.2, 0.0][:p] + [0.0] * max(0, p - 3))
    gross = np.nan_to_num(X) @ beta + rng.normal(0, 1.0, n)
    cost = np.full(n, 0.07)
    inst = np.array([insts[i % len(insts)] for i in range(n)])
    return L.TrainData(X=X, columns=tuple(f"c{i}" for i in range(p)), gross=gross, net=gross - cost, cost=cost,
                       w=np.full(n, 0.5), instrument=inst, day=np.arange(n) // 40, tf=np.full(n, "15m"),
                       design_effect=1.0)


# ---------------------------------------------------------------------------------------------- L-cw
def test_lcw_on_one_instrument_is_the_obj_v1_offset_fit() -> None:
    d = _data()
    a = L.fit_lcw(d, lam=V.LAMBDA_ROWS, lam_a=820.0)
    ref = O.fit_offset(d.X, d.gross, d.w, lam0=V.LAMBDA_ROWS, design_effect=1.0)
    assert a.intercept == pytest.approx(ref.intercept, abs=1e-10)
    assert np.allclose(a.coef, ref.coef, atol=1e-10)
    assert np.allclose(a.value(d.X, d.instrument), ref.predict_gross(d.X), atol=1e-10)
    assert a.levels == {}


def test_shrinkage_of_a_standardised_coefficient_is_n_eff_over_n_eff_plus_540() -> None:
    n = 2_000
    z = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    d = L.TrainData(X=z[:, None], columns=("z",), gross=0.3 * z, net=0.3 * z, cost=np.zeros(n), w=np.ones(n),
                    instrument=np.full(n, "XAUUSD"), day=np.arange(n), tf=np.full(n, "15m"), design_effect=1.0)
    a = L.fit_lcw(d, lam=V.LAMBDA_ROWS, lam_a=0.0)
    assert a.coef[0] == pytest.approx(0.3 * n / (n + 540))


def test_weights_times_ten_and_phase_copies_at_one_over_p_give_the_same_fit() -> None:
    d = _data()
    a = L.fit_lcw(d, lam=540, lam_a=820)
    b = L.fit_lcw(d.subset(np.ones(len(d.w), bool), w=d.w * 10), lam=540, lam_a=820)
    assert np.allclose(a.coef, b.coef) and a.intercept == pytest.approx(b.intercept)
    P = 3
    idx = np.tile(np.arange(len(d.w)), P)
    c = L.fit_lcw(L.TrainData(X=d.X[idx], columns=d.columns, gross=d.gross[idx], net=d.net[idx], cost=d.cost[idx],
                              w=d.w[idx] / P, instrument=d.instrument[idx], day=d.day[idx], tf=d.tf[idx],
                              design_effect=float(P)), lam=540, lam_a=820)
    assert np.allclose(a.coef, c.coef) and a.intercept == pytest.approx(c.intercept)


def test_an_overlap_h_raises_the_relative_penalty_by_h() -> None:
    d = _data()
    a = L.fit_lcw(d, lam=540, lam_a=820)
    h = L.fit_lcw(d.subset(np.ones(len(d.w), bool), design_effect=4.0), lam=540, lam_a=820)
    q = L.fit_lcw(d, lam=4 * 540, lam_a=4 * 820)
    assert np.allclose(h.coef, q.coef) and abs(h.coef[0]) < abs(a.coef[0])


def test_instrument_levels_shrink_towards_the_unit_intercept_by_their_own_prior() -> None:
    d = _data(n=6_000, insts=("XAUUSD", "EURUSD", "XAGUSD"))
    g = d.gross + np.where(d.instrument == "XAGUSD", 0.4, 0.0)
    d = d.subset(np.ones(len(g), bool), gross=g)
    a = L.fit_lcw(d, lam=540, lam_a=200.0)
    assert set(a.levels) == {"XAUUSD", "EURUSD", "XAGUSD"}
    assert a.levels["XAGUSD"] > 0.1 > a.levels["XAUUSD"]
    s0 = a.level_rows["XAGUSD"]
    assert a.levels_unshrunk["XAGUSD"] == pytest.approx(a.levels["XAGUSD"] * (s0 + 200.0) / s0)
    assert abs(a.levels_unshrunk["XAGUSD"]) > abs(a.levels["XAGUSD"])
    tighter = L.fit_lcw(d, lam=540, lam_a=20_000.0)
    assert abs(tighter.levels["XAGUSD"]) < abs(a.levels["XAGUSD"])


def test_moments_are_additive_so_streamed_years_give_the_in_memory_fit() -> None:
    d = _data(n=3_000, insts=("XAUUSD", "EURUSD"))
    imp = L.medians(d.X)
    half = np.arange(len(d.w)) < 1_500
    m = L.moments(d.subset(half), imp)
    for k, v in L.moments(d.subset(~half), imp).items():
        m[k] = m[k] + v if k in m else v
    a = L.lcw_from_moments(m, imp, d.columns, lam=540, lam_a=820, design_effect=1.0)
    b = L.fit_lcw(d, lam=540, lam_a=820)
    assert np.allclose(a.coef, b.coef, atol=1e-12) and a.levels == pytest.approx(b.levels, abs=1e-12)


def test_intercept_only_body_has_no_columns_and_keeps_on_level_minus_cost() -> None:
    d = _data(insts=("XAUUSD", "EURUSD"))
    a = L.fit_lcw(d.subset(np.ones(len(d.w), bool), X=np.empty((len(d.w), 0)), columns=()), lam=540, lam_a=820)
    assert a.columns == () and a.coef == ()
    v = a.value(np.empty((3, 0)), np.array(["XAUUSD", "EURUSD", "USDJPY"]))
    assert v[0] == pytest.approx(a.intercept + a.levels["XAUUSD"]) and v[2] == pytest.approx(a.intercept)


def test_lcw_artifact_is_deterministic_and_round_trips() -> None:
    a, b = L.fit_lcw(_data(), lam=540, lam_a=820), L.fit_lcw(_data(), lam=540, lam_a=820)
    assert L.artifact_sha(a) == L.artifact_sha(b)
    back = L.load_artifact(L.artifact_json(a))
    assert isinstance(back, L.LcwArtifact) and L.artifact_sha(back) == L.artifact_sha(a)
    other = L.fit_lcw(_data(seed=2), lam=540, lam_a=820)
    assert L.artifact_sha(other) != L.artifact_sha(a)


def test_lcw_row_score_equals_the_batch_score() -> None:
    d = _data(insts=("XAUUSD", "EURUSD"))
    a = L.fit_lcw(d, lam=540, lam_a=820)
    batch = a.value(d.X, d.instrument)
    rows = np.array([a.value_row(d.X[i], str(d.instrument[i])) for i in range(50)])
    assert np.array_equal(rows, batch[:50])


# ---------------------------------------------------------------------------------------------- keep rules
def test_offset_keep_is_strict_and_probability_keep_is_at_one_half() -> None:
    assert L.keep("gross", np.array([0.07, 0.0701, 0.06]), np.full(3, 0.07)).tolist() == [False, True, False]
    assert L.keep("p", np.array([0.5, 0.4999, 0.9]), np.zeros(3)).tolist() == [True, False, True]


# ---------------------------------------------------------------------------------------------- G-cw
def _gdata(n: int = 6_000, seed: int = 3) -> L.TrainData:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    X[rng.random((n, 3)) < 0.05] = np.nan
    r2c = rng.uniform(0.5, 3.0, n)
    gross = 0.4 * np.nan_to_num(X[:, 0]) + rng.normal(0, 1, n)
    cost = 0.2 / r2c
    Xf = np.column_stack([X, r2c])
    return L.TrainData(X=Xf, columns=("a", "b", "c", L.RANGE_TO_COST), gross=gross, net=gross - cost, cost=cost,
                       w=rng.uniform(0.2, 1.0, n), instrument=np.full(n, "XAUUSD"), day=np.arange(n) // 30,
                       tf=np.full(n, "15m"), design_effect=1.0)


def test_gcw_is_gbt_slow_with_cost_weighted_targets_and_deterministic_bytes() -> None:
    d = _gdata()
    a, b = L.fit_gcw(d), L.fit_gcw(d)
    assert L.artifact_sha(a) == L.artifact_sha(b)
    assert a.params["max_depth"] == 2 and a.params["learning_rate"] == 0.01 and a.params["num_iterations"] == 400
    assert a.params["min_data_in_leaf"] == max(200, int(np.ceil(0.02 * len(d.w))))
    p = a.value(d.X)
    assert ((p > 0) & (p < 1)).all()
    assert np.corrcoef(p[np.isfinite(d.X[:, 0])], d.X[np.isfinite(d.X[:, 0]), 0])[0, 1] > 0.5


def test_gcw_is_monotone_in_range_to_cost() -> None:
    a = L.fit_gcw(_gdata())
    base = np.tile([[0.1, -0.2, 0.3, 0.0]], (50, 1))
    base[:, 3] = np.linspace(0.3, 4.0, 50)
    assert (np.diff(a.value(base)) >= -1e-15).all()


def test_gcw_row_score_equals_the_batch_score_and_survives_a_round_trip() -> None:
    d = _gdata()
    a = L.fit_gcw(d)
    back = L.load_artifact(L.artifact_json(a))
    assert isinstance(back, L.GcwArtifact)
    batch = back.value(d.X[:40])
    assert np.array_equal(np.array([back.value_row(d.X[i]) for i in range(40)]), batch)
    assert np.array_equal(batch, a.value(d.X[:40]))


# ---------------------------------------------------------------------------------------------- HB
def _cells(n: int = 3_000) -> dict[tuple[str, str], L.TrainData]:
    out = {}
    for j, (tf, cls) in enumerate((("5m", "gold"), ("15m", "gold"), ("15m", "fx10"), ("1h", "gold"))):
        d = _data(n=n, seed=10 + j)
        out[(tf, cls)] = d.subset(np.ones(n, bool), tf=np.full(n, tf),
                                  instrument=np.full(n, "XAUUSD" if cls == "gold" else "EURUSD"))
    return out


def test_hb_with_one_cell_and_a_rigid_class_prior_is_the_ridge_body() -> None:
    d = _data()
    imp = L.medians(d.X)
    hb = L.fit_hb({("15m", "gold"): L.moments(d, imp)[V.GOLD]}, {("15m", "gold"): imp}, d.columns, tf_order=("15m",),
                  lam0=540, lam_rw=540, lam_c=1e12, design_effect={("15m", "gold"): 1.0})
    lc = L.fit_lcw(d, lam=540, lam_a=0)
    assert np.allclose(hb.value(d.X, d.instrument, d.tf), lc.value(d.X, d.instrument), atol=1e-6)


def test_hb_a_rung_without_data_follows_its_neighbours_along_the_tf_ladder() -> None:
    cells = _cells()
    moms = {k: L.moments(v, L.medians(v.X))[str(v.instrument[0])] for k, v in cells.items()}
    imps = {k: L.medians(v.X) for k, v in cells.items()}
    hb = L.fit_hb(moms, imps, ("c0", "c1", "c2"), tf_order=("5m", "15m", "30m", "1h"), lam0=540, lam_rw=540,
                  lam_c=820, design_effect=dict.fromkeys(moms, 1.0))
    th = hb.theta
    assert np.allclose(th["30m"], (np.asarray(th["15m"]) + np.asarray(th["1h"])) / 2, atol=1e-9)
    assert hb.coef_sd[("15m|gold")][0] > 0


def test_hb_is_deterministic_and_row_parity_holds() -> None:
    cells = _cells()
    moms = {k: L.moments(v, L.medians(v.X))[str(v.instrument[0])] for k, v in cells.items()}
    imps = {k: L.medians(v.X) for k, v in cells.items()}
    def fit() -> L.HbArtifact:
        return L.fit_hb(moms, imps, ("c0", "c1", "c2"), tf_order=("5m", "15m", "1h"), lam0=540.0, lam_rw=540.0,
                        lam_c=820.0, design_effect=dict.fromkeys(moms, 1.0))
    a, b = fit(), fit()
    assert L.artifact_sha(a) == L.artifact_sha(b)
    d = cells[("15m", "fx10")]
    batch = a.value(d.X[:30], d.instrument[:30], d.tf[:30])
    assert np.array_equal(np.array([a.value_row(d.X[i], "EURUSD", "15m") for i in range(30)]), batch)
    assert isinstance(L.load_artifact(L.artifact_json(a)), L.HbArtifact)


# ---------------------------------------------------------------------------------------------- calibrators, AVG
def test_weighted_isotonic_matches_sklearn_and_is_monotone() -> None:
    from sklearn.isotonic import IsotonicRegression
    rng = np.random.default_rng(5)
    s = rng.normal(size=500)
    y = (rng.random(500) < 1 / (1 + np.exp(-s))).astype(float)
    w = rng.uniform(0.1, 2, 500)
    cal = L.fit_isotonic(s, y, w)
    ref = IsotonicRegression(out_of_bounds="clip").fit(s, y, sample_weight=w)
    assert np.allclose(cal.apply(s), ref.predict(s), atol=1e-12)
    grid = np.linspace(-4, 4, 200)
    assert (np.diff(cal.apply(grid)) >= 0).all()


def test_weighted_platt_recovers_a_known_curve() -> None:
    rng = np.random.default_rng(6)
    s = rng.normal(size=40_000)
    y = (rng.random(40_000) < 1 / (1 + np.exp(-(0.3 + 1.5 * s)))).astype(float)
    cal = L.fit_platt(s, y, np.ones(40_000))
    assert cal.a == pytest.approx(0.3, abs=0.05) and cal.b == pytest.approx(1.5, abs=0.06)


def test_avg_is_equal_weight_rank_fusion_then_a_weighted_calibrator() -> None:
    rng = np.random.default_rng(7)
    n = 5_000
    sig = rng.normal(size=n)
    net = 0.3 * sig + rng.normal(0, 1, n) - 0.05
    bases = {"L-cw": sig + rng.normal(0, 1, n), "G-cw": 1 / (1 + np.exp(-(sig + rng.normal(0, 1, n))))}
    kinds = {"L-cw": "gross", "G-cw": "p"}
    cost = np.full(n, 0.07)
    av = L.fit_avg(bases, kinds, {"L-cw": "sha1", "G-cw": "sha2"}, cost, net, np.ones(n))
    assert av.bases == ("G-cw", "L-cw") and len(av.refs[0]) <= L.AVG_REF_POINTS
    f = av.fused({k: v for k, v in bases.items()}, cost)
    assert ((f >= 0) & (f <= 1)).all()
    hi = {"L-cw": np.array([5.0]), "G-cw": np.array([0.99])}
    lo = {"L-cw": np.array([-5.0]), "G-cw": np.array([0.01])}
    assert av.value(hi, np.array([0.07]))[0] > av.value(lo, np.array([0.07]))[0]
    assert av.value_row({"L-cw": 5.0, "G-cw": 0.99}, 0.07) == av.value(hi, np.array([0.07]))[0]


# ---------------------------------------------------------------------------------------------- D-40, registry
def test_dense_prior_gives_two_columns_and_is_deterministic() -> None:
    d = _gdata()
    t0 = np.abs(d.gross) * 1.3
    a, b = L.fit_dense(d, t0_gross=t0), L.fit_dense(d, t0_gross=t0)
    assert L.artifact_sha(a) == L.artifact_sha(b)
    cols = a.columns_for(d.X, d.cost)
    assert set(cols) == set(L.DENSE_COLUMNS) and ((cols["dense_p_side"] > 0) & (cols["dense_p_side"] < 1)).all()
    row = a.value_row(d.X[0], float(d.cost[0]))
    assert row == (cols["dense_p_side"][0], cols["dense_range_cost"][0])


def test_registry_names_the_core_learners_and_holds_slots_that_refuse_to_fit() -> None:
    assert [k for k, s in L.LEARNERS.items() if s.status == "built"] == ["L-cw", "G-cw", "HB", "AVG"]
    assert L.LEARNERS["FS"].status == "off" and L.LEARNERS["B6"].status == "slot"
    assert L.LEARNERS["SEQ-1"].status == "slot"
    with pytest.raises(L.NotBuilt):
        L.fit_slot("B6")


def test_learner_modules_are_on_the_obj_v1_guard() -> None:
    assert {"goldbot.research.learners", "goldbot.research.sr_v3"} <= set(O.GUARDED_MODULES)
    assert O.guard_violations("goldbot.research.learners") == []
    assert O.guard_violations("goldbot.research.sr_v3") == []

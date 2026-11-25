"""H-101 model zoo and fusion (docs/research/prereg-2026-11-25-model-zoo-fusion.md, spec zoo-v1): the card's section 6
item 5 tests, on synthetic rows only (no history label is read here)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.research import model_zoo as Z
from goldbot.research import power_first as pf
from goldbot.research import self_retrain as sr
from goldbot.research import windows as W
from goldbot.research.model import MAX_FEATURES
from goldbot.research.registry import NOT_STARTED_POWER, TrialRegistry

BLOCKS = (2011, 2012, 2013)          # a short walk for speed; the pinned walk is 2011..2017
SETTINGS = load_settings()


def synth(*, arms: tuple[str, ...] = ("A", "B"), years: range = range(2010, 2014), per_year: int = 260,
          means: dict[str, float] | None = None, signal: float = 0.0, seed: int = 0, hold_s: float = 3 * 3600,
          start: str | None = None) -> pd.DataFrame:
    """Candidate rows in the history-replay schema: FEATURES, side, net R `r`, mid R `r_mid`, decision, label exit,
    max_hold_s, session day. `signal` puts within-arm information on `ret_4`; `means` shifts each arm's net R."""
    rng = np.random.default_rng(seed)
    means = means or dict.fromkeys(arms, 0.0)
    out = []
    for arm in arms:
        for y in years:
            lo = pd.Timestamp(start or f"{y}-01-02", tz="UTC")
            span = (pd.Timestamp(f"{y + 1}-01-01", tz="UTC") - lo).total_seconds()
            dec = lo + pd.to_timedelta(np.sort(rng.uniform(0, span - 2 * hold_s, per_year)), unit="s")
            dec = dec.floor("min")
            f = pd.DataFrame(rng.normal(size=(per_year, len(sr.FEATURES))), columns=list(sr.FEATURES))
            f["vol_tercile"] = rng.integers(0, 3, per_year).astype(float)
            side = rng.choice([-1.0, 1.0], per_year)
            r = means[arm] + signal * f["ret_4"].to_numpy() * side + rng.normal(0, 1.0, per_year)
            ext = dec + pd.to_timedelta(rng.uniform(60, hold_s, per_year), unit="s")
            out.append(f.assign(arm=arm, side=side, r=r, r_mid=r + 0.05, decision_utc=dec, label_exit_utc=ext,
                                max_hold_s=hold_s, session_day=dec.strftime("%Y-%m-%d"),
                                candidate_id=[f"{arm}-{y}-{i}" for i in range(per_year)]))
    return pd.concat(out, ignore_index=True)


@pytest.fixture(scope="module")
def rows() -> pd.DataFrame:
    r = synth(means={"A": 0.4, "B": -0.4}, per_year=1000)
    r.loc[r["arm"] == "A", "atr14_pct"] += 2.0     # arm identity readable from an input (quant item 4)
    return r


@pytest.fixture(scope="module")
def walk(rows: pd.DataFrame) -> Z.WalkForward:
    return Z.walk_forward(rows, blocks=BLOCKS)


# ------------------------------------------------------------------------------------------------- constants
def test_constants_are_pinned_to_the_card() -> None:
    assert Z.SPEC_VERSION == "zoo-v1"
    assert Z.REGISTRY_FAMILY == "model_zoo_fusion"
    assert Z.SEED == 20261125 and Z.BOOT == 10_000 and Z.MEAN_BLOCK_DAYS == 5
    assert Z.ARMS == ("RBR15", "Z3D", "Z1P", "Z9D", "Z10D", "S1A", "S1B", "F1A", "Z2P", "Z5P")
    assert Z.REPLAY_AS == {"RBR15": "RB15"} and Z.MIN_ARMS == 5
    assert Z.CANDIDATES == ("B1", "B2", "B3", "B4", "B5", "F1", "F2", "F3")
    assert Z.SIMPLICITY == {"B3": 1, "B1": 2, "B4": 3, "B5": 4, "B2": 5, "F1": 6, "F3": 7, "F2": 8}
    assert Z.C_BASE == 0.1 and Z.C_STACK == 1.0 and Z.STACK_CLIP == (0.01, 0.99)
    assert Z.LGBM_PARAMS == {"num_leaves": 15, "max_depth": 4, "learning_rate": 0.03, "n_estimators": 300,
                             "min_child_samples": 200, "subsample": 0.8, "subsample_freq": 1,
                             "colsample_bytree": 0.8, "reg_lambda": 1.0, "deterministic": True,
                             "force_row_wise": True, "n_jobs": 1, "random_state": 20261125, "verbose": -1}
    assert Z.KNN_MIN_K == 50 and Z.REGIME_MIN_ROWS == 100 and Z.F3_MIN_CELL == 100
    assert Z.KEEP_QUANTILE == pytest.approx(1 / 3) and Z.EMBARGO == pd.Timedelta(days=1)
    assert Z.OOF_BLOCKS == tuple(range(2011, 2018)) and Z.JUDGED_BLOCKS == tuple(range(2012, 2018))
    assert Z.CONFIRM_FROM == pd.Timestamp("2018-01-01", tz="UTC") and Z.CONFIRM_TO == W.HOLDOUT_FROM
    assert (Z.TEST_OLD_FROM, Z.TEST_OLD_TO) == (pd.Timestamp("2008-02-01", tz="UTC"), pd.Timestamp("2010-01-01", tz="UTC"))
    assert Z.TEST_OLD_YEARS == 1.92 and Z.MIN_EDGE_R == 0.05 and Z.ALPHA == 0.10 and Z.MIN_BLOCK_ROWS == 60
    assert Z.KEEP_SHARE == pytest.approx(2 / 3) and Z.RHO_DAY == 0.25 and Z.SD_R == 1.2 and Z.DELTA_R == 0.10
    assert Z.Z_L == pytest.approx(2.2414, abs=1e-3) and Z.Z_C == Z.Z_L and Z.Z_T == pytest.approx(1.2816, abs=1e-3)


def test_card_power_constants_equal_the_adr_0085_settings() -> None:
    p = SETTINGS.research.power_first
    assert (Z.DELTA_R, Z.SD_R, Z.RHO_DAY) == (p.delta_r, p.sd_r_floor, p.rho_day)
    assert (Z.MIN_GATE_POWER, Z.MIN_JOINT_POWER, Z.N_EFF_FLOOR) == (p.min_gate_power, p.min_joint_power, p.n_eff_floor)


def test_inputs_are_the_sr_v2_forty_within_the_cap() -> None:
    assert Z.INPUTS == sr.INPUTS
    assert len(Z.INPUTS) == 40 == MAX_FEATURES


# ------------------------------------------------------------------------------------------------- walk-forward
def test_every_candidate_is_scored_on_the_same_judged_rows(walk: Z.WalkForward) -> None:
    j = Z.judged(walk.oof, blocks=BLOCKS[1:])
    assert set(j["block"]) == {2012, 2013}
    assert np.isfinite(j[list(Z.CANDIDATES)].to_numpy()).all()
    assert len(j) == len(walk.oof[walk.oof["block"].isin(BLOCKS[1:])])


def test_first_block_is_meta_training_only(walk: Z.WalkForward) -> None:
    first = walk.oof[walk.oof["block"] == BLOCKS[0]]
    assert np.isfinite(first[list(Z.BASES)].to_numpy()).all()
    assert first[list(Z.FUSIONS)].isna().all().all()
    assert not first[[f"keep_{c}" for c in Z.CANDIDATES]].any().any()


def test_purge_holds_for_every_training_fit_cell_edge_and_tau_row(rows: pd.DataFrame, walk: Z.WalkForward) -> None:
    ext = pd.to_datetime(rows["label_exit_utc"], utc=True)
    hor = sr.horizon_end(rows)
    by_id = pd.DataFrame({"ext": ext.to_numpy(), "hor": hor}, index=rows["candidate_id"])
    for t, a in walk.audit.items():
        cut = a["t0"] - Z.EMBARGO
        for kind in ("train", "fit"):           # fit = every F2 / F3 fit row, F3 cell edge row and tau row
            ids = a[kind]
            if not ids:
                assert kind == "fit" and t == BLOCKS[0]
                continue
            sel = by_id.loc[ids]
            assert (sel["ext"] < cut).all() and (sel["hor"] < cut).all(), (t, kind)


def test_purge_drops_a_row_whose_horizon_crosses_the_embargo() -> None:
    r = synth(per_year=150, seed=3)
    first = r.index[r["decision_utc"].dt.year == 2012][0]
    r.loc[first, "decision_utc"] = pd.Timestamp("2012-01-01 00:30", tz="UTC")       # block 2012's t0
    r.loc[first, "label_exit_utc"] = pd.Timestamp("2012-01-01 01:30", tz="UTC")
    late = r.index[r["decision_utc"].dt.year == 2011][-1]
    r.loc[late, "decision_utc"] = pd.Timestamp("2011-12-30 20:00", tz="UTC")          # horizon 23:01 < t0 - 1 day
    r.loc[late, "label_exit_utc"] = pd.Timestamp("2011-12-30 21:00", tz="UTC")
    crossing = r.index[r["decision_utc"].dt.year == 2011][-2]
    r.loc[crossing, "decision_utc"] = pd.Timestamp("2011-12-30 22:00", tz="UTC")      # horizon 2011-12-31 01:01
    r.loc[crossing, "label_exit_utc"] = pd.Timestamp("2011-12-30 22:30", tz="UTC")    # exit before the cut
    wf = Z.walk_forward(r, blocks=(2011, 2012))
    assert wf.audit[2012]["t0"] == pd.Timestamp("2012-01-01 00:30", tz="UTC")
    assert r.loc[late, "candidate_id"] in wf.audit[2012]["train"]
    cid = r.loc[crossing, "candidate_id"]
    assert cid not in wf.audit[2012]["train"] and cid not in wf.audit[2012]["fit"]


def test_stacking_is_out_of_fold_only(walk: Z.WalkForward) -> None:
    """Every F2 / F3 training row's base score came from a model whose training ended before that row's block."""
    oof = walk.oof.set_index("candidate_id")
    for t, a in walk.audit.items():
        if not a["fit"]:
            continue
        fit = oof.loc[a["fit"]]
        assert (fit["block"] < t).all()
        assert (pd.to_datetime(fit["base_train_end"], utc=True) < pd.to_datetime(fit["block_t0"], utc=True)).all()


def _block_view(wf: Z.WalkForward, t: int) -> pd.DataFrame:
    cols = ["candidate_id", *Z.CANDIDATES, *[f"keep_{c}" for c in Z.CANDIDATES], *[f"tau_{c}" for c in Z.CANDIDATES]]
    return wf.oof.loc[wf.oof["block"] == t, cols].sort_values("candidate_id").reset_index(drop=True)


@pytest.mark.parametrize("t", BLOCKS[1:])
def test_truncation_invariance_for_all_eight_at_the_block_edge(rows: pd.DataFrame, walk: Z.WalkForward, t: int) -> None:
    """Perturbing every label decided or resolved at or after a block's start leaves that block's scores, taus and
    keep set unchanged, F2 and F3 included."""
    t0 = walk.audit[t]["t0"]
    late = (pd.to_datetime(rows["decision_utc"], utc=True) >= t0) | (pd.to_datetime(rows["label_exit_utc"], utc=True) >= t0)
    bent = rows.copy()
    rng = np.random.default_rng(9)
    bent.loc[late, "r"] = rng.normal(5, 3, int(late.sum()))
    bent.loc[late, "r_mid"] = bent.loc[late, "r"]
    again = Z.walk_forward(bent, blocks=BLOCKS)
    pd.testing.assert_frame_equal(_block_view(walk, t), _block_view(again, t))


def test_truncation_invariance_of_the_confirm_refit_and_read(rows: pd.DataFrame, walk: Z.WalkForward) -> None:
    """The confirm refit is unchanged by perturbing anything at or after its start minus 1 day, and the confirm read
    is unchanged by perturbing a row whose horizon or exit reaches the holdout (it is dropped, never truncated)."""
    t0 = pd.Timestamp("2013-07-01", tz="UTC")
    conf = synth(years=range(2014, 2015), per_year=120, seed=5)
    frozen = Z.freeze(rows, walk.oof, t0=t0)
    bent = rows.copy()
    late = (pd.to_datetime(rows["label_exit_utc"], utc=True) >= t0 - Z.EMBARGO) | (sr.horizon_end(rows) >= t0 - Z.EMBARGO)
    assert late.any()
    bent.loc[late, "r"] = 9.0
    oof_bent = walk.oof.copy()
    lo = (pd.to_datetime(oof_bent["label_exit_utc"], utc=True) >= t0 - Z.EMBARGO)
    oof_bent.loc[lo, "r"] = 9.0
    again = Z.freeze(bent, oof_bent, t0=t0)
    assert Z.frozen_hash(frozen) == Z.frozen_hash(again)
    pd.testing.assert_frame_equal(Z.score(frozen, conf), Z.score(again, conf))


def test_a_confirm_row_reaching_the_holdout_is_dropped_and_counted() -> None:
    conf = synth(years=range(2025, 2026), per_year=300, seed=6, start="2025-09-01", hold_s=2 * 86400)
    conf = conf[conf["decision_utc"] < W.HOLDOUT_FROM].reset_index(drop=True)
    edge = conf["decision_utc"] >= pd.Timestamp("2025-09-29 12:00", tz="UTC")
    conf.loc[edge, "label_exit_utc"] = pd.Timestamp("2025-09-30 23:59", tz="UTC")
    kept, dropped = Z.read_rows(conf, Z.CONFIRM_FROM, Z.CONFIRM_TO)
    hor = sr.horizon_end(conf)
    assert dropped == int((np.asarray(hor >= W.HOLDOUT_FROM) | (conf["label_exit_utc"] >= W.HOLDOUT_FROM)).sum()) > 0
    assert (sr.horizon_end(kept) < W.HOLDOUT_FROM).all()


def test_a_row_decided_in_the_holdout_is_refused() -> None:
    r = synth(per_year=50, seed=7)
    r.loc[0, "decision_utc"] = W.HOLDOUT_FROM + pd.Timedelta(hours=1)
    with pytest.raises(W.HoldoutSealed):
        Z.walk_forward(r, blocks=(2011,))
    with pytest.raises(W.HoldoutSealed):
        Z.read_rows(r, Z.CONFIRM_FROM, Z.CONFIRM_TO)
    with pytest.raises(W.HoldoutSealed):
        Z.count_table(r)


def test_keep_share_per_arm(walk: Z.WalkForward) -> None:
    """Each arm keeps about 2/3 for every candidate (per-arm tau, C1 / C4); two synthetic arms with different means,
    one input that reveals the arm and no within-arm signal give a pooled edge_nm within 0.04 of 0 for all 8 (quant
    condition at code review). Negative control: one global tau breaks the keep-share band."""
    j = Z.judged(walk.oof, blocks=BLOCKS[1:])
    for c in Z.CANDIDATES:
        share = {str(a): float(g[f"keep_{c}"].mean()) for a, g in j.groupby("arm")}
        # about 2/3 and the arms alike; realised shares run above 2/3 because a refit on more data shrinks a noise
        # model, so later scores sit tighter than the earlier OOF scores tau came from (reported per block and arm)
        assert all(0.6 <= v <= 0.9 for v in share.values()), (c, share)
        assert abs(share["A"] - share["B"]) <= 0.1, (c, share)
        e = Z.pooled_edge(j["r"].to_numpy(), j[f"keep_{c}"].to_numpy(bool), j["arm"].to_numpy())
        assert abs(e) <= 0.04, (c, e)
    shares = Z.keep_shares(j)
    assert set(shares) == set(Z.CANDIDATES) and set(shares["F2"]) == {(t, a) for t in BLOCKS[1:] for a in ("A", "B")}
    s3 = j["B3"].to_numpy()
    glob = s3 >= np.quantile(s3, Z.KEEP_QUANTILE)
    by_arm = {a: float(glob[j["arm"].to_numpy() == a].mean()) for a in ("A", "B")}
    assert abs(by_arm["A"] - by_arm["B"]) > 0.3 and not all(0.6 <= v <= 0.9 for v in by_arm.values()), by_arm


def test_pooled_edge_is_the_candidate_weighted_mean_of_per_arm_edges() -> None:
    r = np.array([1.0, 0.0, 10.0, 12.0, 14.0])
    k = np.array([True, False, True, True, False])
    arm = np.array(["a", "a", "b", "b", "b"])
    # a: kept 1.0 - all 0.5 = 0.5 (n 2); b: kept 11 - all 12 = -1 (n 3)
    assert Z.pooled_edge(r, k, arm) == pytest.approx((2 * 0.5 + 3 * -1.0) / 5)


def test_a_signal_inside_the_arms_is_found_by_the_learners() -> None:
    r = synth(signal=0.8, seed=11)
    j = Z.judged(Z.walk_forward(r, blocks=BLOCKS).oof, blocks=BLOCKS[1:])
    for c in ("B1", "B3", "F1"):
        assert Z.pooled_edge(j["r"].to_numpy(), j[f"keep_{c}"].to_numpy(bool), j["arm"].to_numpy()) > 0.15, c


def test_same_seed_gives_the_same_scores(rows: pd.DataFrame, walk: Z.WalkForward) -> None:
    again = Z.walk_forward(rows, blocks=BLOCKS)
    pd.testing.assert_frame_equal(walk.oof, again.oof)
    t0 = pd.Timestamp("2014-01-01", tz="UTC")
    assert Z.frozen_hash(Z.freeze(rows, walk.oof, t0=t0)) == Z.frozen_hash(Z.freeze(rows, walk.oof, t0=t0))


def test_a_regime_cell_too_small_or_a_missing_tercile_falls_back_to_b1() -> None:
    r = synth(per_year=200, seed=12)
    r.loc[r["vol_tercile"] == 2.0, "vol_tercile"] = np.where(np.arange(int((r["vol_tercile"] == 2.0).sum())) < 30, 2.0, 1.0)
    r.loc[r.index[:5], "vol_tercile"] = np.nan
    t0 = pd.Timestamp("2014-01-01", tz="UTC")
    wf = Z.walk_forward(r, blocks=(2011, 2012, 2013))
    fz = Z.freeze(r, wf.oof, t0=t0)
    probe = synth(years=range(2014, 2015), per_year=40, seed=13)
    probe["vol_tercile"] = [2.0, np.nan] * (len(probe) // 2)
    s = Z.score(fz, probe)
    np.testing.assert_allclose(s["B4"].to_numpy(), s["B1"].to_numpy())
    nan_vt = probe["vol_tercile"].isna().to_numpy()
    np.testing.assert_allclose(s.loc[nan_vt, "F3"].to_numpy(), s.loc[nan_vt, "F1"].to_numpy())


# ------------------------------------------------------------------------------------------------- statistics
def test_bootstrap_same_seed_gives_the_same_p(walk: Z.WalkForward) -> None:
    j = Z.judged(walk.oof, blocks=BLOCKS[1:])
    a = Z.metrics(j, "F2", boot=300, seed=1)
    b = Z.metrics(j, "F2", boot=300, seed=1)
    assert a == b
    assert a["p"] == max(a["p_edge"], a["p_kept"])


def test_bootstrap_draws_session_days_jointly_across_arms(walk: Z.WalkForward) -> None:
    j = Z.judged(walk.oof, blocks=BLOCKS[1:])
    bs = Z.JointBootstrap(j["session_day"].to_numpy(), boot=50, seed=2)
    assert bs.weights.shape == (50, j["session_day"].nunique())
    assert (bs.weights.sum(axis=1) == j["session_day"].nunique()).all()


def test_stationary_bootstrap_mean_block_is_five_days() -> None:
    bs = Z.JointBootstrap(np.array([f"d{i:04d}" for i in range(4000)]), boot=20, seed=3)
    runs = np.diff(bs.index, axis=1) != 1
    assert 1 / runs.mean() == pytest.approx(5.0, rel=0.1)


def test_paired_difference_uses_identical_index_draws(walk: Z.WalkForward) -> None:
    j = Z.judged(walk.oof, blocks=BLOCKS[1:])
    same = Z.paired(j, "F1", "F1", boot=200, seed=4)
    assert same["d"] == 0.0 and same["p_d"] == 1.0
    d = Z.paired(j, "F2", "B1", boot=200, seed=4, keep_draws=True)
    k_f = Z.metrics(j, "F2", boot=200, seed=4, keep_draws=True)["kept_draws"]
    k_b = Z.metrics(j, "B1", boot=200, seed=4, keep_draws=True)["kept_draws"]
    np.testing.assert_allclose(d["d_draws"], k_f - k_b)


def test_a_draw_with_no_kept_row_counts_against() -> None:
    j = Z.judged(Z.walk_forward(synth(per_year=120, seed=8), blocks=(2011, 2012)).oof, blocks=(2012,))
    j = j.assign(keep_B1=False)
    m = Z.metrics(j, "B1", boot=100, seed=1)
    assert m["p_edge"] == 1.0 and m["p_kept"] == 1.0


# ------------------------------------------------------------------------------------------------- reading rule
def _m(edge: float, kept: float, gross: float, p: float) -> dict[str, float]:
    return {"edge_nm": edge, "kept": kept, "gross": gross, "p": p}


def test_stage_l_continues_on_all_four_conditions_and_names_the_best_base() -> None:
    m = {c: _m(0.0, -0.1, 0.0, 0.9) for c in Z.CANDIDATES}
    m["B2"] = _m(0.09, 0.02, 0.01, 0.001)
    m["B5"] = _m(0.09, 0.02, 0.01, 0.001)        # tie with B2 on edge: the simpler (B5 rank 4) wins
    m["F1"] = _m(0.06, 0.01, -0.01, 0.001)       # gross < 0: does not continue
    d = Z.decide_l(m)
    assert d["continuing"] == ["B2", "B5"] and d["best_base"] == "B5" and d["verdict"] == "CONTINUE"


def test_stage_l_with_nothing_continuing_is_a_null_and_still_names_b_star() -> None:
    m = {c: _m(0.01 * i, -0.1, 0.0, 0.5) for i, c in enumerate(Z.CANDIDATES)}
    d = Z.decide_l(m)
    assert d["continuing"] == [] and d["verdict"] == "NULL" and d["best_base"] == "B5"


def test_stage_c_fusion_beats_b_star_only_through_the_paired_holm_test() -> None:
    m = {"B1": _m(0.08, 0.05, 0.01, 0.001), "F1": _m(0.10, 0.08, 0.02, 0.001), "F2": _m(0.12, 0.09, 0.02, 0.001)}
    pairs = {"F1": {"d": 0.03, "p_d": 0.04}, "F2": {"d": 0.04, "p_d": 0.30}}
    d = Z.decide_c(m, continuing=["B1", "F1", "F2"], best_base="B1", pairs=pairs)
    assert d["passing"] == ["B1", "F1", "F2"] and d["beats_b_star"] == ["F1"] and d["winner"] == "F1"
    d = Z.decide_c(m, continuing=["B1", "F1", "F2"], best_base="B1",
                   pairs={"F1": {"d": 0.03, "p_d": 0.20}, "F2": {"d": 0.04, "p_d": 0.30}})
    assert d["beats_b_star"] == [] and d["winner"] == "B1"          # the simplest passing candidate


def test_stage_c_a_fusion_never_wins_by_the_fallback_when_b_star_fails() -> None:
    """Quant 1: the fallback is the simplest passing BASE; a fusion wins only through the paired test."""
    m = {"B1": _m(0.01, -0.02, 0.0, 0.5), "F1": _m(0.10, 0.08, 0.02, 0.001)}
    d = Z.decide_c(m, continuing=["B1", "F1"], best_base="B1", pairs={"F1": {"d": 0.1, "p_d": 0.3}})
    assert d["passing"] == ["F1"] and d["winner"] is None and d["verdict"] == "NULL"


def test_stage_c_none_passing_is_a_null() -> None:
    m = {"B1": _m(0.01, 0.05, 0.01, 0.001)}
    d = Z.decide_c(m, continuing=["B1"], best_base="B1", pairs={})
    assert d["winner"] is None and d["verdict"] == "NULL"


def test_stage_t_with_fewer_than_five_arms_left_is_inconclusive() -> None:
    assert Z.decide_t(_m(0.2, 0.1, 0.0, 0.01), arms_left=4)["verdict"] == "INCONCLUSIVE"
    assert Z.decide_t(_m(0.2, 0.1, 0.0, 0.01), arms_left=5)["verdict"] == "QUALIFIED"
    assert Z.decide_t(_m(0.2, 0.1, 0.0, 0.2), arms_left=5)["verdict"] == "NULL"


def test_arms_whose_family_read_test_old_are_excluded_label_blind() -> None:
    rules = {"RBR15": "redbox_break", "Z3D": "round_break", "Z1P": "tick_surge"}
    assert Z.test_old_excluded(rules, {"round_break", "Z1P"}) == ["Z1P", "Z3D"]


# ------------------------------------------------------------------------------------------------- guards
def test_gold_only_guard_refuses_any_non_metal_instrument() -> None:
    Z.gold_only("XAUUSD")
    for sym in ("EURUSD", "US500", "XAGUSD"):
        with pytest.raises(Z.GoldOnly):
            Z.gold_only(sym)


def test_windows_register_the_card_for_read_once_confirm_and_test_old(tmp_path: Path) -> None:
    assert W.READ_ONCE_FAMILIES["model_zoo_fusion"] == frozenset({W.Window.LEARN, W.Window.CONFIRM, W.Window.TEST_OLD})
    ledger = tmp_path / "window_reads.jsonl"
    W.record_read_once(ledger, "model_zoo_fusion", "confirm", ref={"hash": "x"})
    with pytest.raises(W.AlreadyRead):
        W.record_read_once(ledger, "model_zoo_fusion", "confirm", ref={"hash": "y"})
    W.record_read_once(ledger, "model_zoo_fusion", "test_old", ref={"winner": "B1"})
    assert W.reads_of(ledger, "model_zoo_fusion") == {W.Window.CONFIRM, W.Window.TEST_OLD}
    with pytest.raises(ValueError):
        W.record_read_once(ledger, "unregistered_family", "confirm", ref={})
    with pytest.raises(W.HoldoutSealed):
        W.record_read_once(ledger, "model_zoo_fusion", "holdout", ref={})


# ------------------------------------------------------------------------------------------------- Stage 0
def _counts(n_per_day: int, days_per_year: int, arms: tuple[str, ...] = Z.ARMS) -> pd.DataFrame:
    out = []
    for y in (*Z.JUDGED_BLOCKS, *range(2018, 2026)):
        days = pd.bdate_range(f"{y}-01-03", periods=days_per_year, tz="UTC")
        for arm in arms:
            for d in days:
                dec = d + pd.to_timedelta(np.arange(n_per_day) * 600 + 3600 * 8, unit="s")
                out.append(pd.DataFrame({"arm": arm, "decision_utc": dec, "max_hold_s": 3600.0,
                                         "label_exit_utc": dec + pd.Timedelta(hours=1),
                                         "session_day": d.strftime("%Y-%m-%d")}))
    return pd.concat(out, ignore_index=True)


def test_stage_zero_counts_and_power_start_on_ample_rows() -> None:
    v, card = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    assert v.started, v.reasons
    names = [g.name for g in v.gates]
    assert names == ["L", "C", "T"]
    g = {x.name: x for x in v.gates}
    assert g["T"].n_expected == pytest.approx(g["L"].n_expected / 6 * 1.92)
    assert card.family == Z.REGISTRY_FAMILY


def test_stage_zero_refuses_with_few_arms_or_a_thin_block() -> None:
    v, _ = Z.stage0(Z.count_table(_counts(3, 120, arms=Z.ARMS[:4])), excluded=(), settings=SETTINGS)
    assert not v.started and any("arms" in r for r in v.reasons)
    thin = _counts(1, 5)
    v, _ = Z.stage0(Z.count_table(thin), excluded=(), settings=SETTINGS)
    assert not v.started and any("block" in r for r in v.reasons)


def test_stage_zero_test_old_n_comes_from_the_arms_left_after_the_ledger_exclusion() -> None:
    t = Z.count_table(_counts(3, 120))
    v_all, _ = Z.stage0(t, excluded=(), settings=SETTINGS)
    v_ex, _ = Z.stage0(t, excluded=Z.ARMS[:2], settings=SETTINGS)
    n = {g.name: g.n_expected for g in v_all.gates}
    n_ex = {g.name: g.n_expected for g in v_ex.gates}
    assert n_ex["T"] == pytest.approx(n["T"] * 8 / 10) and n_ex["L"] == n["L"]


def test_not_started_power_path_writes_zero_k(tmp_path: Path) -> None:
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    v = Z.stage0_record(reg, Z.count_table(_counts(1, 5)), excluded=(), settings=SETTINGS, agent_id="test")
    assert not v.started
    rows = [json.loads(x) for x in (tmp_path / "registry.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == [NOT_STARTED_POWER] and rows[0]["trial"] is None
    assert reg.n_trials == 0 and reg.budget_used(rows[0]["ts"][:4] + "Q" + str((int(rows[0]["ts"][5:7]) - 1) // 3 + 1)) == 0
    with pytest.raises(Z.PowerRefused):
        Z.book_stage_l(reg, SETTINGS, stage0=v, agent_id="test")
    assert reg.n_trials == 0


def test_a_started_stage_zero_writes_nothing_and_stage_l_books_eight_once(tmp_path: Path) -> None:
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    v = Z.stage0_record(reg, Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS, agent_id="test")
    reg_file = tmp_path / "registry.jsonl"
    assert v.started and (not reg_file.exists() or not reg_file.read_text().strip())
    s = SETTINGS
    booked = Z.book_stage_l(reg, s, stage0=v, agent_id="test")
    assert [r["config"]["candidate"] for r in booked] == list(Z.CANDIDATES) and reg.n_trials == 8
    assert Z.book_stage_l(reg, s, stage0=v, agent_id="test") == [] and reg.n_trials == 8


def test_power_first_start_or_refuse_records_a_given_verdict(tmp_path: Path) -> None:
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    v, card = Z.stage0(Z.count_table(_counts(1, 5)), excluded=(), settings=SETTINGS)
    other = card.model_copy(update={"card_id": "other"})
    with pytest.raises(ValueError):
        pf.start_or_refuse(reg, other, SETTINGS, agent_id="t", verdict=v)
    out = pf.start_or_refuse(reg, card, SETTINGS, agent_id="t", verdict=v)
    assert out is v and reg.n_trials == 0


def test_shuffled_label_leakage_check_reads_about_half_for_every_base_learner() -> None:
    r = synth(signal=0.8, seed=21, per_year=600)
    out = Z.shuffled_label_check(r, blocks=BLOCKS, judged_blocks=BLOCKS[1:])
    assert set(out["auc"]) == {"lightgbm_snapshot", *Z.BASES}
    assert out["ok"], out


# ------------------------------------------------------------------------------------------------- the CLI
def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "model_zoo_history", Path(__file__).resolve().parents[2] / "scripts" / "research" / "model_zoo_history.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_refuses_a_non_gold_instrument_before_reading(tmp_path: Path) -> None:
    with pytest.raises(Z.GoldOnly):
        _cli().main(["count", "--instrument", "EURUSD", "--out", str(tmp_path), "--cache", str(tmp_path)])


def test_cli_refuses_learn_without_a_started_stage_zero_and_confirm_without_stage_l(tmp_path: Path) -> None:
    cli = _cli()
    base = ["--out", str(tmp_path), "--cache", str(tmp_path), "--registry", str(tmp_path / "registry.jsonl")]
    with pytest.raises(SystemExit, match="Stage 0"):
        cli.main(["learn", *base])
    (tmp_path / "stage0.json").write_text(json.dumps({"started": False, "recorded_in_registry": True,
                                                      "verdict": {**_verdict_json(False)}}))
    with pytest.raises(SystemExit, match="did not start"):
        cli.main(["learn", *base])
    with pytest.raises(SystemExit, match="Stage L"):
        cli.main(["confirm", *base])
    (tmp_path / "stage_l.json").write_text(json.dumps({"verdict": "NULL", "frozen_hash": None,
                                                       "ledger": str(tmp_path / W.READS_LEDGER)}))
    with pytest.raises(SystemExit, match="confirm stays closed"):
        cli.main(["confirm", *base])


def test_cli_refuses_test_old_without_a_confirm_winner(tmp_path: Path) -> None:
    (tmp_path / "stage_l.json").write_text(json.dumps({"verdict": "CONTINUE", "frozen_hash": "x", "arms": [],
                                                       "ledger": str(tmp_path / W.READS_LEDGER)}))
    (tmp_path / "stage_c.json").write_text(json.dumps({"winner": None}))
    with pytest.raises(W.TestOldSealed):
        _cli().main(["test_old", "--out", str(tmp_path), "--cache", str(tmp_path),
                     "--registry", str(tmp_path / "registry.jsonl")])


@pytest.mark.parametrize("mode", ["learn", "confirm", "test_old"])
def test_cli_label_stages_require_the_registry_of_record(tmp_path: Path, mode: str) -> None:
    with pytest.raises(SystemExit, match="--registry"):
        _cli().main([mode, "--out", str(tmp_path), "--cache", str(tmp_path)])


def test_cli_has_no_ledger_override(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        _cli().main(["confirm", "--ledger", str(tmp_path / "other.jsonl"), "--out", str(tmp_path)])
    assert e.value.code == 2


def test_confirm_twice_through_a_different_ledger_is_refused(tmp_path: Path) -> None:
    """M1: the ledger is the registry's; a confirm against another registry (so another ledger) than the one Stage L
    recorded is refused before anything is read."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (tmp_path / "stage_l.json").write_text(json.dumps({"verdict": "CONTINUE", "frozen_hash": "h", "arms": [],
                                                       "continuing": ["B1"], "best_base": "B1",
                                                       "ledger": str(a / W.READS_LEDGER)}))
    W.record_read_once(a / W.READS_LEDGER, Z.REGISTRY_FAMILY, "confirm", ref={}, resume_key="h")
    with pytest.raises(SystemExit, match="ledger"):
        _cli().main(["confirm", "--out", str(tmp_path), "--cache", str(tmp_path), "--registry", str(b / "registry.jsonl")])


def test_a_second_learn_is_refused(tmp_path: Path) -> None:
    """M2: Stage L reads the learn labels once: a written Stage L record, a learn row in the read-once ledger without
    a resumable crash, or Stage L rows of this spec in the registry refuse a second learn."""
    cli, reg = _cli(), tmp_path / "registry.jsonl"
    base = ["learn", "--out", str(tmp_path), "--cache", str(tmp_path), "--registry", str(reg)]
    (tmp_path / "stage_l.json").write_text("{}")
    with pytest.raises(SystemExit, match="already"):
        cli.main(base)
    (tmp_path / "stage_l.json").unlink()
    TrialRegistry(reg).record(agent_id="t", family=Z.REGISTRY_FAMILY, config=Z.candidate_config("B1"),
                              feature_version=Z.SPEC_VERSION, rationale="x", results={})
    with pytest.raises(SystemExit, match="already"):
        cli.main(base)


def test_a_stage_whose_record_exists_is_never_resumed(tmp_path: Path) -> None:
    (tmp_path / "stage_l.json").write_text(json.dumps({"verdict": "CONTINUE", "frozen_hash": "h", "arms": [],
                                                       "ledger": str(tmp_path / W.READS_LEDGER)}))
    (tmp_path / "stage_c.json").write_text("{}")
    with pytest.raises(SystemExit, match="already"):
        _cli().main(["confirm", "--out", str(tmp_path), "--cache", str(tmp_path),
                     "--registry", str(tmp_path / "registry.jsonl")])


def test_learn_rows_exiting_after_the_learn_window_are_dropped_not_judged() -> None:
    """L4: a 2017 candidate whose horizon or exit reaches 2018 is dropped before the walk-forward."""
    r = synth(years=range(2017, 2018), per_year=50, seed=41)
    r.loc[0, "decision_utc"] = pd.Timestamp("2017-12-31 23:00", tz="UTC")
    r.loc[0, "label_exit_utc"] = pd.Timestamp("2018-01-01 01:00", tz="UTC")
    kept, dropped = Z.read_rows(r, *_cli().LEARN)
    assert dropped >= 1 and r.loc[0, "candidate_id"] not in set(kept["candidate_id"])


def test_feature_cache_key_changes_with_the_decisions_and_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _cli()
    d1 = pd.DatetimeIndex(["2012-01-03 10:00", "2012-01-03 10:05"], tz="UTC")
    d2 = pd.DatetimeIndex(["2012-01-03 10:00", "2012-01-03 10:10"], tz="UTC")
    k1 = cli.feature_key("5m", 2012, d1)
    assert k1 != cli.feature_key("5m", 2012, d2) and k1 == cli.feature_key("5m", 2012, d1)
    monkeypatch.setattr(Z, "SPEC_VERSION", "zoo-v2")
    assert k1 != cli.feature_key("5m", 2012, d1)


def _verdict_json(started: bool) -> dict:
    v, _ = Z.stage0(Z.count_table(_counts(1, 5)), excluded=(), settings=SETTINGS)
    return v.model_copy(update={"started": started}).model_dump(mode="json")


def test_a_thin_arm_without_earlier_oof_is_left_out_of_every_candidate_alike() -> None:
    r = pd.concat([synth(arms=("A",), per_year=300, seed=31),
                   synth(arms=("C",), years=range(2012, 2014), per_year=300, seed=32)], ignore_index=True)
    wf = Z.walk_forward(r, blocks=BLOCKS)
    j = Z.judged(wf.oof, blocks=BLOCKS[1:])
    # C trains from 2012 only: no model for block 2012 and no OOF before 2013, so no tau in 2013 either
    assert set(j["arm"]) == {"A"}
    assert Z.unjudged(wf.oof, blocks=BLOCKS[1:]) == {"C": int((r["arm"] == "C").sum())}


def test_an_arm_with_no_kept_row_in_a_draw_leaves_that_draw_but_does_not_void_it() -> None:
    W_ = np.array([[1.0, 1.0]])
    n = np.array([[1.0, 1.0], [1.0, 1.0]])       # day x arm
    s = np.array([[1.0, 0.0], [0.0, 2.0]])
    kn = np.array([[1.0, 0.0], [0.0, 0.0]])      # arm 2 keeps nothing
    ks = np.array([[1.0, 0.0], [0.0, 0.0]])
    edge, kept = Z._stats(W_, n, s, kn, ks)
    assert edge[0] == pytest.approx(1.0 - 0.5) and kept[0] == 1.0



def test_read_once_allows_one_resume_with_the_same_key_after_a_crash(tmp_path: Path) -> None:
    """L3: the ledger row is written as started before the read; a crash before the stage record lets the same run
    (same key) resume once, never with another key and never twice."""
    led = tmp_path / "l.jsonl"
    W.record_read_once(led, Z.REGISTRY_FAMILY, "confirm", ref={}, resume_key="h1")
    with pytest.raises(W.AlreadyRead):
        W.record_read_once(led, Z.REGISTRY_FAMILY, "confirm", ref={}, resume_key="h2")
    W.record_read_once(led, Z.REGISTRY_FAMILY, "confirm", ref={}, resume_key="h1")
    with pytest.raises(W.AlreadyRead):
        W.record_read_once(led, Z.REGISTRY_FAMILY, "confirm", ref={}, resume_key="h1")
    rows = [json.loads(x) for x in led.read_text().splitlines()]
    assert [r["status"] for r in rows] == ["started", "resumed"]


def _inputs(n: int = 50) -> pd.DataFrame:
    r = synth(years=range(2012, 2013), per_year=n, seed=51)
    return r.assign(available_utc_max=r["decision_utc"] - pd.Timedelta(minutes=1))


def test_inputs_with_a_missing_snapshot_time_fail_the_look_ahead_check() -> None:
    r = _inputs()
    Z.check_inputs(r)
    r.loc[3, "available_utc_max"] = pd.NaT
    with pytest.raises(ValueError, match="point in time"):
        Z.check_inputs(r)
    r = _inputs()
    r.loc[3, "available_utc_max"] = pd.Timestamp(r["decision_utc"].iloc[3]) + pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="point in time"):
        Z.check_inputs(r)


def test_a_feature_column_mostly_missing_is_refused_not_median_imputed() -> None:
    r = _inputs(200)
    r[list(Z.STRUCTURAL_NAN)] = np.nan          # never computable on the engine's 90k-minute window: allowed, named
    Z.check_inputs(r)
    r.loc[r.index[:40], "rsi14"] = np.nan        # 10 % > MAX_NAN_SHARE
    with pytest.raises(ValueError, match="rsi14"):
        Z.check_inputs(r)



def test_stage_l_is_refused_after_a_not_started_power_row_of_this_spec(tmp_path: Path) -> None:
    """Quant 3: a NOT STARTED (power) row of this spec blocks Stage L whatever verdict is passed in."""
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    Z.stage0_record(reg, Z.count_table(_counts(1, 5)), excluded=(), settings=SETTINGS, agent_id="test")
    good, _ = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    with pytest.raises(Z.PowerRefused):
        Z.book_stage_l(reg, SETTINGS, stage0=good, agent_id="test")
    assert reg.n_trials == 0


def test_stage_l_books_only_in_the_allocated_quarter(tmp_path: Path) -> None:
    """Quant 5: the 8 K are allocated from 2026Q4; any other quarter is refused."""
    from datetime import datetime, timezone
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    good, _ = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    with pytest.raises(Z.QuarterRefused):
        Z.book_stage_l(reg, SETTINGS, stage0=good, agent_id="t", now=datetime(2027, 1, 5, tzinfo=timezone.utc))
    assert reg.n_trials == 0


def test_stage_l_rows_carry_the_provenance(tmp_path: Path) -> None:
    """Quant 2: the K rows carry the git SHA, the learn-rows sha256 and the Stage 0 verdict; the outcome and the
    frozen hash are written back as evidence."""
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    good, _ = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    rows = Z.book_stage_l(reg, SETTINGS, stage0=good, agent_id="t", provenance={"git_sha": "abc", "learn_rows_sha256": "f00"})
    assert all(r["results"]["git_sha"] == "abc" and r["results"]["learn_rows_sha256"] == "f00"
               and r["results"]["stage0"]["started"] for r in rows)
    Z.write_back_stage_l(reg, {"verdict": "NULL", "continuing": []}, frozen_hash="h")
    ev = reg.evidence(kind="stage_l_outcome")
    assert len(ev) == 8 and ev[0]["payload"]["frozen_hash"] == "h" and ev[0]["payload"]["verdict"] == "NULL"


def test_power_first_refuses_a_given_verdict_looser_than_settings_or_inconsistent(tmp_path: Path) -> None:
    """Quant 9: a passed verdict cannot carry looser thresholds than the settings, nor claim a start its gates
    contradict."""
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    v, card = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    with pytest.raises(ValueError, match="looser"):
        pf.start_or_refuse(reg, card, SETTINGS, agent_id="t", verdict=v.model_copy(update={"min_gate_power": 0.4}))
    bad = v.model_copy(update={"joint_power": 0.1})
    with pytest.raises(ValueError, match="contradict"):
        pf.start_or_refuse(reg, card, SETTINGS, agent_id="t", verdict=bad)


def test_stage_zero_records_the_joint_power_independence_deviation() -> None:
    v, _ = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    assert any("independent" in n for n in Z.STAGE0_NOTES)


def test_input_coverage_names_the_informative_inputs() -> None:
    r = _inputs(200)
    r[list(Z.STRUCTURAL_NAN)] = np.nan
    cov = Z.check_inputs(r)
    assert cov["informative_inputs"] == 38 and set(cov["nan_share"]) == set(sr.FEATURES)


def test_learn_is_refused_once_a_stage_l_outcome_was_written_back(tmp_path: Path) -> None:
    """Quant residual: a crash between the write-back and stage_l.json must not let a resume read Stage L again."""
    reg_path = tmp_path / "registry.jsonl"
    reg = TrialRegistry(reg_path)
    good, _ = Z.stage0(Z.count_table(_counts(3, 120)), excluded=(), settings=SETTINGS)
    Z.book_stage_l(reg, SETTINGS, stage0=good, agent_id="t")
    assert not Z.stage_l_written_back(reg)
    Z.write_back_stage_l(reg, {"verdict": "NULL"}, frozen_hash="h")
    assert Z.stage_l_written_back(reg)
    W.record_read_once(reg_path.with_name(W.READS_LEDGER), Z.REGISTRY_FAMILY, "learn", ref={}, resume_key="k")
    with pytest.raises(SystemExit, match="written back"):
        _cli().main(["learn", "--out", str(tmp_path), "--cache", str(tmp_path), "--registry", str(reg_path)])
    with pytest.raises(ValueError, match="already"):
        Z.write_back_stage_l(reg, {"verdict": "NULL"}, frozen_hash="h")

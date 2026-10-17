"""E-2 (BACKLOG 75): the top-tercile cut of the inner cross-fitted p.

Design rule (preregistration-2027Q1.md, "Threshold rule, per row"): O-4 "uses a top-tercile cut ... the 2/3 quantile
of the inner cross-fitted out-of-fold p of the training fold's rows, never in-sample training p ... with a test
proving that the cut never sees a test-fold row". Quant plan review (2026-10-17), binding: the prior set is the
earlier folds' test rows inside fold k's training window; overlapping test folds raise; `method="linear"` pinned; the
CPCV cut is taken from p_raw before the 0.5 fill and guarded by `np.isfinite(cut)`. The default rule (calibrated p >
break-even + 0.02) stays byte-identical (golden captured from the tree before E-2)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research import cpcv, pipeline
from goldbot.research.model import MetaLabelModel
from goldbot.research.walkforward import Fold, walk_forward_splits

GOLDEN = Path(__file__).parent / "data" / "tercile_default_golden.json"
WINDOW: dict[str, Any] = dict(train_months=12, test_months=4, step_months=4, purge_days=2, embargo_days=1)
CPCV_T0, CPCV_T1 = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")


# ---------------------------------------------------------------------------------------------- fixtures
class StubModel(MetaLabelModel):
    """A deterministic meta-model: p_raw = 0.5 + 0.4 x / (1 + |x|) of column `x` (plain IEEE arithmetic, identical on
    every platform), so the golden pins the selection path, not LightGBM. It remembers the rows it was fitted on and
    fails if asked to predict one of them (`test_no_in_sample_training_p_reaches_the_cut`)."""
    train_rows: Any = None

    def fit(self, X: pd.DataFrame, y: pd.Series, w: pd.Series | None = None) -> StubModel:
        self.train_rows = frozenset(X.index.tolist())
        return self

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        assert self.train_rows is not None and self.train_rows.isdisjoint(X.index.tolist()), \
            "in-sample training p was requested"
        x = X["x"].to_numpy(dtype=float)
        return 0.5 + 0.4 * x / (1.0 + np.abs(x))

    def calibrate(self, oof_pred: np.ndarray, oof_y: np.ndarray) -> StubModel:
        return self

    def importance(self) -> pd.Series:
        return pd.Series(dtype=float)


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "MetaLabelModel", StubModel)
    monkeypatch.setattr(cpcv, "MetaLabelModel", StubModel)


def _wf_frames(n_days: int = 760, per_day: int = 6, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Candidates every 4 h over about two years with one informative column `x` (folds of ~720 rows, so every
    calibrated fold is isotonic: deterministic)."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2021-01-04", periods=n_days * per_day, freq=f"{24 // per_day}h", tz="UTC")
    n = len(ts)
    x = rng.normal(size=n)
    y = (rng.random(n) < 0.5 + 0.4 * x / (1 + np.abs(x))).astype(int)
    labels = pd.DataFrame({"ts_utc": ts, "ts_exit": ts + pd.Timedelta(hours=6), "target_hit": y,
                           "ret": np.where(y == 1, 0.004, -0.002), "risk": 0.002, "atr_sig": 1.0,
                           "stop_atr": 1.0, "target_atr": 2.0, "weight": 1.0})
    feats = pd.DataFrame({"x": x, "z": rng.normal(size=n), "side": 1.0})
    gross = labels[["ts_utc", "ts_exit", "ret", "risk"]].copy()
    return labels, gross, feats


def _walk(labels: pd.DataFrame, gross: pd.DataFrame, feats: pd.DataFrame, folds: list[Fold],
          **kw: Any) -> pipeline.ResearchResult:
    return pipeline._walk_forward("stub", labels, gross, feats, ["side", "x", "z"], folds, "f-test", n_trials=1,
                                  trades_per_year=None, extra_cost_usd=0.0, holdout=None, score_holdout=False,
                                  window=WINDOW, timeframe=None, **kw)


def _cpcv_frames(n: int = 4000, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    ts = pd.DatetimeIndex(np.sort(rng.uniform(CPCV_T0.value, CPCV_T1.value - 5 * 86400e9, n)).astype("int64"),
                          tz="UTC")
    te = ts + pd.to_timedelta(rng.integers(1, 49, n), unit="h")
    x = rng.normal(size=n)
    hit = rng.random(n) < 0.5 + 0.4 * x / (1 + np.abs(x))
    labels = pd.DataFrame({"ts_utc": ts, "ts_exit": te, "target_hit": hit.astype(int),
                           "ret": np.where(hit, 0.002, -0.001), "risk": 0.001, "atr_sig": 1.0, "stop_atr": 1.0,
                           "target_atr": 2.0, "weight": 1.0})
    feats = pd.DataFrame({"x": x, "z": rng.normal(size=n), "side": 1.0})
    return labels, feats


def _run_cpcv(labels: pd.DataFrame, feats: pd.DataFrame, **kw: Any) -> dict[str, Any]:
    return cpcv.run_cpcv(labels, feats, ["side", "x", "z"], edges=cpcv.group_edges(CPCV_T0, CPCV_T1), purge_days=2,
                         embargo_days=1, extra_cost_usd=0.0, **kw)


def golden_snapshot() -> dict[str, Any]:
    """The default rule's outputs on fixed frames with StubModel in place: the walk-forward's out-of-fold columns and
    metrics, and the CPCV result. Called with today's defaults only, so the same builder captured the golden on the
    tree before E-2 (65ece6e) and checks the tree after it."""
    labels, gross, feats = _wf_frames()
    folds = list(walk_forward_splits(labels, **WINDOW))
    res = _walk(labels, gross, feats, folds)
    oof = res.oof
    c_labels, c_feats = _cpcv_frames()
    out = {
        "walk_forward": {"p_raw": oof["p_raw"].tolist(), "p": oof["p"].tolist(),
                         "threshold": oof["threshold"].tolist(), "taken": oof["taken"].astype(bool).tolist(),
                         "metrics": res.metrics},
        "cpcv": _run_cpcv(c_labels, c_feats),
    }
    return json.loads(json.dumps(out, default=str))


def _same(got: Any, want: Any, path: str = "") -> None:
    """Exact for structure, strings, ints and bools; floats to 1e-12 relative (the same tree reproduces them bit for
    bit; the tolerance only absorbs a libm ulp on another platform)."""
    if isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), f"{path}: keys {sorted(got)} != {sorted(want)}"
        for k in want:
            _same(got[k], want[k], f"{path}.{k}")
    elif isinstance(want, list):
        assert isinstance(got, list) and len(got) == len(want), f"{path}: length"
        for i, (g, w) in enumerate(zip(got, want)):
            _same(g, w, f"{path}[{i}]")
    elif isinstance(want, float) and not isinstance(want, bool):
        assert got == pytest.approx(want, rel=1e-12, abs=1e-15, nan_ok=True), f"{path}: {got} != {want}"
    else:
        assert got == want and type(got) is type(want), f"{path}: {got!r} != {want!r}"


def _fold(k: int, test: np.ndarray, train: np.ndarray | None = None) -> Any:
    """A fold with only the indices fold_cuts reads (train_idx None: a CPCV path group, no single training set)."""
    return SimpleNamespace(k=k, test_idx=np.asarray(test, dtype=int),
                           train_idx=None if train is None else np.asarray(train, dtype=int))


# ---------------------------------------------------------------------------------------------- the cut
def test_the_cut_for_a_fold_is_the_two_thirds_quantile_of_earlier_folds_p_only():
    rng = np.random.default_rng(0)
    p = rng.uniform(size=400)
    folds = [_fold(0, np.arange(0, 100)), _fold(1, np.arange(100, 200)), _fold(2, np.arange(200, 300)),
             _fold(3, np.arange(300, 400))]
    cuts = pipeline.fold_cuts(p, folds)
    assert np.isnan(cuts[0])                                             # nothing earlier
    for i in range(1, 4):
        assert cuts[i] == np.quantile(p[: 100 * i], 2 / 3, method="linear")
    assert pipeline.TERCILE_Q == 2 / 3 and pipeline.TERCILE_METHOD == "linear"


def test_the_prior_set_is_earlier_folds_test_rows_inside_the_folds_training_window_only():
    """Quant condition 1: on a rolling window (15m: 24 months) "prior" must not reach past fold k's training rows."""
    rng = np.random.default_rng(1)
    p = rng.uniform(size=400)
    folds = [_fold(0, np.arange(0, 100)), _fold(1, np.arange(100, 200)), _fold(2, np.arange(200, 300)),
             _fold(3, np.arange(300, 400), train=np.arange(150, 290))]   # rolled past fold 0, purged before 300
    cuts = pipeline.fold_cuts(p, folds)
    assert cuts[3] == np.quantile(p[150:290], 2 / 3, method="linear")
    assert cuts[3] != np.quantile(p[:300], 2 / 3, method="linear")


def test_overlapping_test_folds_raise():
    """Quant condition 2: a specialist override with step_months < test_months gives overlapping test folds."""
    p = np.linspace(0, 1, 300)
    with pytest.raises(ValueError, match="overlap"):
        pipeline.fold_cuts(p, [_fold(0, np.arange(0, 150)), _fold(1, np.arange(100, 300))])


def test_cuts_are_identical_across_two_runs_and_row_order_shuffles_of_the_prior_set():
    rng = np.random.default_rng(2)
    p = rng.uniform(size=600)
    folds = [_fold(k, np.arange(100 * k, 100 * (k + 1))) for k in range(6)]
    a, b = pipeline.fold_cuts(p, folds), pipeline.fold_cuts(p, folds)
    shuffled = [_fold(f.k, rng.permutation(f.test_idx)) for f in folds]
    c = pipeline.fold_cuts(p, shuffled)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(a, c)                                  # bit for bit, not approximately


def test_a_candidate_equal_to_the_cut_is_taken_and_one_ulp_below_is_not():
    cut = np.array([0.61, 0.61, 0.61])
    p_raw = np.array([0.61, np.nextafter(0.61, 0.0), np.nextafter(0.61, 1.0)])
    taken = pipeline.select(p_raw, np.full(3, 0.5), cut, "tercile_inner")
    assert taken.tolist() == [True, False, True]


def test_a_fold_with_fewer_than_50_prior_rows_has_nan_cut_and_takes_no_trade():
    p = np.linspace(0.01, 0.99, 149)
    folds = [_fold(0, np.arange(0, 49)), _fold(1, np.arange(49, 99)), _fold(2, np.arange(99, 149))]
    cuts = pipeline.fold_cuts(p, folds)
    assert np.isnan(cuts[0]) and np.isnan(cuts[1])                       # 0 and 49 prior rows: fail closed
    assert np.isfinite(cuts[2])                                          # 99
    rows = pipeline.row_cuts(cuts, folds, len(p))
    taken = pipeline.select(p, np.full(len(p), 0.9), rows, "tercile_inner")
    assert not taken[:99].any() and taken[99:].any()


def test_nan_p_raw_rows_are_never_taken_and_do_not_enter_the_quantile():
    rng = np.random.default_rng(3)
    p = rng.uniform(size=300)
    p[rng.choice(200, 60, replace=False)] = np.nan
    folds = [_fold(0, np.arange(0, 100)), _fold(1, np.arange(100, 200)), _fold(2, np.arange(200, 300))]
    cuts = pipeline.fold_cuts(p, folds)
    prior = p[:200]
    assert cuts[2] == np.quantile(prior[np.isfinite(prior)], 2 / 3, method="linear")
    p_test = p.copy()
    p_test[[200, 201]] = np.nan
    taken = pipeline.select(p_test, np.full(len(p), 0.9), pipeline.row_cuts(cuts, folds, len(p)), "tercile_inner")
    assert not taken[[200, 201]].any() and not taken[np.isnan(p_test)].any()


def test_an_unknown_threshold_rule_is_refused():
    with pytest.raises(ValueError, match="threshold rule"):
        pipeline.select(np.ones(2), np.ones(2), np.zeros(2), "top_decile")      # type: ignore[arg-type]


def test_the_default_rule_selects_calibrated_p_strictly_above_break_even_plus_margin():
    p_cal = np.array([np.nan, 0.52, np.nextafter(0.52, 1.0), 0.9])
    taken = pipeline.select(np.full(4, 0.5), p_cal, np.full(4, 0.52), "break_even_margin")
    assert taken.tolist() == [False, False, True, True]


# ---------------------------------------------------------------------------------------------- walk-forward
def test_changing_every_test_fold_row_p_and_label_leaves_that_folds_cut_unchanged():
    """Leakage, end to end with the real model: fold k's own rows (features and labels) and every later fold are
    scrambled; the cuts of folds 0..k do not move."""
    labels, gross, feats = _wf_frames(n_days=900, per_day=3, seed=4)
    folds = list(walk_forward_splits(labels, **WINDOW))
    assert len(folds) >= 4
    k = 2
    base = _walk(labels, gross, feats, folds, threshold_rule="tercile_inner").metrics["selection"]["fold_cuts"]
    later = np.concatenate([folds[j].test_idx for j in range(k, len(folds))])
    rng = np.random.default_rng(9)
    f2, l2 = feats.copy(), labels.copy()
    f2.loc[later, ["x", "z"]] = rng.normal(size=(len(later), 2)) * 50
    l2.loc[later, "target_hit"] = 1 - l2.loc[later, "target_hit"]
    moved = _walk(l2, gross, f2, folds, threshold_rule="tercile_inner").metrics["selection"]["fold_cuts"]
    assert moved[: k + 1] == base[: k + 1] and any(c is not None for c in base[: k + 1])
    assert moved[k + 1:] != base[k + 1:]                                 # the scramble did reach the later cuts


def test_point_in_time_truncation_at_a_folds_test_end_gives_the_same_cuts_up_to_that_fold():
    """Quant condition 6: what fold k's cut uses is known before its test window. Labels cut after fold k (its own
    outcomes flipped as well) give identical fold_cuts[:k+1] through `_walk_forward`."""
    labels, gross, feats = _wf_frames(n_days=900, per_day=3, seed=5)
    folds = list(walk_forward_splits(labels, **WINDOW))
    k = 2
    full = _walk(labels, gross, feats, folds, threshold_rule="tercile_inner").metrics["selection"]["fold_cuts"]
    keep = np.asarray(pd.to_datetime(labels["ts_utc"], utc=True) < folds[k].test_end)
    lt, gt, ft = labels[keep].reset_index(drop=True), gross[keep].reset_index(drop=True), feats[keep].reset_index(drop=True)
    lt.loc[folds[k].test_idx, "target_hit"] = 1 - lt.loc[folds[k].test_idx, "target_hit"]
    tfolds = list(walk_forward_splits(lt, **WINDOW))
    assert len(tfolds) == k + 1
    for a, b in zip(tfolds, folds[: k + 1]):
        np.testing.assert_array_equal(a.train_idx, b.train_idx)
        np.testing.assert_array_equal(a.test_idx, b.test_idx)
    cut = _walk(lt, gt, ft, tfolds, threshold_rule="tercile_inner").metrics["selection"]["fold_cuts"]
    assert cut == full[: k + 1] and cut[k] is not None


def test_no_in_sample_training_p_reaches_the_cut(stub_model: None):
    """The spy model fails on any prediction of a row it was fitted on, so every p the cut reads is out of fold; and
    each cut is the quantile of exactly the earlier folds' out-of-fold p inside the fold's training window."""
    labels, gross, feats = _wf_frames()
    folds = list(walk_forward_splits(labels, **WINDOW))
    res = _walk(labels, gross, feats, folds, threshold_rule="tercile_inner")
    cuts = res.metrics["selection"]["fold_cuts"]
    p_raw = res.oof["p_raw"].to_numpy()
    seen: list[np.ndarray] = []
    for f, c in zip(folds, cuts):
        prior = np.intersect1d(np.concatenate(seen), f.train_idx) if seen else np.array([], dtype=int)
        if len(prior) >= pipeline.MIN_CALIBRATION_ROWS:
            assert c == float(np.quantile(p_raw[prior], 2 / 3, method="linear"))
        else:
            assert c is None
        seen.append(f.test_idx)


def test_the_tercile_rule_takes_test_p_at_or_above_its_folds_cut_and_records_the_selection(stub_model: None):
    labels, gross, feats = _wf_frames()
    folds = list(walk_forward_splits(labels, **WINDOW))
    res = _walk(labels, gross, feats, folds, threshold_rule="tercile_inner")
    oof, sel = res.oof, res.metrics["selection"]
    assert sel["threshold_rule"] == "tercile_inner" and sel["tercile_q"] == 2 / 3
    assert sel["tercile_method"] == "linear" and sel["margin"] is None
    for f, c in zip(folds, sel["fold_cuts"]):
        p = oof["p_raw"].to_numpy()[f.test_idx]
        want = np.zeros(len(p), dtype=bool) if c is None else p >= c
        np.testing.assert_array_equal(oof["taken"].to_numpy()[f.test_idx], want)
    per = sel["retention"]["per_fold"]
    assert per[0] == 0.0 and all(0.2 < r < 0.5 for r in per[1:])         # recorded, never read
    taken = oof[oof["taken"]]
    below = float((taken["p"] < taken["threshold"] - pipeline.THRESHOLD_MARGIN).mean())
    assert sel["share_taken_below_breakeven"] == below
    json.dumps(res.metrics, default=str)


# ---------------------------------------------------------------------------------------------- CPCV
def test_the_cpcv_path_cut_uses_only_groups_before_g_on_that_path():
    rng = np.random.default_rng(6)
    n_groups = 6
    groups = np.repeat(np.arange(n_groups), 100)
    p = rng.uniform(size=len(groups))
    y = rng.integers(0, 2, len(groups))
    _, _, cuts = cpcv.path_selection(p, groups, n_groups, y, np.full(len(p), 0.5), "tercile_inner")
    assert cuts is not None and np.isnan(cuts[0])
    for g in range(1, n_groups):
        assert cuts[g] == np.quantile(p[groups < g], 2 / 3, method="linear")
    later = p.copy()
    later[groups >= 3] = rng.uniform(size=int((groups >= 3).sum()))
    _, _, moved = cpcv.path_selection(later, groups, n_groups, y, np.full(len(p), 0.5), "tercile_inner")
    assert moved is not None
    np.testing.assert_array_equal(moved[:4], cuts[:4])


def test_cpcv_cut_ignores_nan_filled_rows():
    """Quant condition 7: the 0.5 fill feeds the calibrator only; the cut reads p_raw before it, finite rows only, and
    only finite rows count toward the 50."""
    n_groups = 3
    groups = np.repeat(np.arange(n_groups), 100)
    p = np.linspace(0.6, 0.9, len(groups))
    p[:70] = np.nan                                                      # group 0: 30 finite rows (an unfitted split)
    y = np.tile([0, 1], len(groups) // 2)
    taken, _, cuts = cpcv.path_selection(p, groups, n_groups, y, np.full(len(p), 0.5), "tercile_inner")
    assert cuts is not None and np.isnan(cuts[1])                        # 30 < 50 finite prior rows: no cut
    assert not taken[groups == 1].any()
    prior = p[groups < 2]
    assert cuts[2] == np.quantile(prior[np.isfinite(prior)], 2 / 3, method="linear")
    assert cuts[2] > 0.5                                                 # the 0.5 fill never reached the quantile
    assert not taken[np.isnan(p)].any()


def test_run_cpcv_under_the_tercile_rule_records_the_rule_and_each_paths_cuts(stub_model: None):
    labels, feats = _cpcv_frames()
    res = _run_cpcv(labels, feats, threshold_rule="tercile_inner")
    assert res["threshold_rule"] == "tercile_inner" and "top-tercile" in res["evaluation"]
    assert all(len(p["fold_cuts"]) == res["n_groups"] and p["fold_cuts"][0] is None for p in res["paths"])
    assert all(p["n_trades"] > 0 for p in res["paths"])


# ---------------------------------------------------------------------------------------------- default unchanged
def test_default_rule_results_are_unchanged_on_a_golden_frame(stub_model: None):
    """Captured with `golden_snapshot()` on 65ece6e (before E-2); the default rule's out-of-fold columns, metrics and
    CPCV result must not move (Q4 rows stay comparable)."""
    want = json.loads(GOLDEN.read_text())
    got = golden_snapshot()
    _same(got["walk_forward"], want["walk_forward"], "walk_forward")
    _same(got["cpcv"], want["cpcv"], "cpcv")


# ---------------------------------------------------------------------------------------------- discovery and registry
def _discover_with(rule: str) -> Any:
    from goldbot.research.discovery import DiscoveryConfig, run_discovery
    from tests.test_discovery import POOL, _synthetic
    from tests.test_discovery import WINDOW as D_WINDOW
    labels, gross, feats = _synthetic(n_days=600)
    folds = list(walk_forward_splits(labels, **D_WINDOW))
    cfg = DiscoveryConfig(n_subsamples=4, top_k=2, freq_threshold=0.5, stability_threshold=0.5,
                          threshold_rule=rule)                                  # type: ignore[arg-type]
    return run_discovery("synthetic-e2", labels, gross, feats, folds, "f-test", D_WINDOW, POOL, cfg)


def test_default_reading_rule_string_is_byte_identical_and_tercile_replaces_the_break_even_clause():
    """Quant condition 3: the default string is unchanged (the director compares cited_rule == the pre-registration's
    reading_rule); the tercile rule REPLACES "(cross-fitted, break-even + margin)" and adds one sentence: promotion
    needs a separate trial under the live rule."""
    from goldbot.research.discovery import LIVE_RULE_NOTE, READING_RULE, TERCILE_CLAUSE, reading_rule
    want = json.loads(GOLDEN.read_text())["reading_rule"]
    assert reading_rule("feature") == reading_rule("feature", "break_even_margin") == READING_RULE == want["feature"]
    assert reading_rule("group") == reading_rule("group", "break_even_margin") == want["group"]
    assert "test-fold raw p >= the 2/3 quantile (linear)" in TERCILE_CLAUSE and "no break-even floor" in TERCILE_CLAUSE
    assert "promotion needs a separate trial under the live rule" in LIVE_RULE_NOTE
    for unit in ("feature", "group"):
        t = reading_rule(unit, "tercile_inner")
        assert "(cross-fitted, break-even + margin)" not in t and TERCILE_CLAUSE in t and LIVE_RULE_NOTE in t
        back = t.replace(TERCILE_CLAUSE, "(cross-fitted, break-even + margin)").replace(" " + LIVE_RULE_NOTE, "")
        assert back == want[unit]                                           # nothing else in the rule moved


def test_the_default_discovery_config_dump_and_its_hash_are_unchanged():
    """A default config dumps (and so hashes) exactly as before E-2: H-02's pinned dump and every queued hash hold;
    the tercile rule is in the dump, so it is part of the hashed, pre-registered configuration."""
    from goldbot.research.discovery import DiscoveryConfig
    from goldbot.research.registry import config_hash
    assert "threshold_rule" not in DiscoveryConfig().model_dump()
    assert DiscoveryConfig(threshold_rule="break_even_margin").model_dump() == DiscoveryConfig().model_dump()
    t = DiscoveryConfig(threshold_rule="tercile_inner")
    assert t.model_dump()["threshold_rule"] == "tercile_inner" and DiscoveryConfig(**t.model_dump()) == t
    assert config_hash({"discovery": t.model_dump()}) != config_hash({"discovery": DiscoveryConfig().model_dump()})
    with pytest.raises(ValueError):
        DiscoveryConfig(threshold_rule="top_decile")                            # type: ignore[arg-type]


def test_threshold_rule_and_fold_cuts_are_recorded_on_the_registry_row(tmp_path: Path):
    from goldbot.research.registry import DISCOVERY, TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    for rule in ("tercile_inner", "break_even_margin"):
        d = _discover_with(rule)
        reg.record(agent_id="synthetic-e2", family="discovery_tsmom", config={"rule": rule}, feature_version="f",
                   rationale="r", results=d.result.metrics, status=DISCOVERY)
    rows = TrialRegistry(tmp_path / "r.jsonl")._rows()                          # after a reload
    t, b = (r["results"]["discovery"] for r in rows)
    assert t["threshold_rule"] == "tercile_inner" and t["tercile_q"] == 2 / 3 and t["tercile_method"] == "linear"
    assert t["margin"] is None and len(t["fold_cuts"]) == rows[0]["results"]["n_folds"]
    assert t["fold_cuts"][0] is None and all(isinstance(c, float) for c in t["fold_cuts"][1:])
    assert set(t["retention"]) == {"per_fold", "overall", "n_taken", "n_scored"} and t["retention"]["n_taken"] > 0
    assert isinstance(t["share_taken_below_breakeven"], float)
    assert "promotion needs a separate trial under the live rule" in t["reading_verdict"]["rule"]
    assert b["threshold_rule"] == "break_even_margin" and b["margin"] == pipeline.THRESHOLD_MARGIN
    assert b["tercile_q"] is None and b["tercile_method"] is None and b["fold_cuts"] is None
    assert b["share_taken_below_breakeven"] in (0.0, None)                  # p > break-even + margin by construction
    assert "selection" not in rows[1]["results"]                              # the default walk-forward is unchanged


def test_the_preregistration_plan_names_the_threshold_rule():
    from goldbot.research.discovery import DiscoveryConfig, threshold_plan
    assert threshold_plan(DiscoveryConfig(threshold_rule="tercile_inner")) == {
        "threshold_rule": "tercile_inner", "tercile_q": 2 / 3, "tercile_method": "linear", "margin": None}
    assert threshold_plan(DiscoveryConfig()) == {"threshold_rule": "break_even_margin", "tercile_q": None,
                                                 "tercile_method": None, "margin": pipeline.THRESHOLD_MARGIN}


def test_a_second_reading_of_the_same_candidates_under_the_other_rule_is_a_new_trial_in_n_trials_effective(
        tmp_path: Path):
    from goldbot.research.discovery import DiscoveryConfig, discovery_trial_config
    from goldbot.research.registry import DISCOVERY, TrialRegistry, config_hash
    from goldbot.specialists import SPECIALISTS
    spec = SPECIALISTS["tsmom"](timeframe="4h", max_bars=12)
    cfgs = [discovery_trial_config(spec, DiscoveryConfig(threshold_rule=r), "column", ["atr"], "a", "b", None)
            for r in ("break_even_margin", "tercile_inner")]
    assert config_hash(cfgs[0]) != config_hash(cfgs[1])                       # not the same trial re-read
    reg = TrialRegistry(tmp_path / "r.jsonl")
    for c in cfgs:
        reg.record(agent_id=spec.agent_id, family="discovery_tsmom", config=c, feature_version="f", rationale="r",
                   results={"discovery": {"k_eff": 12}}, status=DISCOVERY)
    assert reg.n_trials == 2 and reg.n_trials_effective == 2 + 12 + 12       # each reading pays its K_eff


def test_threshold_rule_change_after_preregistration_is_refused_by_director(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch):
    """The queued discovery row pins its DiscoveryConfig; a run reading it under the other threshold rule hashes
    differently and is refused (director.queued_mismatch), never charged to the open budget."""
    from goldbot.research.discovery import H02_CLUSTERS
    from tests.test_discover_holdout_and_digests import (
        _b1_past_the_holdout,
        _discover,
        _release,
        _research_pass,
        _stop_at_link,
        _written,
    )
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars)
    before = reg.path.read_text()
    rp = _research_pass(monkeypatch)
    _stop_at_link(monkeypatch)
    tercile = {"survivor_unit": "feature", "group_top_k": 3, "clusters": H02_CLUSTERS, "threshold_rule": "tercile_inner"}
    with pytest.raises(SystemExit) as exc:
        _discover(rp, tmp_path, reg, bars, _b1_past_the_holdout(), discovery=tercile)
    msg = str(exc.value)
    assert "pending queued row" in msg and "discovery.threshold_rule" in msg
    assert reg.path.read_text() == before                                  # no pre-registration, no trial


# ---------------------------------------------------------------------------------------------- quant review round 1
def test_tercile_verdict_needs_1000_trades():
    """Quant R1 fix 1: O-4's rule is "net mean R > 0 on >= 1,000 trades"; under the tercile rule 999 taken trades with
    a positive mean R read inconclusive. The default rule's verdict and string do not move."""
    from goldbot.research.discovery import MIN_READ_EVENTS, TERCILE_CLAUSE, reading_rule, reading_verdict
    assert MIN_READ_EVENTS == 1000 and "(at least 1,000 taken)" in TERCILE_CLAUSE
    t_rule = reading_rule("group", "tercile_inner")
    assert "(at least 1,000 taken)" in t_rule
    assert reading_verdict(0.56, 0.53, 50_000, 0.05, 999, rule=t_rule, min_taken=MIN_READ_EVENTS)["decision"] \
        == "inconclusive"
    assert reading_verdict(0.56, 0.53, 50_000, 0.05, 1000, rule=t_rule, min_taken=MIN_READ_EVENTS)["decision"] \
        == "continue"
    assert reading_verdict(0.56, 0.53, 50_000, 0.05, 1)["decision"] == "continue"      # default rule: unchanged
    assert reading_rule("group") == json.loads(GOLDEN.read_text())["reading_rule"]["group"]


def test_run_discovery_under_the_tercile_rule_applies_the_1000_trade_floor(monkeypatch: pytest.MonkeyPatch):
    from goldbot.research import discovery
    seen: dict[str, Any] = {}
    real = discovery.reading_verdict

    def spy(*a: Any, **k: Any) -> dict[str, Any]:
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(discovery, "reading_verdict", spy)
    _discover_with("tercile_inner")
    assert seen["min_taken"] == discovery.MIN_READ_EVENTS
    _discover_with("break_even_margin")
    assert seen["min_taken"] == 1


def test_q_is_part_of_the_config_hash_and_of_the_reading_rule():
    """Quant R1 fix 2: q and the quantile method are in the tercile dump (so in the hash), and the clause is built
    from them; the default rule is dropped from the dump only when it is literally "break_even_margin"."""
    from goldbot.research.discovery import DiscoveryConfig, reading_rule
    from goldbot.research.registry import config_hash
    assert pipeline.DEFAULT_THRESHOLD_RULE == "break_even_margin"
    t = DiscoveryConfig(threshold_rule="tercile_inner")
    d = t.model_dump()
    assert d["tercile_q"] == 2 / 3 and d["tercile_method"] == "linear"
    other = DiscoveryConfig(threshold_rule="tercile_inner", tercile_q=0.75)
    assert config_hash({"discovery": other.model_dump()}) != config_hash({"discovery": d})
    assert "the 3/4 quantile (linear)" in reading_rule("feature", "tercile_inner", tercile_q=0.75)
    assert "the 2/3 quantile (linear)" in reading_rule("feature", "tercile_inner")
    assert not {"threshold_rule", "tercile_q", "tercile_method"} & set(DiscoveryConfig().model_dump())
    with pytest.raises(ValueError, match="tercile_q"):
        DiscoveryConfig(tercile_q=0.75)                     # a q under the break-even rule would be silently ignored
    with pytest.raises(ValueError):
        DiscoveryConfig(threshold_rule="tercile_inner", tercile_q=1.0)


def test_a_tercile_q_other_than_two_thirds_reaches_the_cut(stub_model: None):
    labels, gross, feats = _wf_frames()
    folds = list(walk_forward_splits(labels, **WINDOW))
    sel = _walk(labels, gross, feats, folds, threshold_rule="tercile_inner", tercile_q=0.75).metrics["selection"]
    p = _walk(labels, gross, feats, folds).oof["p_raw"].to_numpy()
    assert sel["tercile_q"] == 0.75
    assert sel["fold_cuts"][2] == float(np.quantile(p[np.intersect1d(np.concatenate([folds[0].test_idx,
                                                                                      folds[1].test_idx]),
                                                                       folds[2].train_idx)], 0.75, method="linear"))


def test_a_tercile_discovery_with_overlapping_test_windows_is_refused_before_any_registry_row(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Quant R1 fix 3: step_months < test_months gives overlapping test folds; under the tercile rule the run is
    refused before the budget check, the pre-registration or any look (the default rule is not affected)."""
    from goldbot.research.discovery import DiscoveryConfig, check_threshold_config, discovery_pass
    from goldbot.research.registry import TrialRegistry
    from goldbot.specialists import SPECIALISTS
    with pytest.raises(ValueError, match="step_months"):
        check_threshold_config(DiscoveryConfig(threshold_rule="tercile_inner"),
                               {"test_months": 3, "step_months": 1})
    check_threshold_config(DiscoveryConfig(), {"test_months": 3, "step_months": 1})
    check_threshold_config(DiscoveryConfig(threshold_rule="tercile_inner"), {"test_months": 3, "step_months": 3})
    spec = SPECIALISTS["tsmom"](timeframe="4h", max_bars=12)
    monkeypatch.setattr(type(spec), "walkforward", {"step_months": 1})
    reg = TrialRegistry(tmp_path / "r.jsonl")
    b1 = pd.DataFrame({"ts_utc": pd.to_datetime(["2010-01-03 23:00:00", "2025-09-30 20:59:00"], utc=True)})
    with pytest.raises(ValueError, match="step_months"):
        discovery_pass(spec, b1, reg, budget_cap=20, reserved_setting=0,
                       cfg=DiscoveryConfig(threshold_rule="tercile_inner"))
    assert not (tmp_path / "r.jsonl").exists() or not (tmp_path / "r.jsonl").read_text()

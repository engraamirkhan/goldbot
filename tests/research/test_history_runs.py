"""Run records of SEQ-1, its embedding and RL-X1 in the one format of record (goldbot/research/history_runs.py,
run_manifest, ADR 0106 7 / ADR 0109): designs round-trip without pickles, every fit goes through a builder,
`run_manifest.rebuild` reproduces the artifact sha from a clean checkout, the GBT comparison and seed stability
become the bundle's verdicts, and the embedding and RL-X1 never pass the serving rule."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import holdout_guard as HG
from goldbot.research import history_runs as HR
from goldbot.research import model_quality as MQ
from goldbot.research import run_manifest as RM
from goldbot.research import seq1 as S
from goldbot.research import seq1_embed as E
from tests.research.test_rl_exits import FREE, _world
from tests.research.test_seq1 import _bars, _labels, needs_torch
from tests.test_run_manifest import git_repo

DV = "dl-v1:tiers-" + "f" * 52
END = pd.Timestamp("2014-06-30", tz="UTC")
KEY = "unit:XAUUSD-1h|fold:2014-06-30"


def _design() -> S.Seq1Data:
    bars = _bars("2012-01-02", 700)
    return S.design(_labels(bars, "1h"), {("XAUUSD", "1h"): bars})


def test_a_design_round_trips_through_its_npz_without_pickles(tmp_path: Path):
    data = _design()
    back = HR.load_design(HR.save_design(data, tmp_path / "d.npz"))
    np.testing.assert_array_equal(back.X, data.X)
    np.testing.assert_array_equal(back.y, data.y)
    pd.testing.assert_frame_equal(back.keys, data.keys[list(back.keys.columns)], check_dtype=False)
    assert HR.design_sha(back) == HR.design_sha(data)


def test_episodes_round_trip_through_their_npz(tmp_path: Path):
    eps = _world(20, 1, reverting=True)
    back = HR.load_episodes(HR.save_episodes(eps, tmp_path / "e.npz"))
    assert len(back) == 20
    for a, b in zip(eps, back, strict=True):
        np.testing.assert_array_equal(a.close_r, b.close_r)
        assert (a.k, a.day, a.weight) == (b.k, b.day, b.weight)


def test_a_design_whose_rows_reach_the_holdout_is_refused_at_load(tmp_path: Path):
    data = _design()
    keys = data.keys.copy()
    keys.loc[keys.index[-1], "horizon_end_utc"] = HG.HOLDOUT_START + pd.Timedelta(hours=1)
    path = HR.save_design(data.model_copy(update={"keys": keys}), tmp_path / "d.npz")
    with pytest.raises(HG.HoldoutSealed):
        HR.load_design(path)


def test_episodes_on_a_holdout_session_day_are_refused_at_load(tmp_path: Path):
    eps = _world(3, 1, reverting=True)
    eps = [*eps[:-1], eps[-1].model_copy(update={"day": str(HG.HOLDOUT_START.date())})]
    path = HR.save_episodes(eps, tmp_path / "e.npz")
    with pytest.raises(HG.HoldoutSealed):
        HR.load_episodes(path)


def test_rlx1_records_a_family_d_manifest_that_rebuilds_exactly_and_never_serves(tmp_path: Path):
    repo = git_repo(tmp_path / "repo")
    eps = _world(60, 2, reverting=True)
    pol, m = HR.record_rlx1(eps, manifest_key=KEY, train_end=END, data_root=tmp_path / "data", data_version=DV,
                            root=tmp_path / "manifests", costs=FREE, repo=repo)
    assert m.family == "D" and m.model == "rlx1:fqi" and m.builder == "goldbot.research.history_runs:build_rlx1"
    assert m.seeds == MQ.seed_list(KEY, 1) == [pol.spec.seed] and m.served == "seed0" and m.label_end_max == END
    assert m.artifact_sha256 == RM.sha256_bytes(pol.model_dump_json().encode()) and pol.live is False
    assert RM.rebuild(m, repo=repo, data_root=tmp_path / "data") == m.artifact_sha256
    with pytest.raises(MQ.ModelRefused):
        MQ.require_servable(m, lookup=lambda i: RM.load(i, tmp_path / "manifests"))


@needs_torch
def test_seq1_records_its_seed_bundle_and_the_bundle_rebuilds_exactly(tmp_path: Path):
    repo = git_repo(tmp_path / "repo")
    spec = S.SPEC.model_copy(update={"max_epochs": 1, "batch": 256, "threads": 1})
    bundle, m = HR.record_seq1(_design(), manifest_key=KEY, data_root=tmp_path / "data", data_version=DV,
                               root=tmp_path / "manifests", spec=spec, repo=repo)
    assert m.family == "A" and m.seeds == MQ.seed_list(KEY) == list(bundle.seeds) and m.served == "ensemble"
    assert list(HR.seq1_spec(KEY, spec).seeds) == m.seeds       # the walk-forward read fits the manifest's seeds
    assert m.scaler_fit_end <= m.train_end and m.label_end_max == m.train_end
    assert RM.rebuild(m, repo=repo, data_root=tmp_path / "data") == m.artifact_sha256
    np.testing.assert_allclose(S.score_bundle(bundle, _design().X[:5]).shape, (5, 2))


@needs_torch
def test_the_embedding_records_one_seed_rebuilds_exactly_and_is_never_served_alone(tmp_path: Path):
    repo = git_repo(tmp_path / "repo")
    bars = _bars("2012-01-02", 700)
    data = E.rows({("XAUUSD", "1h"): bars["visible_at"].iloc[20:670]}, {("XAUUSD", "1h"): bars})
    fast = E.SPEC.model_copy(update={"base": S.SPEC.model_copy(update={"max_epochs": 1, "batch": 256,
                                                                       "threads": 1})})
    art, m = HR.record_seq1_embed(data, manifest_key=KEY, data_root=tmp_path / "data", data_version=DV,
                                  root=tmp_path / "mf", spec=fast, repo=repo)
    assert art.outcome_free and m.seeds == [art.seed] == MQ.seed_list(KEY, 1) and m.quality == []
    assert m.served == "seed0" and HR.embed_spec(KEY, fast).base.seeds[0] == art.seed
    assert RM.rebuild(m, repo=repo, data_root=tmp_path / "data") == m.artifact_sha256
    with pytest.raises(MQ.ModelRefused):
        MQ.require_servable(m, lookup=lambda i: RM.load(i, tmp_path / "mf"))


def test_seq1_quality_reads_seed_stability_and_the_gbt_comparison_on_the_same_rows():
    n = 400
    rng = np.random.default_rng(0)
    rows = pd.DataFrame({"net_r": rng.normal(0.2, 1.0, n),
                         "day": pd.date_range("2016-01-04", periods=n, freq="6h", tz="UTC").normalize()})
    keep = rows["net_r"].to_numpy() > 0                       # an oracle keep beats a GBT that keeps nothing
    s_by = (0.06, 0.07, 0.05, 0.08, 0.06)
    seeds = HR.seq1_spec(KEY).seeds
    rd = S.Seq1Read(spec_id=S.SPEC.spec_id, seeds=seeds, shas=("a" * 64,) * 5, s_by_seed=s_by,
                    seed=S.seed_check(s_by), verdict="READ", s_mean=float(np.mean(s_by)), keep_share=0.5, per_tf={},
                    keep_ensemble=keep, reads=())
    folds = dict(horizon_days=np.full(n, 1.0), train_end=END, fold_keys=["f0", "f1"], trial=1)
    seeds_v, gbt_v = HR.seq1_quality(rd, rows, gbt_net=np.zeros(n), gbt_manifest_id="c" * 64, **folds)
    assert seeds_v.check == "seed_stability" and seeds_v.passed and seeds_v.detail["seeds"] == list(seeds)
    assert gbt_v.check == "beats_baseline" and gbt_v.passed and gbt_v.detail["baseline_manifest_id"] == "c" * 64
    assert seeds_v.detail["rows_key"] == gbt_v.detail["rows_key"]          # Q3: one set of OOF rows
    assert gbt_v.detail["train_end"] == END.isoformat() and gbt_v.detail["fold_keys"] == ["f0", "f1"]
    rd_bad = rd.model_copy(update={"s_by_seed": (0.06, -0.01, 0.05, 0.08, 0.06), "keep_ensemble": ~keep})
    s2, g2 = HR.seq1_quality(rd_bad, rows, gbt_net=np.zeros(n), gbt_manifest_id="c" * 64, **folds)
    assert not s2.passed and not g2.passed

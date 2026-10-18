"""BACKLOG 82 (H-02/H-07): a pre-registered column without full coverage in a fold is NaN in that fold's train and test.

Design rule (preregistration-2027Q1.md, H-02 amendment 2026-10-17): "H-07 is a true VIX-only cluster in any fold whose
training window lacks full SP500 coverage. In such a fold, `macro_spx_dd20` is set to NaN in both the training and
the test rows ... Full coverage means the column is finite on every training row of the fold. Masking is per fold
and uses only the fold's own training rows' coverage, never the test rows." Fully covered folds are untouched, and a
config without `full_coverage_columns` (the default) dumps, hashes and runs exactly as before."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pandas as pd
import pytest

from goldbot.research import discovery, pipeline
from goldbot.research.discovery import DiscoveryConfig, run_discovery
from goldbot.research.registry import config_hash
from goldbot.research.walkforward import Fold, walk_forward_splits
from tests.test_tercile_cut import WINDOW, StubModel, _wf_frames

SPX = "macro_spx_dd20"
SENTINEL: Any = object()
SPX_FROM = pd.Timestamp("2021-09-01", tz="UTC")       # the column is NaN before this (FRED's SP500 window, in small)


class RecordingModel(StubModel):
    """StubModel that keeps a copy of every frame it is fitted on and asked to score, in call order."""
    fitted: ClassVar[list[pd.DataFrame]] = []
    scored: ClassVar[list[pd.DataFrame]] = []

    def fit(self, X: pd.DataFrame, y: pd.Series, w: pd.Series | None = None) -> RecordingModel:
        RecordingModel.fitted.append(X.copy())
        super().fit(X, y, w)
        return self

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        RecordingModel.scored.append(X.copy())
        return super().predict_raw(X)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> type[RecordingModel]:
    RecordingModel.fitted, RecordingModel.scored = [], []
    monkeypatch.setattr(pipeline, "MetaLabelModel", RecordingModel)
    return RecordingModel


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[Fold]]:
    """`_wf_frames` plus `macro_spx_dd20`: NaN before SPX_FROM, finite after, so the early folds' rolling training
    windows only partly cover it and the later ones fully."""
    labels, gross, feats = _wf_frames()
    rng = np.random.default_rng(3)
    spx = rng.normal(size=len(labels))
    spx[np.asarray(labels["ts_utc"] < SPX_FROM)] = np.nan
    feats[SPX] = spx
    return labels, gross, feats, list(walk_forward_splits(labels, **WINDOW))


def _covered(feats: pd.DataFrame, f: Fold) -> bool:
    return bool(np.isfinite(feats[SPX].to_numpy(dtype=float)[f.train_idx]).all())


def _walk(labels: pd.DataFrame, gross: pd.DataFrame, feats: pd.DataFrame, folds: list[Fold],
          **kw: Any) -> pipeline.ResearchResult:
    return pipeline._walk_forward("stub", labels, gross, feats, ["side", "x", "z", SPX], folds, "f-test", n_trials=1,
                                  trades_per_year=None, extra_cost_usd=0.0, holdout=None, score_holdout=False,
                                  window=WINDOW, timeframe=None, **kw)


# ---------------------------------------------------------------------------------------------- the rule
def test_spx_dd20_is_nan_in_train_and_test_for_folds_without_full_coverage(recording: type[RecordingModel]):
    labels, gross, feats, folds = _frames()
    covered = [_covered(feats, f) for f in folds]
    assert any(covered) and not all(covered)                      # both kinds of fold are exercised
    # an uncovered fold whose own test rows are all finite: the test rows do not rescue it
    assert any(not c and np.isfinite(feats[SPX].to_numpy()[f.test_idx]).all() for f, c in zip(folds, covered))
    # a covered fold with NaN on some test rows: the test rows do not spoil it, and they pass through as they are
    # (the last fold: its test rows are no later fold's training rows)
    k_cov = len(folds) - 1
    assert covered[k_cov]
    holes = folds[k_cov].test_idx[::7]
    feats.loc[holes, SPX] = np.nan
    before = feats.copy()

    res = _walk(labels, gross, feats, folds, full_coverage_columns=(SPX,))

    assert len(recording.fitted) == len(recording.scored) == len(folds)
    for f, c, tr, te in zip(folds, covered, recording.fitted, recording.scored):
        if c:                                                     # fully covered: untouched, train and test
            pd.testing.assert_frame_equal(tr, feats.iloc[f.train_idx])
            pd.testing.assert_frame_equal(te, feats.iloc[f.test_idx])
        else:                                                     # NaN in both, every other column untouched
            assert tr[SPX].isna().all() and te[SPX].isna().all()
            pd.testing.assert_frame_equal(tr.drop(columns=SPX), feats.iloc[f.train_idx].drop(columns=SPX))
            pd.testing.assert_frame_equal(te.drop(columns=SPX), feats.iloc[f.test_idx].drop(columns=SPX))
    assert recording.scored[k_cov][SPX].isna().sum() == len(holes)
    pd.testing.assert_frame_equal(feats, before)                  # the caller's frame is never mutated
    assert res.metrics["full_coverage"] == {
        "columns": [SPX], "masked": {str(f.k): ([] if c else [SPX]) for f, c in zip(folds, covered)}}


def test_the_mask_reads_only_the_folds_training_rows_never_its_test_rows():
    """Whatever the test rows hold (all NaN, all finite, anything), each fold's masked set is the same."""
    _, _, feats, folds = _frames()
    want = [pipeline.uncovered_columns(feats, f.train_idx, (SPX,)) for f in folds]
    rng = np.random.default_rng(11)
    for fill in (np.nan, 1.0):
        for f, w in zip(folds, want):
            g = feats.copy()
            g.loc[f.test_idx, SPX] = fill
            g.loc[f.test_idx, "x"] = rng.normal(size=len(f.test_idx))
            assert pipeline.uncovered_columns(g, f.train_idx, (SPX,)) == w
    # one NaN (or inf) training row is enough to lose full coverage
    f = next(f for f in folds if not want[folds.index(f)] and len(f.train_idx))
    for bad in (np.nan, np.inf):
        g = feats.copy()
        g.loc[f.train_idx[len(f.train_idx) // 2], SPX] = bad
        assert pipeline.uncovered_columns(g, f.train_idx, (SPX,)) == [SPX]


def test_a_named_column_missing_from_the_frame_is_refused():
    _, _, feats, folds = _frames()
    with pytest.raises(ValueError, match="macro_spx_typo"):
        pipeline.uncovered_columns(feats, folds[0].train_idx, ("macro_spx_typo",))


def test_masking_is_deterministic(recording: type[RecordingModel]):
    labels, gross, feats, folds = _frames()
    a = _walk(labels, gross, feats, folds, full_coverage_columns=(SPX,))
    b = _walk(labels, gross, feats, folds, full_coverage_columns=(SPX,))
    pd.testing.assert_frame_equal(a.oof, b.oof)
    assert json.dumps(a.metrics, default=str) == json.dumps(b.metrics, default=str)


def test_a_fully_covered_column_or_no_column_leaves_the_walk_forward_byte_identical(recording: type[RecordingModel]):
    labels, gross, feats, folds = _frames()
    base = _walk(labels, gross, feats, folds)
    assert "full_coverage" not in base.metrics                    # the default metrics carry no new key
    same = _walk(labels, gross, feats, folds, full_coverage_columns=("x",))      # x is finite everywhere
    pd.testing.assert_frame_equal(base.oof, same.oof)
    assert same.metrics.pop("full_coverage") == {"columns": ["x"], "masked": {str(f.k): [] for f in folds}}
    assert json.dumps(base.metrics, default=str) == json.dumps(same.metrics, default=str)


# ---------------------------------------------------------------------------------------------- discovery
def test_discovery_selection_sees_the_masked_column_on_uncovered_folds_only(monkeypatch: pytest.MonkeyPatch):
    labels, gross, feats, folds = _frames()
    seen: dict[int, pd.Series] = {}
    real = discovery.select_in_fold

    def spy(f_: pd.DataFrame, lab: pd.DataFrame, fold: Fold, *a: Any, **k: Any) -> Any:
        seen[fold.k] = f_[SPX].iloc[fold.train_idx].copy()
        return real(f_, lab, fold, *a, **k)
    monkeypatch.setattr(discovery, "select_in_fold", spy)
    cfg = DiscoveryConfig(n_subsamples=2, top_k=2, freq_threshold=0.5, stability_threshold=0.5,
                          full_coverage_columns=(SPX,))
    d = run_discovery("synthetic-82", labels, gross, feats, folds, "f-test", WINDOW, ["x", "z", SPX], cfg)
    for f in folds:
        if _covered(feats, f):
            pd.testing.assert_series_equal(seen[f.k], feats[SPX].iloc[f.train_idx])
        else:
            assert seen[f.k].isna().all()
            assert SPX not in next(s for s in d.folds if s.k == f.k).selected
    assert d.result.metrics["discovery"]["config"]["full_coverage_columns"] == (SPX,)
    assert d.result.metrics["full_coverage"]["masked"] == {
        str(f.k): ([] if _covered(feats, f) else [SPX]) for f in folds}


def test_a_discovery_naming_a_column_the_frame_lacks_is_refused_before_any_selection(
        monkeypatch: pytest.MonkeyPatch):
    labels, gross, feats, folds = _frames()
    monkeypatch.setattr(discovery, "select_in_fold", lambda *a, **k: pytest.fail("selection ran"))
    cfg = DiscoveryConfig(n_subsamples=2, full_coverage_columns=("macro_spx_typo",))
    with pytest.raises(ValueError, match="macro_spx_typo"):
        run_discovery("synthetic-82", labels, gross, feats, folds, "f-test", WINDOW, ["x", "z"], cfg)


def test_the_default_discovery_config_dumps_and_hashes_as_before_and_the_field_is_hashed_when_set():
    default = DiscoveryConfig()
    assert "full_coverage_columns" not in default.model_dump()
    assert DiscoveryConfig(full_coverage_columns=()).model_dump() == default.model_dump()
    pinned = DiscoveryConfig(full_coverage_columns=[SPX])                  # type: ignore[arg-type]  # yaml gives a list
    dump = pinned.model_dump()
    assert dump["full_coverage_columns"] == (SPX,) and DiscoveryConfig(**json.loads(json.dumps(dump))) == pinned
    assert config_hash({"discovery": dump}) != config_hash({"discovery": default.model_dump()})


# ---------------------------------------------------------------------------------------------- BACKLOG 88
def _coverage_feats(n: int = 100, share: float = 0.57) -> pd.DataFrame:
    """`side`, a fully populated column and two columns finite on only `share` of the candidates."""
    k = int(n * share)
    part = np.r_[np.full(n - k, np.nan), np.ones(k)]
    return pd.DataFrame({"side": 1.0, "x": 1.0, SPX: part, "macro_other_57": part})


def test_discovery_pool_keeps_a_named_column_at_57_percent_coverage_and_drops_an_unnamed_one():
    """Pre-registration pin 2026-10-17 (item 2): columns named in `full_coverage_columns` are exempt from the 80%
    pool rule; every other column keeps the 80% rule."""
    prep: Any = SimpleNamespace(feats=_coverage_feats())
    pool = discovery.discovery_pool(prep, (SPX,))
    assert SPX in pool and "macro_other_57" not in pool and "x" in pool and "side" not in pool
    assert SPX not in discovery.discovery_pool(prep)            # no field, no exemption: the 80% rule as before
    assert pipeline.eligible_columns(prep.feats) == ["side", "x"]


def test_discover_builds_its_pool_with_the_configs_full_coverage_columns(monkeypatch: pytest.MonkeyPatch):
    labels, gross, feats, _ = _frames()
    spec = SimpleNamespace(agent_id="a", timeframe="4h", walkforward={})
    prep: Any = SimpleNamespace(feats=feats, labels=labels, gross=gross, n_bars=len(labels), swap=None,
                                feature_version="f-test", spec=spec)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(discovery, "uniqueness_weights", lambda *a, **k: pd.Series(1.0, index=labels.index))
    monkeypatch.setattr(discovery, "splits_for", lambda *a, **k: [])
    monkeypatch.setattr(discovery, "window_for", lambda *a, **k: WINDOW)
    monkeypatch.setattr(discovery, "discovery_pool", lambda p, cover=(): seen.update(cover=cover) or [])
    monkeypatch.setattr(discovery, "run_discovery", lambda *a, **k: SENTINEL)
    assert discovery.discover(prep, DiscoveryConfig(full_coverage_columns=(SPX,))) is SENTINEL
    assert seen["cover"] == (SPX,)


def test_run_specialist_passes_full_coverage_columns_to_evaluate(monkeypatch: pytest.MonkeyPatch):
    """Pin item 3: survivor trials run with the same per-fold mask, so `run_specialist` forwards the field."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(pipeline, "prepare", lambda *a, **k: "prep")
    monkeypatch.setattr(pipeline, "evaluate", lambda prep, **k: seen.update(k) or SENTINEL)
    out = pipeline.run_specialist(None, None, full_coverage_columns=(SPX,))     # type: ignore[arg-type]
    assert out is SENTINEL and seen["full_coverage_columns"] == (SPX,)
    pipeline.run_specialist(None, None)                                          # type: ignore[arg-type]
    assert seen["full_coverage_columns"] == ()


def test_evaluate_hands_full_coverage_columns_to_the_walk_forward(monkeypatch: pytest.MonkeyPatch):
    from goldbot.specialists import SPECIALISTS
    labels, gross, feats, _ = _frames()
    prep = pipeline.Prepared(spec=SPECIALISTS["tsmom"](), labels=labels, gross=gross, feats=feats,
                             feature_version="f-test", n_bars=len(labels))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(pipeline, "_walk_forward", lambda *a, **k: seen.update(k) or SENTINEL)
    monkeypatch.setattr(pipeline, "uniqueness_weights", lambda *a, **k: pd.Series(1.0, index=labels.index))
    monkeypatch.setattr(pipeline, "splits_for", lambda *a, **k: [])
    assert pipeline.evaluate(prep, model_features=["side", "x"], full_coverage_columns=(SPX,)) is SENTINEL
    assert seen["full_coverage_columns"] == (SPX,)
    pipeline.evaluate(prep, model_features=["side", "x"])
    assert seen["full_coverage_columns"] == ()


def test_a_survivor_trial_takes_its_full_coverage_columns_from_the_parent_discovery_row():
    """Pin item 3: a survivor trial (config tagged `from_discovery`) runs with the parent discovery's mask; any other
    config (no tag, a parent without the field, an unknown parent) runs with none."""
    from goldbot.research.director import survivor_full_coverage
    parent = {"ts": "2027-01-05T00:00:00+00:00", "config_hash": "abc", "trial": 21, "status": "discovery",
              "config": {"discovery": DiscoveryConfig(full_coverage_columns=(SPX,)).model_dump()}}
    plain = {"ts": "2027-01-06T00:00:00+00:00", "config_hash": "def", "trial": 22, "status": "discovery",
             "config": {"discovery": DiscoveryConfig().model_dump()}}
    ref = {"ts": parent["ts"], "config_hash": "abc", "trial": 21}
    rows = [parent, plain]
    assert survivor_full_coverage(rows, {"from_discovery": ref, "survivor": "H-07"}) == (SPX,)
    assert survivor_full_coverage(rows, {"from_discovery": {**ref, "ts": plain["ts"], "config_hash": "def"}}) == ()
    assert survivor_full_coverage(rows, {"survivor": "H-07"}) == ()
    assert survivor_full_coverage(rows, {"from_discovery": {**ref, "config_hash": "zzz"}}) == ()

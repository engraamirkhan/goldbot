"""Shadow scoring of the exploration candidates by every frozen self-retrain model (goldbot/engine/shadow_scoring.py;
lifecycle plan W1, ADR 0101: "Every candidate gets the scores of every frozen model ..., logged with model_version
... No gating"). Synthetic engine (tests/test_exploration_engine.py); models written into aux.db `self_retrain_models`
the way the weekly job writes them, with their provenance."""
from __future__ import annotations

import ast
import functools
import hashlib
import json
import math
import re
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import exploration, long_bars, shadow_scoring
from goldbot.engine.exploration import Pending
from goldbot.research import feature_snapshots as fs
from goldbot.research import self_retrain as sr
from tests.test_exploration_engine import CLOSE, TICKS, _actions, _arm, _at, _engine, _sent
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def _quick_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's GIL courtesy (DEFER_S after queueing, one job per pass end or IDLE_S) shortened for the tests;
    test_the_worker_waits_out_the_deferral_and_every_pass checks the real rule."""
    monkeypatch.setattr(shadow_scoring, "DEFER_S", 0.0)
    monkeypatch.setattr(shadow_scoring, "IDLE_S", 0.005)
CUTOFF = pd.Timestamp("2025-02-28 21:00", tz="UTC")           # before CLOSE: CLOSE's candidates are out of sample


def _model(p: float = 0.5, tau: float = 0.5, inputs: tuple[str, ...] = sr.INPUTS,
           coef: tuple[float, ...] | None = None) -> sr.LogitModel:
    """A filter whose score is `p` for every vector (zero weights) unless `coef` is given."""
    n = len(inputs)
    return sr.LogitModel(inputs=inputs, medians=(0.0,) * n, means=(0.0,) * n, scales=(1.0,) * n,
                         coef=coef if coef is not None else (0.0,) * n, intercept=math.log(p / (1 - p)), tau=tau)


@functools.cache
def _feature_version() -> str:
    """The feature version of the test engine's vectors (what a trainer on those snapshots records), built from the
    synthetic minutes before CLOSE themselves, never from an engine: a test whose engine has no bars yet (or that
    runs first on its xdist worker) gets the same version as every other test."""
    snap = fs.decision_features(ticks_to_1m(TICKS[TICKS["ts_utc"] < CLOSE]), "15m", CLOSE)
    assert snap is not None
    return snap.feature_version


def _store(tmp_path: Path, arm_id: str, model: sr.LogitModel, *, status: str = "shadow", broker: str = "icm",
           cutoff: pd.Timestamp = CUTOFF, version: str | None = None, spec: str = sr.SPEC_VERSION,
           feature_versions: list[str] | None = None, sha: str | None = "auto", text: str | None = None) -> str:
    v = version or f"{spec}-{broker}-{cutoff:%Y%m%d}-{arm_id}"
    body = text if text is not None else model.model_dump_json()
    with exploration.ledger_db(str(tmp_path)).write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, status, "
            "status_reason, gate_pass, cutoff_ns, activation_ns, n_train, n_features, n_weights, model, oos, "
            "recorded_ns, status_ns, model_sha256, feature_versions) "
            "VALUES (?, 'run', ?, ?, ?, ?, 'test', 0, ?, ?, 300, 40, 41, ?, '{}', 0, 0, ?, ?)",
            (v, broker, arm_id, spec, status, int(cutoff.value), int((cutoff + pd.Timedelta(days=2)).value), body,
             sr.model_sha256(model.model_dump_json()) if sha == "auto" else sha,
             json.dumps(feature_versions if feature_versions is not None else [_feature_version()])))
    return v


def _scoring(eng: Any, on: bool = True) -> Any:
    eng.cfg.shadow_score = on
    return eng


def _drain(eng: Any) -> None:
    sc = eng.exploration.shadow_scorer if eng.exploration is not None else None
    if sc is not None:
        assert sc.drain(60.0)


def _rows(tmp_path: Path, table: str, order: str) -> list[dict[str, Any]]:
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]


def _obs(tmp_path: Path) -> list[dict[str, Any]]:
    return _rows(tmp_path, "exploration_obs", "arm_id, decision_ns")


def _scores(tmp_path: Path) -> list[dict[str, Any]]:
    return _rows(tmp_path, "exploration_model_scores", "candidate_id, model_version")


def _pass(tmp_path: Path, arms: list[Any], *, on: bool) -> tuple[Any, Any]:
    eng, pb = _engine(tmp_path, arms)
    tick = _at(eng, pb, CLOSE)
    _scoring(eng, on)
    exploration.on_pass(eng, tick)
    _drain(eng)
    return eng, pb


# ------------------------------------------------------------------------------------------------ what is logged
def test_every_candidate_of_an_arm_with_frozen_models_gets_each_models_score_sha_and_feature_version(tmp_path):
    """Every active or shadow model of the arm scores it: one append-only row per (candidate, model) with the model's
    sha256 and spec, the vector's feature version and sha256; the candidate row carries the primary score."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b")])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    old = _store(tmp_path, "a", _model(0.3), cutoff=CUTOFF - pd.Timedelta(days=7))
    new = _store(tmp_path, "a", _model(0.6))
    exploration.on_pass(eng, tick)
    _drain(eng)
    obs = {o["arm_id"]: o for o in _obs(tmp_path)}
    got = _scores(tmp_path)
    assert {(s["model_version"], round(s["score"], 6), s["is_primary"]) for s in got} == {
        (old, 0.3, 0), (new, 0.6, 1)}                                    # the newest is primary (none active)
    assert all(s["candidate_id"] == obs["a"]["candidate_id"] for s in got)
    assert all(s["model_sha256"] == sr.model_sha256(_model(0.3 if s["model_version"] == old else 0.6)
                                                    .model_dump_json()) for s in got)
    assert all(s["spec_version"] == sr.SPEC_VERSION and s["feature_version"] == _feature_version() for s in got)
    assert obs["a"]["meta_p"] == pytest.approx(0.6) and obs["a"]["model_version"] == new
    assert obs["a"]["feature_version"] == _feature_version()
    assert obs["b"]["meta_p"] is None and obs["b"]["model_version"] is None        # an arm without a model


def test_the_active_model_is_primary_on_the_candidate_over_a_newer_shadow(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    act = _store(tmp_path, "a", _model(0.4), status="active", cutoff=CUTOFF - pd.Timedelta(days=7))
    _store(tmp_path, "a", _model(0.7))
    exploration.on_pass(eng, tick)
    _drain(eng)
    [obs] = _obs(tmp_path)
    assert obs["model_version"] == act and obs["meta_p"] == pytest.approx(0.4)


def test_the_logged_score_is_the_trainers_model_on_the_nightly_snapshots_vector(tmp_path):
    """Parity: the nightly snapshot's own function at the candidate's decision, through the trainer's `design`
    (signed columns, side) and the stored model; the vector's sha256 is the one the nightly job would store."""
    eng, pb = _engine(tmp_path, [_arm("a", side=-1)])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    weights = tuple(0.05 * ((i % 7) - 3) for i in range(len(sr.INPUTS)))
    m = _model(0.5, coef=weights)
    _store(tmp_path, "a", m)
    exploration.on_pass(eng, tick)
    _drain(eng)
    [row] = _scores(tmp_path)
    [obs] = _obs(tmp_path)
    decision = pd.Timestamp(int(obs["decision_ns"]), tz="UTC")
    assert decision == long_bars.origin("15m", CLOSE, None)
    snap = fs.decision_features(eng.bars_1m, "15m", decision)
    assert snap is not None and snap.available_utc_max <= decision            # point in time
    want = float(m.score(sr.design(pd.DataFrame([{**snap.features, "side": -1.0}])))[0])
    assert obs["side"] == -1
    assert row["score"] == pytest.approx(want, rel=1e-12) and obs["meta_p"] == pytest.approx(want, rel=1e-12)
    assert row["vector_sha256"] == fs.vector_sha256(snap)


def test_the_snapshot_vector_hash_is_unchanged_by_the_refactor():
    snap = fs.Snapshot(features={"b": 1.0, "a": None}, feature_version="fv1",
                       available_utc_max=pd.Timestamp("2025-01-01", tz="UTC"))
    body = json.dumps(snap.features, sort_keys=True)
    assert fs.vector_sha256(snap) == hashlib.sha256(f"fv1|{body}".encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ no decision changes
def _strip(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in ("ts",)}


def test_entries_refusals_gate_calls_and_records_are_identical_with_and_without_models(tmp_path):
    """The proof that nothing gates: one engine scoring with models that would drop every candidate (score 0.01 under
    a cut of 0.5, ACTIVE and shadow), one with scoring off and no models. Same orders, refusals, journal, gate calls,
    positions and candidate records (bar the three score columns)."""
    arms = [_arm("a"), _arm("b", side=-1), _arm("c", control_share=1.0), _arm("d", tf="1h")]

    def run(path: Path, with_models: bool) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        path.mkdir()
        eng, pb = _engine(path, arms)
        tick = _at(eng, pb, CLOSE)
        _scoring(eng, with_models)
        if with_models:
            for a in ("a", "b", "c", "d"):
                _store(path, a, _model(0.01), status="active")
                _store(path, a, _model(0.02), cutoff=CUTOFF - pd.Timedelta(days=7))
        calls: list[Any] = []
        check = eng.gate.check

        def spy(*a: Any, **k: Any) -> Any:
            gd = check(*a, **k)
            calls.append((repr(a[0]).replace(str(path), "<state>"), repr(gd).replace(str(path), "<state>")))
            return gd

        eng.gate.check = spy  # type: ignore[method-assign]
        exploration.on_pass(eng, tick)
        _drain(eng)
        obs = [{k: v for k, v in o.items() if k not in ("meta_p", "model_version", "feature_version", "recorded_ns")}
               for o in _obs(path)]
        pos = sorted((p.side, p.lots, p.sl, p.tp) for p in pb.positions())
        return [_strip(d) for d in eng.decisions], calls, pos, obs

    with_m = run(tmp_path / "with", True)
    without = run(tmp_path / "without", False)
    assert with_m == without
    assert _sent_count(with_m[0]) >= 2
    scored = [o for o in _obs(tmp_path / "with") if o["meta_p"] is not None]
    assert scored and all(o["meta_p"] == pytest.approx(0.01) for o in scored)     # scored, and still sent
    assert not any(o["meta_p"] is not None for o in _obs(tmp_path / "without"))


def _sent_count(decisions: list[dict[str, Any]]) -> int:
    return sum(1 for d in decisions if str(d.get("action", "")).startswith("exploration:"))


def test_scoring_off_queues_nothing_and_starts_no_thread(tmp_path):
    eng, _pb = _pass(tmp_path, [_arm("a")], on=False)
    assert eng.exploration is not None and eng.exploration.shadow_scorer is None
    assert len(_sent(eng)) == 1


def test_shadow_scoring_is_off_in_the_engine_by_default_and_on_in_production_settings():
    from goldbot.config import load_settings
    from goldbot.engine import EngineConfig
    assert EngineConfig(account_id="x", broker_name="icm").shadow_score is False
    assert load_settings().research.self_retrain.shadow_score is True
    text = (ROOT / "goldbot" / "ops" / "services" / "engine.py").read_text()
    assert "shadow_score=settings.research.self_retrain.shadow_score" in text


# ------------------------------------------------------------------------------------------------ the tick's budget
def test_queueing_a_candidate_costs_the_tick_under_5_ms_and_never_builds_a_vector_on_it(tmp_path, monkeypatch, capsys):
    """Target < 5 ms a candidate (brief; tests/test_meta_filter_serve.py's bar). The tick only queues: the vector
    build (0.1-0.5 s) runs on the worker thread, never the caller's."""
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    _store(tmp_path, "a", _model(0.6))
    ex = eng.exploration
    assert ex is not None
    arm = ex.arms["a"]
    import threading
    built_on: list[str] = []
    real = fs.decision_features

    def spy(*a: Any, **k: Any) -> Any:
        built_on.append(threading.current_thread().name)
        return real(*a, **k)

    monkeypatch.setattr(fs, "decision_features", spy)
    s = Pending(arm_id="a", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4)
    shadow_scoring.submit(eng, ex, arm, s, "warm", CLOSE)                # starts the thread
    took: list[float] = []
    for i in range(150):
        t0 = time.perf_counter()
        shadow_scoring.submit(eng, ex, arm, s, f"c{i}", CLOSE)
        took.append((time.perf_counter() - t0) * 1000)
    assert ex.shadow_scorer is not None and ex.shadow_scorer.drain(60.0)
    p99 = sorted(took)[int(0.99 * len(took)) - 1]
    with capsys.disabled():
        print(f"\nshadow scoring on the tick: median {statistics.median(took):.4f} ms, p99 {p99:.4f} ms")
    assert statistics.median(took) < 5.0 and p99 < 5.0
    assert built_on and all(n.startswith("shadow-score-") for n in built_on)
    assert len(built_on) == 1                                          # one vector per (tf, decision), shared
    assert tick is not None


def test_a_full_queue_drops_the_candidate_unscored_counts_it_and_never_blocks(tmp_path, caplog):
    db = exploration.ledger_db(str(tmp_path))
    sc = shadow_scoring.ShadowScorer(db, "icm", queue_max=1, start=False)
    job = shadow_scoring.Job(candidate_id="x", arm_id="a", tf="15m", side=1, decision=CLOSE)
    t0 = time.perf_counter()
    assert sc.submit(job, pd.DataFrame())
    assert not sc.submit(shadow_scoring.Job(**{**job.__dict__, "candidate_id": "y"}), pd.DataFrame())
    assert (time.perf_counter() - t0) < 0.05
    assert sc.counts["dropped"] == 1 and sc.counts["submitted"] == 2
    assert any("shadow scoring full" in r.getMessage() for r in caplog.records)
    assert shadow_scoring.QUEUE_MAX == 200


def test_jobs_of_one_decision_share_one_pinned_frame_and_new_decisions_over_the_cap_are_dropped(tmp_path):
    """F4 / code review LOW of #463: a 1m frame is 5-15 MB; one is held per (timeframe, decision), at most FRAMES_MAX."""
    db = exploration.ledger_db(str(tmp_path))
    sc = shadow_scoring.ShadowScorer(db, "icm", start=False)
    frame = pd.DataFrame({"x": [1]})
    for i in range(5):
        assert sc.submit(shadow_scoring.Job(candidate_id=f"c{i}", arm_id="a", tf="15m", side=1, decision=CLOSE), frame)
    assert len(sc.frames) == 1 and sc.frames[("15m", int(CLOSE.value))][1] == 5
    for k in range(1, shadow_scoring.FRAMES_MAX):
        at = CLOSE + pd.Timedelta(minutes=15 * k)
        assert sc.submit(shadow_scoring.Job(candidate_id=f"d{k}", arm_id="a", tf="15m", side=1, decision=at), frame)
    over = CLOSE + pd.Timedelta(days=1)
    assert not sc.submit(shadow_scoring.Job(candidate_id="z", arm_id="a", tf="15m", side=1, decision=over), frame)
    assert len(sc.frames) == shadow_scoring.FRAMES_MAX and sc.counts["dropped"] == 1


def test_a_job_older_than_one_bar_is_dropped_unscored_and_its_frame_released(tmp_path, monkeypatch):
    db = exploration.ledger_db(str(tmp_path))
    sc = shadow_scoring.ShadowScorer(db, "icm")
    ran: list[str] = []
    monkeypatch.setattr(sc, "run", lambda job: ran.append(job.candidate_id))
    monkeypatch.setattr(shadow_scoring, "_bar_s", lambda tf: -1.0)            # every job already a bar old
    assert sc.submit(shadow_scoring.Job(candidate_id="old", arm_id="a", tf="1m", side=1, decision=CLOSE),
                     pd.DataFrame())
    assert sc.drain(10.0)
    assert ran == [] and sc.counts["expired"] == 1 and sc.frames == {}


def test_the_worker_waits_out_the_deferral_and_every_pass(tmp_path, monkeypatch):
    """F1 of #463: a scoring job runs DEFER_S after it was queued, never while an exploration pass runs, and one per
    pass end (or IDLE_S without a pass)."""
    monkeypatch.setattr(shadow_scoring, "DEFER_S", 0.3)
    monkeypatch.setattr(shadow_scoring, "IDLE_S", 60.0)
    db = exploration.ledger_db(str(tmp_path))
    sc = shadow_scoring.ShadowScorer(db, "icm")
    ran: list[tuple[str, float]] = []
    monkeypatch.setattr(sc, "run", lambda job: ran.append((job.candidate_id, time.monotonic())))
    sc._last_turn = time.monotonic()                     # a job just ran: the next waits for a pass end
    sc.mark_pass(True)
    t0 = time.monotonic()
    for cid in ("a", "b"):
        assert sc.submit(shadow_scoring.Job(candidate_id=cid, arm_id="a", tf="15m", side=1, decision=CLOSE),
                         pd.DataFrame())
    time.sleep(0.6)
    assert ran == []                                     # deferral over, but a pass is running
    sc.mark_pass(False)                                  # one pass end: one job
    time.sleep(0.2)
    assert [c for c, _ in ran] == ["a"] and ran[0][1] - t0 >= 0.3
    sc.mark_pass(True)
    sc.mark_pass(False)                                  # the next pass end: the next job
    assert sc.drain(5.0) and [c for c, _ in ran] == ["a", "b"]


def test_the_models_are_reloaded_after_a_failed_read_not_cached_as_loaded(tmp_path, monkeypatch):
    db = exploration.ledger_db(str(tmp_path))
    sc = shadow_scoring.ShadowScorer(db, "icm", start=False)
    calls: list[int] = []

    def flaky(*_: Any, **__: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("locked")
        return {}, []

    monkeypatch.setattr(shadow_scoring, "load_models", flaky)
    job = shadow_scoring.Job(candidate_id="x", arm_id="a", tf="15m", side=1, decision=CLOSE)
    sc.run(job)
    assert sc.loaded_at is None and sc.counts["failed"] == 1
    sc.run(job)
    assert len(calls) == 2 and sc.loaded_at is not None


# ------------------------------------------------------------------------------------------------ provenance
def test_a_model_whose_stored_json_does_not_match_its_sha256_is_refused_and_named(tmp_path):
    db = exploration.ledger_db(str(tmp_path))
    _store(tmp_path, "a", _model(0.6), text=_model(0.9).model_dump_json())       # tampered after training
    good = _store(tmp_path, "b", _model(0.6))
    got, problems = shadow_scoring.load_models(db, "icm")
    assert set(got) == {"b"} and got["b"][0].model_version == good
    [why] = problems
    assert why.startswith("a ") and "sha256 mismatch" in why


@pytest.mark.parametrize("kw, reason", [
    ({"spec": "sr-v0"}, "spec sr-v0"),
    ({"sha": None}, "no recorded model_sha256"),
    ({"feature_versions": []}, "no recorded training feature version"),
])
def test_a_model_of_another_spec_or_without_recorded_provenance_is_refused(tmp_path, kw, reason):
    db = exploration.ledger_db(str(tmp_path))
    _store(tmp_path, "a", _model(0.6), **kw)
    got, problems = shadow_scoring.load_models(db, "icm")
    assert got == {} and len(problems) == 1 and reason in problems[0]


def test_a_model_with_other_inputs_is_refused_even_with_a_matching_sha(tmp_path):
    db = exploration.ledger_db(str(tmp_path))
    _store(tmp_path, "a", _model(0.6, inputs=sr.INPUTS[:-1]))
    got, problems = shadow_scoring.load_models(db, "icm")
    assert got == {} and "not the frozen" in problems[0]


@pytest.mark.parametrize("status, broker", [("demoted", "icm"), ("superseded", "icm"), ("shadow", "vantage")])
def test_demoted_superseded_and_other_broker_models_never_score(tmp_path, status, broker):
    db = exploration.ledger_db(str(tmp_path))
    _store(tmp_path, "a", _model(0.6), status=status, broker=broker)
    assert shadow_scoring.load_models(db, "icm") == ({}, [])


def _snap(version: str) -> fs.Snapshot:
    return fs.Snapshot(features={c: 0.0 for c in sr.FEATURES}, feature_version=version,
                       available_utc_max=CLOSE)


def _served(version: str, cutoff: pd.Timestamp, fvs: tuple[str, ...] = ("fv1",),
            status: str = "shadow") -> shadow_scoring.ServedModel:
    return shadow_scoring.ServedModel(model_version=version, arm_id="a", status=status, spec_version=sr.SPEC_VERSION,
                                      sha256="s", cutoff_ns=int(cutoff.value),
                                      activation_ns=int((cutoff + pd.Timedelta(days=2)).value),
                                      feature_versions=fvs, model=_model(0.6))


def test_a_model_never_scores_a_candidate_decided_at_or_before_its_data_cutoff():
    """In sample: its training window may hold that very candidate. Only later decisions are scored."""
    m = _served("m1", CLOSE)
    got, skipped = shadow_scoring.score((m,), _snap("fv1"), 1, int(CLOSE.value))
    assert got == [] and "in sample" in skipped[0]
    got, _ = shadow_scoring.score((m,), _snap("fv1"), 1, int(CLOSE.value) + 1)
    assert [g.model_version for g in got] == ["m1"] and got[0].primary


def test_a_vector_of_a_feature_version_the_model_never_trained_on_is_not_scored_by_it():
    a, b = _served("m-a", CUTOFF, fvs=("fv1",)), _served("m-b", CUTOFF, fvs=("fv2",))
    got, skipped = shadow_scoring.score((a, b), _snap("fv2"), 1, int(CLOSE.value))
    assert [g.model_version for g in got] == ["m-b"] and got[0].primary
    assert any("m-a" in s and "fv2" in s for s in skipped)


# ------------------------------------------------------------------------------------------------ failing soft
def test_a_scoring_failure_is_logged_counted_skipped_and_never_reaches_the_engine(tmp_path, monkeypatch, caplog):
    def boom(*_: Any, **__: Any) -> Any:
        raise ZeroDivisionError("bad")

    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    monkeypatch.setattr(fs, "decision_features", boom)
    _store(tmp_path, "a", _model(0.6))
    exploration.on_pass(eng, tick)
    _drain(eng)
    assert len(_sent(eng)) == 1                                        # sent as without scoring
    [obs] = _obs(tmp_path)
    assert obs["meta_p"] is None and obs["taken"] == 1
    assert eng.exploration is not None and eng.exploration.shadow_scorer is not None
    assert eng.exploration.shadow_scorer.counts["failed"] == 1
    assert any("shadow scoring" in r.getMessage() and "ZeroDivisionError" in r.getMessage() for r in caplog.records)
    pub = shadow_scoring.published(eng)["shadow_scoring"]
    assert pub["failed"] == 1 and "ZeroDivisionError" in pub["last_error"]


def test_an_unreadable_model_table_scores_nothing_and_the_engine_goes_on(tmp_path, monkeypatch):
    def unreadable(*_: Any, **__: Any) -> Any:
        raise sqlite3.OperationalError("disk")

    monkeypatch.setattr(shadow_scoring, "load_models", unreadable)
    eng, _pb = _pass(tmp_path, [_arm("a")], on=True)
    assert len(_sent(eng)) == 1
    assert eng.exploration is not None and eng.exploration.shadow_scorer is not None
    assert eng.exploration.shadow_scorer.counts["failed"] == 1


def test_a_queueing_error_is_swallowed_and_the_candidate_is_still_recorded(tmp_path, monkeypatch):
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("no thread")

    monkeypatch.setattr(shadow_scoring, "ShadowScorer", boom)
    eng, _pb = _pass(tmp_path, [_arm("a")], on=True)
    assert len(_sent(eng)) == 1 and len(_obs(tmp_path)) == 1


def test_a_score_never_overwrites_a_value_already_on_the_candidate(tmp_path):
    db = exploration.ledger_db(str(tmp_path))
    exploration_data = __import__("goldbot.research.exploration_data", fromlist=["record_candidate"])
    exploration_data.record_candidate(db, candidate_id="c1", account_id="icm-probe", broker="icm", arm_id="a",
                                      family="f", style="intraday", tf="15m", side=1, decision_utc=CLOSE,
                                      stop_dist=5.0, target_dist=10.0, max_hold_s=3600, taken=False, mu=0.0,
                                      blocked_reason="cap", branch="blocked", meta_p=0.9, model_version="served-v")
    s = shadow_scoring.Scored(model_version="m", sha256="s", spec_version=sr.SPEC_VERSION, score=0.2, primary=True)
    shadow_scoring.write(db, "c1", int(CLOSE.value) + 1, _snap("fv1"), [s])
    [obs] = _obs(tmp_path)
    assert obs["meta_p"] == pytest.approx(0.9) and obs["model_version"] == "served-v" and obs["feature_version"] is None
    [row] = _scores(tmp_path)
    assert row["score"] == pytest.approx(0.2)
    with db.write() as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE exploration_model_scores SET score = 0.5")


# ------------------------------------------------------------------------------------------------ health and trainer
def test_model_influence_reports_the_scored_share_and_zero_changed(tmp_path):
    from goldbot.ops.checks.learning import influence
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b")])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    _store(tmp_path, "a", _model(0.01), status="active")
    exploration.on_pass(eng, tick)
    _drain(eng)
    con = sqlite3.connect(tmp_path / "aux.db")
    try:
        got = influence(con, since=CLOSE - pd.Timedelta(days=1), until=CLOSE + pd.Timedelta(days=1))
    finally:
        con.close()
    assert (got.n, got.scored, got.changed) == (2, 1, 0)


# ------------------------------------------------------------------------------------------------ structure
def test_the_scorer_reaches_no_order_close_size_limit_or_gate_and_writes_only_scores():
    """Trading-safety: the module names nothing on the order path, and its only writes are the scores table and the
    three NULL score columns of exploration_obs."""
    text = (ROOT / "goldbot" / "engine" / "shadow_scoring.py").read_text()
    mod = ast.parse(text)
    names = {n.attr for n in ast.walk(mod) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(mod) if isinstance(n, ast.Name)}
    for name in ("_execute", "_place", "order_send", "_try_close", "close", "modify", "_sizing_terms", "limits", "gate",
                 "_send", "queue_signal"):
        assert name not in names, name
    sql = [s for s in (n.value for n in ast.walk(mod) if isinstance(n, ast.Constant) and isinstance(n.value, str))
           if s.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ", "DROP ", "CREATE ", "ALTER "))]
    assert sql == ["INSERT OR IGNORE INTO exploration_model_scores (candidate_id, model_version, model_sha256, "
                   "spec_version, feature_version, vector_sha256, score, is_primary, decision_ns, scored_ns) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   "UPDATE exploration_obs SET meta_p = ?, model_version = ?, feature_version = ? WHERE candidate_id "
                   "= ? AND meta_p IS NULL AND model_version IS NULL AND feature_version IS NULL"]


def test_the_engine_calls_the_scorer_only_from_candidate_after_the_record_and_gates_only_before_the_riskgate():
    ex = ast.parse((ROOT / "goldbot" / "engine" / "exploration.py").read_text())
    calls = [(fn.name, n.func.attr, n.lineno) for fn in ast.walk(ex) if isinstance(fn, ast.FunctionDef)
             for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "shadow_scoring"]
    assert sorted((f, a) for f, a, _ in calls) == [("_enter", "gate_waits"), ("_send", "gate"),
                                                   ("candidate", "submit"), ("on_pass", "mark_pass"),
                                                   ("on_pass", "mark_pass"), ("on_pass_slow", "mark_pass"),
                                                   ("on_pass_slow", "mark_pass")]
    line = {a: ln for _, a, ln in calls}
    cand = next(fn for fn in ast.walk(ex) if isinstance(fn, ast.FunctionDef) and fn.name == "candidate")
    record = [n.lineno for n in ast.walk(cand) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "record_candidate"]
    assert record and line["submit"] > max(record)
    send = next(fn for fn in ast.walk(ex) if isinstance(fn, ast.FunctionDef) and fn.name == "_send")
    places = [n.lineno for n in ast.walk(send) if isinstance(n, ast.Attribute) and n.attr == "_place"]
    assert places and line["gate"] < min(places)                       # the gate acts before the RiskGate path
    for path in sorted((ROOT / "goldbot").rglob("*.py")):
        if path.name not in ("exploration.py", "shadow_scoring.py", "runner.py") and \
                re.search(r"\bshadow_scoring\.[A-Za-z_]+\(", path.read_text()):
            raise AssertionError(path)


def test_the_published_state_is_empty_until_a_candidate_was_queued(tmp_path):
    eng, _pb = _pass(tmp_path, [_arm("a")], on=False)
    assert shadow_scoring.published(eng) == {}
    assert _actions(eng, "exploration:")


def test_a_published_state_that_raises_publishes_its_error_and_the_state_risk_and_orders_writes_still_happen(
        tmp_path, monkeypatch, caplog):
    """Trading-safety L1 of #463: a failure in the scoring counts never skips write_engine_state, _save_risk_state
    or _save_orders (like `_throttle_state`, it publishes `error`)."""
    eng, _pb = _pass(tmp_path, [_arm("a")], on=True)
    assert eng.exploration is not None and eng.exploration.shadow_scorer is not None
    monkeypatch.setattr(eng.exploration.shadow_scorer, "jobs", None)          # .qsize() raises AttributeError
    assert shadow_scoring.published(eng) == {"shadow_scoring": {"error": "AttributeError"}}
    written: list[str] = []
    store = eng.state_store
    write = store.write_engine_state

    def spy_write(payload: dict, **k: Any) -> Any:
        written.append("state")
        assert payload["shadow_scoring"] == {"error": "AttributeError"}
        return write(payload, **k)

    monkeypatch.setattr(store, "write_engine_state", spy_write)
    def spy(name: str) -> Any:
        orig = getattr(eng, name)

        def run() -> Any:
            written.append(name)
            return orig()
        return run

    for name in ("_save_risk_state", "_save_orders"):
        monkeypatch.setattr(eng, name, spy(name))
    eng._write_state()
    assert written == ["state", "_save_risk_state", "_save_orders"]
    assert any("shadow scoring state not published" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------------------------------------ the demo gate
# Owner 2026-11-25: served models may gate DEMO exploration entries, with a randomised 10 % of model-rejected
# candidates still taken (ADR 0098 section 3); then "before deploying, the models should be trained": shipped OFF on
# every account and no spec may gate until the new pipeline's model is named in GATE_SPECS.
def _gating(monkeypatch: pytest.MonkeyPatch, eng: Any, *, u: float = 0.5) -> Any:
    monkeypatch.setattr(shadow_scoring, "GATE_SPECS", frozenset({sr.SPEC_VERSION}))
    monkeypatch.setattr(shadow_scoring, "explore_u", lambda _cid: u)
    eng.cfg.model_gate = True
    return _scoring(eng)


def _gated_pass(eng: Any, tick: Any) -> None:
    """The first pass waits for the vector (built on the worker), the next one (same tick, inside GATE_WAIT_S)
    decides."""
    exploration.on_pass(eng, tick)
    _drain(eng)
    exploration.on_pass(eng, tick)
    _drain(eng)


def _trades(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(r["body"]) for r in _rows(tmp_path, "exploration_trades", "client_order_id")]


def test_gating_is_shipped_off_on_every_account_and_no_spec_may_gate_yet():
    from goldbot.engine import EngineConfig
    from goldbot.ops.accounts import Account, load_accounts
    assert shadow_scoring.GATE_SPECS == frozenset()
    assert EngineConfig(account_id="x", broker_name="icm").model_gate is False
    assert Account.model_fields["model_gate"].default is False
    assert not [a.account_id for a in load_accounts().values() if a.model_gate]


def test_with_the_flag_on_but_no_gating_spec_entries_are_identical_to_no_models(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _scoring(eng)
    eng.cfg.model_gate = True
    _store(tmp_path, "a", _model(0.01), status="active")
    _gated_pass(eng, tick)
    assert len(_sent(eng)) == 1 and not _actions(eng, "exploration_refused:model_gate")


def test_a_gated_candidate_below_the_cut_is_refused_before_the_riskgate_with_its_propensity(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng, u=0.5)
    v = _store(tmp_path, "a", _model(0.2, tau=0.5), status="active")
    calls: list[Any] = []
    check = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return check(*a, **k)

    eng.gate.check = spy  # type: ignore[method-assign]
    _gated_pass(eng, tick)
    assert not calls and not pb.positions() and not _sent(eng)
    [row] = _actions(eng, "exploration_refused:")
    assert row["action"] == "exploration_refused:model_gate:" + v
    assert row["meta_p"] == pytest.approx(0.2) and row["model_version"] == v
    [obs] = _obs(tmp_path)
    assert obs["taken"] == 0 and obs["blocked_reason"] == "model_gate:" + v
    assert obs["mu"] == pytest.approx(shadow_scoring.EXPLORE_P)          # ADR 0098: propensity 0.10 logged
    assert obs["meta_p"] == pytest.approx(0.2) and obs["model_version"] == v


def test_a_rejected_candidate_in_the_10_percent_sample_is_sent_as_an_explore_sample(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng, u=0.05)
    _store(tmp_path, "a", _model(0.2, tau=0.5), status="active")
    _gated_pass(eng, tick)
    assert len(_sent(eng)) == 1 and _actions(eng, "exploration_model_sample")
    [obs] = _obs(tmp_path)
    assert obs["taken"] == 1 and obs["mu"] == pytest.approx(shadow_scoring.EXPLORE_P)
    [trade] = _trades(tmp_path)
    assert trade["evidence_class"] == "explore_sample"


def test_a_candidate_the_model_keeps_is_sent_unchanged(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng)
    _store(tmp_path, "a", _model(0.7, tau=0.5), status="active")
    _gated_pass(eng, tick)
    [pos] = pb.positions()
    assert pos.lots == pytest.approx(0.01)
    [trade] = _trades(tmp_path)
    assert trade["evidence_class"] != "explore_sample"
    [obs] = _obs(tmp_path)
    assert obs["taken"] == 1 and obs["mu"] == pytest.approx(exploration.PI_RANDOM)


def test_no_vector_in_time_fails_open_to_the_existing_rule(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng)
    _store(tmp_path, "a", _model(0.01), status="active")
    monkeypatch.setattr(fs, "decision_features", lambda *a, **k: None)
    _gated_pass(eng, tick)
    assert len(_sent(eng)) == 1
    ex = eng.exploration
    assert ex is not None and ex.shadow_scorer is not None and ex.shadow_scorer.counts["gate_fail_open"] >= 1


def test_the_gate_waits_at_most_gate_wait_s_for_the_vector(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng)
    _store(tmp_path, "a", _model(0.01), status="active")
    ex = eng.exploration
    assert ex is not None
    s = Pending(arm_id="a", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4)
    late = CLOSE + pd.Timedelta(seconds=shadow_scoring.GATE_WAIT_S)
    assert shadow_scoring.gate_waits(eng, ex, s, late) is False
    assert shadow_scoring.gate_waits(eng, ex, s, CLOSE + pd.Timedelta(seconds=1)) is True
    assert ex.shadow_scorer is not None and ex.shadow_scorer.drain(60.0)
    assert shadow_scoring.gate_waits(eng, ex, s, CLOSE + pd.Timedelta(seconds=1)) is False    # vector ready


def test_controls_replay_only_owner_experiments_and_pyramid_adds_are_never_gated(tmp_path, monkeypatch):
    from goldbot.research.arms import Arm as SpecArm
    from tests.test_exploration_engine import LONG, QUALIFIED
    eng, _pb = _engine(tmp_path, [_arm("a")])
    _gating(monkeypatch, eng)

    def arm(role: str, control_mode: str | None = None, control_share: float | None = None) -> Any:
        extra: dict[str, Any] = {"control_mode": control_mode} if control_mode else {}
        spec = SpecArm(id="x", rule=LONG, timeframe="15m", max_open=5,
                       params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4}, role=role,  # type: ignore[arg-type]
                       qualification=QUALIFIED if role == "strategy" else None, **extra)
        return exploration.Arm(spec=spec, style="intraday", control_share=control_share)

    s = Pending(arm_id="x", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4)
    assert shadow_scoring.gate_applies(eng, arm("strategy"), s)
    assert shadow_scoring.gate_applies(eng, arm("learning"), s)
    assert not shadow_scoring.gate_applies(eng, arm("control", control_share=0.05), s)
    assert not shadow_scoring.gate_applies(eng, arm("control", "replay_only"), s)
    assert not shadow_scoring.gate_applies(eng, arm("owner_experiment"), s)
    assert not shadow_scoring.gate_applies(eng, arm("strategy"), s.model_copy(update={"group_id": "g", "add_no": 1}))
    eng.cfg.model_gate = False
    assert not shadow_scoring.gate_applies(eng, arm("strategy"), s)


def test_an_account_that_is_not_demo_is_never_gated(tmp_path, monkeypatch):
    from goldbot.engine import arm_registry
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng)
    _store(tmp_path, "a", _model(0.01), status="active")
    monkeypatch.setattr(arm_registry, "demo_now", lambda _eng: False)
    _gated_pass(eng, tick)
    assert len(_sent(eng)) == 1 and not _actions(eng, "exploration_refused:model_gate")
    eng.cfg.mode = "live"
    ex = eng.exploration
    assert ex is not None
    assert not shadow_scoring.gate_applies(eng, ex.arms["a"], Pending(arm_id="a", side=1, bar_ts=CLOSE, stop_usd=5.0,
                                                                         target_usd=10.0, hold_bars=4))


def test_the_gate_decides_in_under_5_ms_once_the_vector_is_built(tmp_path, monkeypatch, capsys):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng)
    _store(tmp_path, "a", _model(0.2), status="active")
    ex = eng.exploration
    assert ex is not None
    s = Pending(arm_id="a", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4)
    shadow_scoring.gate_waits(eng, ex, s, tick.ts_utc)
    assert ex.shadow_scorer is not None and ex.shadow_scorer.drain(60.0)
    took = []
    for i in range(100):
        t0 = time.perf_counter()
        got = shadow_scoring.gate(eng, ex, ex.arms["a"], s, f"c{i}")
        took.append((time.perf_counter() - t0) * 1000)
        assert got is not None and not got.keep
    with capsys.disabled():
        print(f"\nmodel gate on the tick: median {statistics.median(took):.3f} ms")
    assert statistics.median(took) < 5.0


def test_the_one_row_design_and_score_equal_the_frame_design_and_score_bit_for_bit():
    """The gate and the shadow worker score one vector without pandas (`sr.design_row`, `LogitModel.score_row`); the
    numbers must be the trainer's (`sr.design` then `LogitModel.score`): missing, None, inf and NaN features imputed
    alike, SIGNED columns centred and signed, both sides."""
    import numpy as np
    rng = np.random.default_rng(7)
    n = len(sr.INPUTS)
    model = sr.LogitModel(inputs=sr.INPUTS, medians=tuple(rng.normal(size=n)), means=tuple(rng.normal(size=n)),
                          scales=tuple(rng.uniform(0.5, 2.0, size=n)), coef=tuple(rng.normal(size=n)),
                          intercept=0.3, tau=0.5)
    feats: dict[str, float | None] = {f: float(v) for f, v in zip(sr.FEATURES, rng.normal(size=len(sr.FEATURES)))}
    feats["rsi14"], feats["ret_4"], feats["adx14"] = 61.0, None, math.inf
    feats["bb_z_20"], feats["ret_16"] = math.nan, -math.inf
    del feats["hour_utc"]
    feats["not_an_input"] = 3.0
    for side in (1, -1):
        frame = sr.design(pd.DataFrame([{**feats, "side": float(side)}]))
        row = sr.design_row(feats, side)
        assert np.array_equal(frame.to_numpy(dtype=float)[0], row, equal_nan=True)
        assert model.score_row(row) == float(model.score(frame)[0])


def test_explore_sample_fills_are_excluded_from_edge_reads_like_cohort_fills():
    from goldbot.research.evidence_exclusions import NOT_EDGE_EVIDENCE
    assert set(NOT_EDGE_EVIDENCE) == {"learning_cohort", "explore_sample"}
    assert shadow_scoring.EXPLORE_EVIDENCE in NOT_EDGE_EVIDENCE


def test_a_live_account_with_model_gate_fails_at_load():
    from goldbot.ops.accounts import Account
    base = {"account_id": "x", "broker": "icm", "server": "s", "login": None, "terminal_path": "t", "server_tz": "UTC",
            "symbol": "XAUUSD", "magic_base": 1, "enabled": True}
    assert Account.model_validate({**base, "mode": "demo", "model_gate": True}).model_gate
    with pytest.raises(ValueError, match="model_gate is allowed on demo accounts only"):
        Account.model_validate({**base, "mode": "live", "model_gate": True})


def test_a_1m_or_5m_arm_is_never_gated(tmp_path, monkeypatch):
    """Trading-safety F3 of #463: a gated scalp would wait up to GATE_WAIT_S past its close."""
    eng, _pb = _engine(tmp_path, [_arm("a", tf="1m"), _arm("b", tf="5m"), _arm("c", tf="15m")])
    _gating(monkeypatch, eng)
    ex = eng.exploration
    assert ex is not None

    def sig(a: str) -> Pending:
        return Pending(arm_id=a, side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4)
    assert not shadow_scoring.gate_applies(eng, ex.arms["a"], sig("a"))
    assert not shadow_scoring.gate_applies(eng, ex.arms["b"], sig("b"))
    assert shadow_scoring.gate_applies(eng, ex.arms["c"], sig("c"))


def test_the_sample_draw_is_journalled_and_recomputable_from_the_candidate_id(tmp_path, monkeypatch):
    """F6 of #463: the draw an off-policy read needs."""
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    _gating(monkeypatch, eng)
    monkeypatch.setattr(shadow_scoring, "explore_u", lambda cid: 0.5 if cid else 0.0)
    _store(tmp_path, "a", _model(0.2, tau=0.5), status="active")
    _gated_pass(eng, tick)
    [row] = _actions(eng, "exploration_refused:model_gate")
    [obs] = _obs(tmp_path)
    assert row["proposal"] == obs["candidate_id"] and row["explore_u"] == pytest.approx(0.5)


def test_the_real_draw_is_a_uniform_hash_of_the_candidate_id():
    us = [shadow_scoring.explore_u(f"cand-{i}") for i in range(4000)]
    assert all(0 <= u < 1 for u in us) and shadow_scoring.explore_u("x") == shadow_scoring.explore_u("x")
    assert 0.08 < sum(u < shadow_scoring.EXPLORE_P for u in us) / len(us) < 0.12


def test_model_influence_counts_eligible_candidates_from_the_database_and_warns_when_they_go_unscored(tmp_path):
    """F5 of #463: eligible = an arm with a served model with provenance, decided after its cutoff."""
    from goldbot.ops.checks.learning import influence
    from goldbot.research.exploration_data import record_candidate
    db = exploration.ledger_db(str(tmp_path))
    _store(tmp_path, "a", _model(0.6))
    _store(tmp_path, "b", _model(0.6), sha=None)                       # no provenance: not eligible
    for i, (arm, at) in enumerate([("a", CLOSE), ("a", CLOSE), ("a", CUTOFF - pd.Timedelta(days=1)), ("b", CLOSE),
                                   ("c", CLOSE)]):
        record_candidate(db, candidate_id=f"k{i}", account_id="icm-probe", broker="icm", arm_id=arm, family="f",
                         style="intraday", tf="15m", side=1, decision_utc=at, stop_dist=5.0, target_dist=10.0,
                         max_hold_s=3600, taken=False, mu=0.0, blocked_reason="cap", branch="blocked",
                         meta_p=0.6 if i == 0 else None)
    con = sqlite3.connect(tmp_path / "aux.db")
    try:
        got = influence(con, since=CUTOFF - pd.Timedelta(days=5), until=CLOSE + pd.Timedelta(days=1))
    finally:
        con.close()
    assert (got.eligible, got.eligible_scored) == (2, 1)


def test_explore_samples_count_toward_the_arms_stops_but_never_its_n_or_edge_reads():
    """F2 of #463: the sample's P&L is the arm's money (H-81 cost stop, H-61 unit stop); it is never n, a ranking, an
    edge read or a class figure. The cohort counts toward none of them (ADR 0095)."""
    from goldbot.research.evidence_exclusions import NOT_EDGE_EVIDENCE, NOT_STOP_EVIDENCE
    from goldbot.telegram import labs
    assert "explore_sample" in NOT_EDGE_EVIDENCE and "explore_sample" not in NOT_STOP_EVIDENCE
    assert "learning_cohort" in NOT_EDGE_EVIDENCE and "learning_cohort" in NOT_STOP_EVIDENCE
    base = exploration.ExplorationRecord(
        client_order_id="o", account_id="icm-probe", arm_id="a", family="f", tf="15m", side=1, lots=0.01,
        signal_utc=CLOSE, sent_utc=CLOSE, atr_usd=5.0, stop_usd=5.0, target_usd=10.0, hold_s=3600,
        requested_price=2000.0, spread_usd=0.2, latency_ms=10.0, retcode=10009, position_id=1, fill_price=2000.0,
        stop_loss=5.0, cost=0.5, exit_utc=CLOSE + pd.Timedelta(hours=1), exit_price=2001.0, pnl=1.0, censored=False)
    sample = base.model_copy(update={"evidence_class": "explore_sample"})
    cohort = base.model_copy(update={"evidence_class": "learning_cohort"})
    assert labs.counts_toward_stops(sample) and not labs.counts_toward_reads(sample)
    assert not labs.counts_toward_stops(cohort) and not labs.counts_toward_reads(cohort)
    assert labs.counts_toward_stops(base) and labs.counts_toward_reads(base)


def test_no_reader_names_a_non_edge_evidence_class_literally():
    """Code review MEDIUM of #463: every evidence reader goes through evidence_exclusions.NOT_EDGE_EVIDENCE /
    NOT_STOP_EVIDENCE, so a new class (explore_sample) can never be counted by a reader that forgot it."""
    pat = re.compile(r"""(==|!=|\bin\b)\s*\(?[^\n]*["'](learning_cohort|explore_sample)["']|"""
                     r"""["'](learning_cohort|explore_sample)["']\s*(==|!=)|==\s*COHORT_EVIDENCE""")
    allowed = {"evidence_exclusions.py"}
    bad = []
    for path in sorted((ROOT / "goldbot").rglob("*.py")):
        if path.name in allowed:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if pat.search(code):
                bad.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert bad == []


def test_a_sample_fill_is_listed_with_its_own_evidence_in_the_trades_ledger():
    from typing import get_args

    from goldbot.api.schema import TradeEvidence
    from goldbot.research.evidence_exclusions import NOT_EDGE_EVIDENCE
    assert set(NOT_EDGE_EVIDENCE) <= set(get_args(TradeEvidence))       # trades_ledger lists them by their own name
    web = (ROOT / "web" / "src" / "lib" / "schema.d.ts").read_text()
    assert '"learning_cohort" | "explore_sample"' in web

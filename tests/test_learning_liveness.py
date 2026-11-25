"""Learning liveness judged by OUTPUT (S5, plan W1-C): learners that write nothing and watchers that watch nothing
warn, then fail; model influence is counted from the decisions; open positions without a broker stop fail; the
permanent noise warnings (a late broker minute that was replaced, an optional key) read OK."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.ops import health
from goldbot.ops.checks import learning
from goldbot.ops.checks import self_retrain as sr_check
from goldbot.research import drift
from tests.test_health import SETTINGS, engine, make_ctx, write

TODAY = pd.Timestamp("2026-11-25 07:45", tz="UTC")      # the brain as measured on 11-25
NS = 1_000_000_000


def _ns(ts: pd.Timestamp) -> int:
    return int(ts.value)


def _aux(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _today_settings() -> Any:
    """settings.yaml as deployed on 11-25: H-62 and HAR off, the allocator equal, self-retrain on."""
    s = SETTINGS
    ex = s.research.exploration.model_copy(update={"allocator": "equal", "meta_labeller": False})
    har = s.research.har_abstention.model_copy(update={"enabled": False})
    srs = s.research.self_retrain.model_copy(update={"enabled": True})
    return s.model_copy(update={"research": s.research.model_copy(
        update={"exploration": ex, "har_abstention": har, "self_retrain": srs})})


def _seed_today(tmp_path: Path, now: pd.Timestamp = TODAY) -> None:
    """11-25: one self-retrain run (812 of 9,289 labels with features), 1 shadow model, nightly weights computed,
    every other learning table empty; drift.json and model_watch.json watch no model."""
    db = _aux(tmp_path)
    run_ns = _ns(pd.Timestamp("2026-11-24 07:54", tz="UTC"))
    slot_ns = _ns(pd.Timestamp("2026-11-22 15:00", tz="UTC"))
    with db.write() as c:
        for run_id, broker, labels, feats, weights, arms, shadow in (("r1", "icm", 9289, 812, 41, 1, 1),
                                                                     ("r2", "vantage", 0, 0, 0, 0, 0)):
            c.execute("INSERT INTO self_retrain_runs (run_id, broker, spec_version, slot_ns, cutoff_ns, run_ns, "
                      "status, n_labels, n_with_features, n_features, n_weights, n_arms, n_active, n_shadow, "
                      "n_demoted, detail) VALUES (?, ?, 'sr-v2', ?, ?, ?, 'ok', ?, ?, 40, ?, ?, 0, ?, 0, '{}')",
                      (run_id, broker, slot_ns, slot_ns - 10 * NS, run_ns, labels, feats, weights, arms, shadow))
        c.execute("INSERT INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, status, "
                  "status_reason, gate_pass, cutoff_ns, activation_ns, n_train, n_features, n_weights, model, oos, "
                  "recorded_ns, status_ns) VALUES ('m1', 'r1', 'icm', 'RBR15', 'sr-v2', 'shadow', 'gate', 0, ?, ?, "
                  "132, 40, 41, '{}', '{}', ?, ?)", (slot_ns - 10 * NS, slot_ns + 10 * NS, run_ns, run_ns))
        c.execute("INSERT INTO exploration_broker_weights (run_ns, broker, arm_id, style, weight, detail) "
                  "VALUES (?, 'icm', 'A', 'scalp', 0.5, '{}')", (_ns(now - pd.Timedelta(hours=9)),))
        c.execute("INSERT INTO exploration_obs (candidate_id, account_id, arm_id, family, style, tf, side, is_control, "
                  "decision_ns, session_day, taken, recorded_ns, broker) VALUES ('seed', 'icm-demo', 'A', 'f', "
                  "'scalp', '15m', 1, 0, ?, '2026-11-20', 1, ?, 'icm')",
                  (_ns(now - pd.Timedelta(days=5)), _ns(now - pd.Timedelta(days=5))))
    stamp = (now - pd.Timedelta(hours=8)).isoformat()
    write(tmp_path / "drift.json", {"ts": stamp, "agents": {}, "halted": {}, "size_factor": {}})
    write(tmp_path / "model_watch.json", {"ts": stamp, "agents": {}})


def _liveness(tmp_path: Path, now: pd.Timestamp, settings: Any = None) -> Any:
    return health.check_learning_liveness(make_ctx(tmp_path, now=now, settings=settings or _today_settings()))


# ---------------------------------------------------------------------------------------------- liveness
def test_todays_brain_warns_on_every_empty_learning_table_and_both_watchers_watching_no_model(tmp_path):
    _seed_today(tmp_path)
    c = _liveness(tmp_path, TODAY)
    assert c.status == "warn"
    for name in ("meta_labeller", "arm_priors", "har_days", "long_tf_decisions", "arm_lifecycle_verdicts",
                 "swap_reconcile", "cost_cells", "drift_watch", "model_watch"):
        assert name in c.reason, name
    assert "0 rows" in c.reason
    assert "watches 0 of 1 model" in c.reason
    # the allocator's weights are computed every night but never applied: influence 0 counts as dead
    assert "exploration_weights: computed, not applied" in c.reason


def test_a_learner_silent_for_48_hours_past_the_grace_start_fails(tmp_path):
    _seed_today(tmp_path)
    later = learning.LIVENESS_FROM + pd.Timedelta(hours=49)
    c = _liveness(tmp_path, later)
    assert c.status == "fail"
    assert "long_tf_decisions" in c.reason


def test_a_learner_switched_off_in_settings_warns_with_its_reason_and_never_fails(tmp_path):
    _seed_today(tmp_path)
    later = learning.LIVENESS_FROM + pd.Timedelta(days=30)
    report = learning.judge_all(_reads_for(tmp_path), settings=_today_settings(), now=later,
                                watchers=[], models_to_watch=0, models_since=None)
    by = {r.name: r for r in report}
    assert by["meta_labeller"].status == "warn"
    assert "switched off" in by["meta_labeller"].reason and "research.exploration.meta_labeller" in \
        by["meta_labeller"].reason
    assert by["har_days"].status == "warn"


def _reads_for(tmp_path: Path) -> dict[str, learning.LearnerRead]:
    con = sqlite3.connect(f"file:{tmp_path / 'aux.db'}?mode=ro", uri=True)
    try:
        return {spec.name: learning.read_learner(con, spec) for spec in learning.LEARNERS}
    finally:
        con.close()


def test_a_learner_writing_inside_its_cadence_is_ok_and_one_overdue_warns_then_fails():
    spec = learning.Learner(name="x", table="t", ts_col="ts", cadence_h=24.0, slack_h=6.0, why="test")
    now = learning.LIVENESS_FROM + pd.Timedelta(days=10)
    fresh = learning.LearnerRead(name="x", rows=5, last=now - pd.Timedelta(hours=20))
    late = learning.LearnerRead(name="x", rows=5, last=now - pd.Timedelta(hours=31))
    dead = learning.LearnerRead(name="x", rows=5, last=now - pd.Timedelta(hours=31 + 48))
    assert learning.judge_learner(spec, fresh, enabled=True, applied=True, now=now).status == "ok"
    assert learning.judge_learner(spec, late, enabled=True, applied=True, now=now).status == "warn"
    assert learning.judge_learner(spec, dead, enabled=True, applied=True, now=now).status == "fail"


def test_a_watcher_with_no_model_to_watch_is_ok_and_one_watching_none_of_its_models_warns():
    now = TODAY
    idle = learning.judge_watcher("drift_watch", subjects=0, ts=now, models_to_watch=0, models_since=None,
                                  cadence_h=24, now=now)
    blind = learning.judge_watcher("drift_watch", subjects=0, ts=now, models_to_watch=2,
                                   models_since=now - pd.Timedelta(days=1), cadence_h=24, now=now)
    stale = learning.judge_watcher("drift_watch", subjects=1, ts=now - pd.Timedelta(days=3), models_to_watch=1,
                                   models_since=None, cadence_h=24, now=now)
    assert idle.status == "ok"
    assert blind.status == "warn" and "watches 0 of 2" in blind.reason
    assert stale.status == "warn" and "old" in stale.reason
    gone = learning.judge_watcher("drift_watch", subjects=0, ts=now, models_to_watch=2,
                                  models_since=learning.LIVENESS_FROM,
                                  cadence_h=24, now=learning.LIVENESS_FROM + pd.Timedelta(hours=49))
    assert gone.status == "fail"


def test_liveness_is_absent_where_no_aux_db_lives_or_no_candidate_was_ever_recorded(tmp_path):
    assert not learning.on_brain(make_ctx(tmp_path))
    _aux(tmp_path)
    assert not learning.on_brain(make_ctx(tmp_path))
    _seed_today(tmp_path)
    assert learning.on_brain(make_ctx(tmp_path))


# ---------------------------------------------------------------------------------------------- influence
def _obs(db: Database, n: int, now: pd.Timestamp, *, scored: int = 0, changed: int = 0) -> None:
    with db.write() as c:
        for i in range(n):
            meta = 0.6 if i < scored else None
            blocked = "model_drop" if i < changed else None
            c.execute("INSERT INTO exploration_obs (candidate_id, account_id, arm_id, family, style, tf, side, "
                      "is_control, decision_ns, session_day, taken, blocked_reason, meta_p, model_version, "
                      "recorded_ns, broker) VALUES (?, 'icm-demo', 'A', 'f', 'scalp', '15m', 1, 0, ?, '2026-11-25', "
                      "?, ?, ?, ?, ?, 'icm')",
                      (f"c{i}", _ns(now - pd.Timedelta(hours=2)), int(blocked is None), blocked, meta,
                       "v1" if meta is not None else None, _ns(now)))


def test_influence_is_zero_and_warns_when_no_decision_carries_a_model(tmp_path):
    _obs(_aux(tmp_path), 40, TODAY)
    c = health.check_model_influence(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c.status == "warn"
    assert "0 of 40" in c.reason and "0.0 % scored" in c.reason


def test_influence_counts_scored_and_changed_decisions(tmp_path):
    _obs(_aux(tmp_path), 40, TODAY, scored=30, changed=4)
    con = sqlite3.connect(tmp_path / "aux.db")
    try:
        inf = learning.influence(con, since=TODAY - pd.Timedelta(hours=24), until=TODAY)
    finally:
        con.close()
    assert (inf.n, inf.scored, inf.changed) == (40, 30, 4)
    assert inf.scored_pct == pytest.approx(75.0)
    c = health.check_model_influence(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c.status == "ok"


def test_influence_is_ok_with_no_candidates_in_the_window(tmp_path):
    _seed_today(tmp_path)          # one candidate five days ago, none in the last 24 h
    c = health.check_model_influence(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c.status == "ok" and "no candidates" in c.reason


# ---------------------------------------------------------------------------------------------- self-retrain
def test_self_retrain_warns_on_a_late_run_low_label_use_a_broker_with_no_rows_and_nothing_active(tmp_path):
    _seed_today(tmp_path)
    c = health.check_self_retrain(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c is not None and c.status == "warn"
    assert "40.9 h late" in c.reason
    assert "8.7 % of labels" in c.reason
    assert "vantage: 0 labels" in c.reason
    assert "0 active" in c.reason


def test_self_retrain_extra_rules_stay_quiet_on_a_clean_run():
    slot = pd.Timestamp("2026-11-29 15:00", tz="UTC")
    run = {"broker": "icm", "status": "ok", "slot_ns": _ns(slot), "run_ns": _ns(slot + pd.Timedelta(minutes=20)),
           "n_labels": 1000, "n_with_features": 900, "n_active": 1}
    assert sr_check.extra_warnings([run], max_late_h=6.0) == []


# ---------------------------------------------------------------------------------------------- stop coverage
def test_every_open_position_with_a_broker_stop_is_ok_and_one_without_fails(tmp_path):
    rows = [{"position_id": 1, "side": 1, "lots": 0.01, "sl": 2600.0, "risk": 3.0},
            {"position_id": 2, "side": -1, "lots": 0.01, "sl": 2700.0, "risk": 3.0}]
    engine(tmp_path, 60, open_risk_positions=rows)
    ok = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    assert ok is not None and ok.status == "ok" and "2 of 2" in ok.reason
    engine(tmp_path, 60, open_risk_positions=[*rows, {"position_id": 3, "side": 1, "lots": 0.01, "sl": None}])
    bad = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    assert bad is not None and bad.status == "fail" and "2 of 3" in bad.reason and "3" in bad.reason
    engine(tmp_path, 60, open_risk_positions=[{"position_id": 4, "side": 1, "lots": 0.01, "sl": 0.0}])
    assert _coverage(tmp_path) == "fail"


def _coverage(tmp_path: Path) -> str | None:
    c = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    return None if c is None else c.status


def test_stop_coverage_is_absent_before_the_engine_publishes_its_positions(tmp_path):
    engine(tmp_path, 60)
    assert _coverage(tmp_path) is None


def test_stop_coverage_is_ok_with_no_open_position(tmp_path):
    engine(tmp_path, 60, open_risk_positions=[])
    assert _coverage(tmp_path) == "ok"


# ---------------------------------------------------------------------------------------------- noise
def test_one_late_broker_minute_then_replaced_reads_ok(tmp_path):
    engine(tmp_path, 60, dq_warnings=[
        "broker_bar_late: 1 minute(s) 2026-10-07 13:50..2026-10-07 13:50 from sampled ticks: the broker's 1m bar was "
        "late or missing; replaced when it arrives",
        "broker_bar_replaced: 1 minute(s) from sampled ticks replaced by the broker's bars (largest close change 0.04); "
        "decisions already taken on them stand"])
    c = health.check_engine(make_ctx(tmp_path), "icm-demo")
    assert c.status == "ok", c.reason
    assert "late broker minute" in c.reason


def test_a_burst_of_late_broker_minutes_still_warns(tmp_path):
    engine(tmp_path, 60, dq_warnings=[
        "broker_bar_late: 6 minute(s) 2026-10-07 13:40..2026-10-07 13:45 from sampled ticks: the broker's 1m bar was "
        "late or missing; replaced when it arrives"])
    assert health.check_engine(make_ctx(tmp_path), "icm-demo").status == "warn"


def test_another_data_quality_warning_still_warns(tmp_path):
    engine(tmp_path, 60, dq_warnings=["closed_trades_unreadable: line 3"])
    assert health.check_engine(make_ctx(tmp_path), "icm-demo").status == "warn"


def test_a_missing_optional_key_reads_ok_with_the_feature_named(tmp_path):
    c = health.check_secrets(make_ctx(tmp_path, secrets={"telegram-bot-token", "mt5-icm-demo"}))
    assert c.status == "ok"
    assert "optional missing: anthropic-api-key" in c.reason


# ---------------------------------------------------------------------------------------------- drift
def test_scored_model_drift_flags_shifted_inputs_and_scores_and_passes_a_stable_model():
    rng = np.random.default_rng(1)
    ref = pd.DataFrame({"a": rng.normal(size=2000), "b": rng.normal(size=2000)})
    same = pd.DataFrame({"a": rng.normal(size=500), "b": rng.normal(size=500)})
    moved = same.assign(a=same["a"] + 2.0)

    def score(X: pd.DataFrame) -> np.ndarray:
        return np.asarray(1 / (1 + np.exp(-X["a"].to_numpy())), dtype=float)
    s = SETTINGS.drift
    stable = drift.scored_model_drift("m", ref, same, score, s)
    shifted = drift.scored_model_drift("m", ref, moved, score, s)
    assert stable.psi_warn == [] and stable.psi_size_down == [] and not stable.score_shift
    assert "a" in shifted.psi_size_down and shifted.score_shift
    assert shifted.psi_score is not None and shifted.psi_score > s.psi_size_down


def test_scored_model_drift_says_why_it_computed_nothing_on_too_few_live_rows():
    ref = pd.DataFrame({"a": np.arange(100.0)})
    d = drift.scored_model_drift("m", ref, ref.head(3), lambda X: X["a"].to_numpy(dtype=float), SETTINGS.drift)
    assert d.psi == {} and d.psi_score is None and "PSI not computed" in d.notes[0]

"""Learning liveness judged by OUTPUT (S5, plan W1-C): learners that write nothing and watchers that watch nothing
warn, then fail; model influence is counted from the decisions; open positions without a broker stop fail; the
permanent noise warnings (a late broker minute that was replaced, an optional key) read OK."""
from __future__ import annotations

import json
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


def _liveness(tmp_path: Path, now: pd.Timestamp, settings: Any = None) -> dict[str, Any]:
    out = health.check_learning_liveness(make_ctx(tmp_path, now=now, settings=settings or _today_settings()))
    return {c.name: c for c in out}


def test_todays_brain_gives_one_check_per_learner_and_watcher_and_warns_on_each_dead_one(tmp_path):
    _seed_today(tmp_path)
    by = _liveness(tmp_path, TODAY)
    for name in ("learning:har_days", "learning:arm_lifecycle_verdicts", "learning:cost_cells", "watch:drift_watch"):
        assert by[name].status == "warn", (name, by[name].reason)
    assert "0 rows" in by["learning:cost_cells"].reason
    assert "watches 0 of 1 self-retrain model" in by["watch:drift_watch"].reason and "m1" in \
        by["watch:drift_watch"].reason
    # SRE review H1: computed-not-applied is model_influence's to report; retired learners read ok with their reason;
    # model_watch is the champions' CUSUM (a filter is judged forward by its own demotion rule)
    assert by["learning:exploration_weights"].status == "ok"
    assert "computed, not applied" in by["learning:exploration_weights"].reason
    assert by["learning:meta_labeller"].status == "ok" and "retired 2026-11-13" in by["learning:meta_labeller"].reason
    assert by["learning:arm_priors"].status == "ok" and "never wired" in by["learning:arm_priors"].reason
    assert by["watch:model_watch"].status == "ok"
    assert "learning:long_tf_decisions" not in by and "learning:swap_reconcile" not in by
    assert by["learning:self_retrain"].status == "ok"


def test_a_dead_learner_fails_48_hours_after_the_grace_start_and_each_check_stands_alone(tmp_path):
    _seed_today(tmp_path)
    by = _liveness(tmp_path, learning.LIVENESS_FROM + pd.Timedelta(hours=49))
    assert by["learning:arm_lifecycle_verdicts"].status == "fail"
    assert by["learning:cost_cells"].status == "fail"
    assert by["watch:drift_watch"].status == "fail"
    assert by["learning:har_days"].status == "warn"           # switched off: never FAILs


def test_the_grace_starts_at_the_first_deploy_whose_commit_contains_this_change(tmp_path, monkeypatch):
    late = learning.LIVENESS_FROM + pd.Timedelta(days=3)
    contains = {"old": False, "new": True, "unknown": None}
    monkeypatch.setattr(learning, "_contains_change", lambda to, repo: contains[to])
    (tmp_path / "deploys.jsonl").write_text("\n".join(json.dumps(r) for r in (
        {"ts": (learning.LIVENESS_FROM + pd.Timedelta(hours=1)).isoformat(), "result": "deployed", "to": "old"},
        {"ts": (late - pd.Timedelta(hours=2)).isoformat(), "result": "deployed", "to": "unknown"},
        {"ts": late.isoformat(), "result": "rolled_back", "to": "new"},
        {"ts": (late + pd.Timedelta(hours=1)).isoformat(), "result": "deployed", "to": "new"})) + "\n")
    assert learning.grace_start(tmp_path) == late + pd.Timedelta(hours=1)
    _seed_today(tmp_path)
    by = _liveness(tmp_path, late + pd.Timedelta(hours=10))
    assert by["learning:cost_cells"].status == "warn"
    assert learning.grace_start(tmp_path / "nowhere") == learning.LIVENESS_FROM


def test_git_ancestry_says_whether_a_deploy_contains_the_change(monkeypatch):
    import subprocess
    head = subprocess.run(["git", "-C", str(learning.REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()          # present in a shallow CI checkout too
    monkeypatch.setattr(learning, "CHANGE_COMMIT", head)
    monkeypatch.setattr(learning, "_CONTAINS", {})
    assert learning._contains_change(head, learning.REPO) is True
    assert learning._contains_change("0" * 40, learning.REPO) is None


def test_a_learner_switched_off_in_settings_warns_with_its_flag_and_never_fails(tmp_path):
    _seed_today(tmp_path)
    by = _liveness(tmp_path, learning.LIVENESS_FROM + pd.Timedelta(days=30))
    assert by["learning:har_days"].status == "warn"
    assert "switched off (research.har_abstention.enabled)" in by["learning:har_days"].reason


def test_a_learner_writing_inside_its_cadence_is_ok_and_one_overdue_warns_then_fails():
    spec = learning.Learner(name="x", table="t", ts_col="ts", cadence_h=24.0, slack_h=6.0, why="test")
    now = learning.LIVENESS_FROM + pd.Timedelta(days=10)
    fresh = learning.LearnerRead(name="x", rows=5, last=now - pd.Timedelta(hours=20))
    late = learning.LearnerRead(name="x", rows=5, last=now - pd.Timedelta(hours=31))
    dead = learning.LearnerRead(name="x", rows=5, last=now - pd.Timedelta(hours=31 + 48))
    assert learning.judge_learner(spec, fresh, enabled=True, applied=True, now=now).status == "ok"
    assert learning.judge_learner(spec, late, enabled=True, applied=True, now=now).status == "warn"
    assert learning.judge_learner(spec, dead, enabled=True, applied=True, now=now).status == "fail"


def test_a_fill_driven_learner_survives_a_weekend_without_output():
    spec = next(x for x in learning.LEARNERS if x.name == "cost_cells")
    cad, slack = learning.cadence(spec, SETTINGS)
    assert cad >= learning.FILL_DRIVEN_CADENCE_H
    friday_night = pd.Timestamp("2026-12-04 23:00", tz="UTC")
    read = learning.LearnerRead(name="cost_cells", rows=9, last=friday_night)
    monday = pd.Timestamp("2026-12-07 07:00", tz="UTC")
    assert learning.judge_learner(spec, read, enabled=True, applied=True, now=monday, cadence_h=cad,
                                  slack_h=slack).status == "ok"


def test_a_watcher_is_judged_by_model_ids_not_by_its_count():
    now = TODAY
    models = {"m1": now - pd.Timedelta(days=1), "m2": now - pd.Timedelta(days=1)}
    other = learning.WatcherReport(name="drift_watch", subjects=frozenset({"agent-x"}), ts=now)
    both = learning.WatcherReport(name="drift_watch", subjects=frozenset({"m1", "m2"}), ts=now)
    stale = learning.WatcherReport(name="drift_watch", subjects=frozenset({"m1", "m2"}), ts=now - pd.Timedelta(days=3))
    blind = learning.judge_watcher(other, must_cover=models, now=now)
    assert blind.status == "warn" and "watches 0 of 2" in blind.reason and "m1, m2" in blind.reason
    assert learning.judge_watcher(both, must_cover=models, now=now).status == "ok"
    assert learning.judge_watcher(stale, must_cover=models, now=now).status == "warn"
    idle = learning.WatcherReport(name="drift_watch", subjects=frozenset(), ts=now)
    assert learning.judge_watcher(idle, must_cover={}, now=now).status == "ok"
    later = learning.LIVENESS_FROM + pd.Timedelta(hours=49)
    assert learning.judge_watcher(other, must_cover=models, now=later).status == "fail"


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


def test_influence_names_the_models_refused_for_missing_provenance(tmp_path):
    # the 11-24 self_retrain_models rows predate migration 45: no model_sha256/feature_versions, so shadow scoring
    # refuses them (goldbot/engine/shadow_scoring.py) and "learning reaches nothing" must say why
    _seed_today(tmp_path)                   # one shadow model, m1, without provenance
    _obs(_aux(tmp_path), 40, TODAY)
    c = health.check_model_influence(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c.status == "warn"
    assert "0 served: 1 rows refused (no provenance, trained before migration 45)" in c.reason


def test_influence_counts_a_null_feature_version_as_refused_by_the_scorers_own_rule(tmp_path):
    # code review of #503: health and scorer share one rule; 'null' is no recorded feature version to the scorer
    from goldbot.research import self_retrain as sr
    _seed_today(tmp_path)
    with _aux(tmp_path).write() as c:
        c.execute("UPDATE self_retrain_models SET model_sha256 = ?, feature_versions = 'null'",
                  (sr.model_sha256("{}"),))
    _obs(_aux(tmp_path), 40, TODAY)
    chk = health.check_model_influence(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert "0 served: 1 rows refused (no provenance, trained before migration 45)" in chk.reason


def test_influence_is_read_at_most_every_15_minutes(tmp_path):
    db = _aux(tmp_path)
    _obs(db, 10, TODAY)
    ctx = make_ctx(tmp_path, now=TODAY, settings=_today_settings())
    assert "0 of 10" in health.check_model_influence(ctx).reason
    with db.write() as c:
        c.execute("UPDATE exploration_obs SET meta_p = 0.7")
    assert "0 of 10" in health.check_model_influence(ctx).reason            # cached
    later = make_ctx(tmp_path, now=TODAY + pd.Timedelta(minutes=16), settings=_today_settings())
    assert "10 of 10" in health.check_model_influence(later).reason


def test_influence_is_ok_with_no_candidates_in_the_window(tmp_path):
    _seed_today(tmp_path)          # one candidate five days ago, none in the last 24 h
    c = health.check_model_influence(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c.status == "ok" and "no candidates" in c.reason


# ---------------------------------------------------------------------------------------------- self-retrain
def test_self_retrain_warns_on_a_late_run_low_label_use_a_broker_with_no_rows_and_nothing_active(tmp_path):
    _seed_today(tmp_path)
    c = health.check_self_retrain(make_ctx(tmp_path, now=TODAY, settings=_today_settings()))
    assert c is not None and c.status == "warn"
    assert c.reason.startswith("next slot 2026-11-29 15:00 UTC; run of 2026-11-24: ")
    assert "40.9 h late" in c.reason
    assert "8.7 % of labels" in c.reason
    assert "vantage: 0 labels" in c.reason
    assert "0 active" in c.reason


def test_self_retrain_extra_rules_stay_quiet_on_a_clean_run():
    slot = pd.Timestamp("2026-11-29 15:00", tz="UTC")
    run = {"broker": "icm", "status": "ok", "slot_ns": _ns(slot), "run_ns": _ns(slot + pd.Timedelta(minutes=20)),
           "n_labels": 1000, "n_with_features": 900, "n_active": 1}
    assert sr_check.extra_warnings([run], max_late_h=6.0, now=slot + pd.Timedelta(hours=1)) == []


def test_a_late_first_run_stops_warning_after_a_day_and_the_reason_names_the_next_slot(tmp_path):
    # SRE 11-27: the 11-24 run (slot 11-22, run_if_never_run) read "40.9 h late" every day, and the 4 h cycle report
    # took it for "self_retrain stopped"; the next slot is Sun 11-29 15:00 UTC
    _seed_today(tmp_path)
    now = pd.Timestamp("2026-11-27 09:00", tz="UTC")
    c = health.check_self_retrain(make_ctx(tmp_path, now=now, settings=_today_settings()))
    assert c is not None
    assert c.reason.startswith("next slot 2026-11-29 15:00 UTC; ")
    assert "late" not in c.reason


def test_an_ok_self_retrain_reason_leads_with_the_next_slot(tmp_path):
    slot = pd.Timestamp("2026-11-29 15:00", tz="UTC")
    db = _aux(tmp_path)
    with db.write() as c:
        c.execute("INSERT INTO self_retrain_runs (run_id, broker, spec_version, slot_ns, cutoff_ns, run_ns, status, "
                  "n_labels, n_with_features, n_features, n_weights, n_arms, n_active, n_shadow, n_demoted, detail) "
                  "VALUES ('r1', 'icm', 'sr-v2', ?, ?, ?, 'ok', 1000, 900, 40, 41, 1, 1, 0, 0, '{}')",
                  (_ns(slot), _ns(slot) - 10 * NS, _ns(slot + pd.Timedelta(minutes=20))))
        c.execute("INSERT INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, status, "
                  "status_reason, gate_pass, cutoff_ns, activation_ns, n_train, n_features, n_weights, model, oos, "
                  "recorded_ns, status_ns) VALUES ('m1', 'r1', 'icm', 'RBR15', 'sr-v2', 'active', 'gate', 1, ?, ?, "
                  "132, 40, 41, '{}', '{}', ?, ?)", (_ns(slot) - 10 * NS, _ns(slot) + 10 * NS, _ns(slot), _ns(slot)))
    chk = health.check_self_retrain(make_ctx(tmp_path, now=slot + pd.Timedelta(hours=2), settings=_today_settings()))
    assert chk is not None and chk.status == "ok"
    assert chk.reason.startswith("next slot 2026-12-06 15:00 UTC; last run 2026-11-29 15:20 UTC")


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


def test_stop_coverage_is_unknown_not_ok_while_positions_are_unreadable_short_listed_or_stale(tmp_path):
    engine(tmp_path, 60, open_risk_positions=[], positions_unreadable=2)
    c = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn" and "unknown" in c.reason and "unreadable" in c.reason
    engine(tmp_path, 60, open_risk_positions=[], open_positions=2)
    c = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn" and "counts 2" in c.reason
    engine(tmp_path, 30 * 60, open_risk_positions=[])          # market open (Wednesday afternoon), 30 min old
    c = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn" and "old" in c.reason


def test_a_foreign_position_without_a_stop_fails_and_says_to_set_it_in_mt5(tmp_path):
    engine(tmp_path, 60, foreign_positions=[7], open_risk_positions=[
        {"position_id": 6, "side": 1, "lots": 0.01, "sl": None}, {"position_id": 7, "side": 1, "lots": 0.01, "sl": None}])
    c = health.check_stop_coverage(make_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "fail"
    assert "own 6" in c.reason and "re-sends" in c.reason
    assert "foreign (other magic or manual) 7" in c.reason and "never touches" in c.reason


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
    assert "1 late broker minute(s): 1 replaced by the broker's bar, 0 still sampled" in c.reason


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


def test_the_decision_time_index_is_additive_so_a_rollback_still_opens_aux_db(tmp_path):
    from goldbot.db.migrations import AUX, compat_back_to
    mig = next(m for m in AUX if m.name == "exploration_obs_decision")
    assert mig.additive and compat_back_to(AUX, mig.version) < mig.version
    _aux(tmp_path)
    con = sqlite3.connect(tmp_path / "aux.db")
    try:
        plan = " ".join(str(r) for r in con.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM exploration_obs WHERE decision_ns >= 0 AND decision_ns < 1"))
    finally:
        con.close()
    assert "exploration_obs_decision" in plan

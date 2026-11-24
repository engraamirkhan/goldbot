"""Weekly self-retrain of the per-arm meta-label filter (goldbot/research/self_retrain.py;
docs/research/prereg-2026-11-24-self-retrain.md). Synthetic aux.db: candidates, replay labels and point-in-time
snapshots written the way the nightly jobs write them."""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to
from goldbot.research import exploration_data as ex
from goldbot.research import self_retrain as sr

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold")
START = pd.Timestamp("2026-10-26 08:00", tz="UTC")
DAYS = 40
PER_DAY = 16


def _write(db: Database, *, arm: str, day: int, k: int, r: float, x: dict[str, float], side: int = 1,
           is_control: bool = False, exit_after: pd.Timedelta = pd.Timedelta(hours=2), pit: str = "written") -> None:
    at = START + pd.Timedelta(days=day, minutes=30 * k)
    cid = f"{arm}-{day}-{k}"
    ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker="icm", arm_id=arm, family="fam",
                        style="intraday", tf="15m", side=side, decision_utc=at, stop_dist=3.0, target_dist=3.0,
                        max_hold_s=7200, taken=False, mu=0.0, branch="blocked", u=0.5, blocked_reason="cap",
                        is_control=is_control)
    body = json.dumps(x, sort_keys=True)
    sha = hashlib.sha256(f"f-test|{cid}|{body}".encode()).hexdigest()
    with db.write() as conn:
        conn.execute("UPDATE exploration_obs SET label_exit_ns = ?, label_sim_r = ?, label_mid_r = ?, "
                     "cost_model_version = 'costs:test', cost_effective_ns = ?, labelled_ns = ? WHERE candidate_id = ?",
                     (int((at + exit_after).value), r, r + 0.1, int(START.value), int((at + exit_after).value), cid))
        conn.execute("INSERT INTO exploration_feature_vectors (sha256, feature_version, n_columns, features, "
                     "recorded_ns) VALUES (?, 'f-test', ?, ?, 0)", (sha, len(x), body))
        conn.execute("INSERT INTO exploration_feature_snapshots (candidate_id, status, vector_sha256, decision_ns, "
                     "available_utc_max_ns, bars_as_of_ns, pit, nominal_minutes, recorded_ns) VALUES "
                     "(?, 'ok', ?, ?, ?, ?, ?, 0, 0)", (cid, sha, int(at.value), int(at.value), int(at.value), pit))


def _feats(rng: np.random.Generator) -> dict[str, float]:
    return {c: float(rng.normal()) for c in sr.FEATURES}


def _populate(db: Database, *, signal_from: int = 0, signal_to: int = DAYS, days: int = DAYS, seed: int = 1,
              arms: tuple[str, ...] = ("A", "N")) -> None:
    """Arm A: net R driven by em_atr_cost inside [signal_from, signal_to); arm N: noise; control C: random, -0.1 R."""
    rng = np.random.default_rng(seed)
    for day in range(days):
        for k in range(PER_DAY):
            for arm in arms:
                x = _feats(rng)
                signal = arm == "A" and signal_from <= day < signal_to
                r = (1.0 if x["em_atr_cost"] > -0.4 else -1.0) if signal else float(rng.choice([1.0, -1.0]))
                _write(db, arm=arm, day=day, k=k, r=r - 0.1, x=x)
            _write(db, arm="C", day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng), is_control=True)


def _run(db: Database, *, days: int = DAYS, serve: bool = True, holdout=None) -> dict:
    cutoff = START + pd.Timedelta(days=days + 1)
    return sr.run_broker(db, broker="icm", slot=cutoff + pd.Timedelta(days=2), cutoff=cutoff,
                         activation=cutoff + pd.Timedelta(days=2), holdout=holdout, serve=serve, named_controls={})


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _models(db: Database) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    with db.connection() as conn:
        for arm, v, st in conn.execute("SELECT arm_id, model_version, status FROM self_retrain_models "
                                       "ORDER BY recorded_ns, model_version"):
            out.setdefault(arm, []).append((v, st))
    return out


# ---------------------------------------------------------------------------------------------- spec
def test_the_model_reads_at_most_forty_inputs_side_included_and_every_signed_column_is_an_input():
    assert len(sr.INPUTS) <= 40 and sr.INPUTS[-1] == "side"
    assert set(sr.SIGNED) <= set(sr.FEATURES) and len(set(sr.FEATURES)) == len(sr.FEATURES)


def test_the_migration_adds_both_tables_additively_so_a_rollback_still_opens_aux_db(tmp_path):
    mig = AUX[-1]
    assert mig.name == "self_retrain" and mig.additive
    assert compat_back_to(AUX, mig.version) < mig.version
    Database.open(tmp_path, "aux")
    con = sqlite3.connect(tmp_path / "aux.db")
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"self_retrain_runs", "self_retrain_models"} <= names


# ---------------------------------------------------------------------------------------------- gate
def test_an_arm_whose_labels_the_features_predict_becomes_active_and_a_noise_arm_stays_shadow(db):
    _populate(db)
    out = _run(db)
    assert out["labels"] == DAYS * PER_DAY * 3 and out["with_features"] == out["labels"]
    assert out["features"] == 40 and out["weights"] == 2 * 41
    st = {a: v[-1][1] for a, v in _models(db).items()}
    assert st == {"A": "active", "N": "shadow"}               # the control is a baseline, never a model
    with db.connection() as conn:
        detail = json.loads(conn.execute("SELECT detail FROM self_retrain_runs").fetchone()[0])
    a = detail["decisions"]["A"]
    assert a["edge_vs_nomodel"] >= sr.MIN_EDGE_R and a["edge_vs_control"] >= sr.MIN_EDGE_R
    assert detail["decisions"]["N"]["decision"] == "shadow"


def test_shuffled_labels_never_promote(db):
    rng = np.random.default_rng(5)
    for day in range(DAYS):
        for k in range(PER_DAY):
            for arm in ("A", "B", "N"):
                _write(db, arm=arm, day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng))
            _write(db, arm="C", day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng), is_control=True)
    _run(db)
    assert all(v[-1][1] == "shadow" for v in _models(db).values())


def test_too_few_candidates_is_insufficient_and_stays_shadow(db):
    _populate(db, days=12)
    _run(db, days=12)
    with db.connection() as conn:
        detail = json.loads(conn.execute("SELECT detail FROM self_retrain_runs").fetchone()[0])
    assert detail["decisions"]["A"]["decision"] == "shadow"
    assert detail["decisions"]["A"]["reason"].startswith("insufficient")


def test_serving_switched_off_keeps_a_passing_model_in_shadow(db):
    _populate(db)
    _run(db, serve=False)
    assert _models(db)["A"][-1][1] == "shadow"


def test_an_active_model_is_demoted_when_its_edge_decays(db):
    _populate(db)
    _run(db)
    assert _models(db)["A"][-1][1] == "active"
    rng = np.random.default_rng(9)                  # the next weeks: arm A's labels are noise
    for day in range(DAYS, DAYS + 40):
        for k in range(PER_DAY):
            _write(db, arm="A", day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng))
            _write(db, arm="C", day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng), is_control=True)
    _run(db, days=DAYS + 40)
    hist = _models(db)["A"]
    assert hist[0][1] == "demoted" and hist[-1][1] == "shadow"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM self_retrain_models WHERE status = 'active'").fetchone()[0] == 0


# ---------------------------------------------------------------------------------------------- point in time
def test_walk_forward_trains_only_on_labels_exited_before_each_test_block(monkeypatch):
    rng = np.random.default_rng(3)
    rows = []
    for day in range(DAYS):
        for k in range(PER_DAY):
            at = START + pd.Timedelta(days=day, minutes=30 * k)
            rows.append({"candidate_id": f"x{day}-{k}", "decision_utc": at, "label_exit_utc": at + pd.Timedelta(days=3),
                         "session_day": ex.session_day(at), "side": 1, "label_sim_r": float(rng.choice([1, -1])),
                         **_feats(rng)})
    tab = pd.DataFrame(rows)
    seen: list[pd.Timestamp] = []
    real_fit = sr.fit_logit

    def spy(X: pd.DataFrame, y: np.ndarray) -> sr.LogitModel:
        seen.append(pd.Timestamp(tab["label_exit_utc"].to_numpy()[X.index.to_numpy(dtype=int)].max()))
        return real_fit(X, y)
    monkeypatch.setattr(sr, "fit_logit", spy)
    oos = sr.walk_forward(tab)
    days = np.array(sorted(set(tab["session_day"])))
    starts = [tab.loc[tab["session_day"].isin(b), "decision_utc"].min()
              for b in np.array_split(days[len(days) // 2:], sr.N_BLOCKS)]
    assert len(seen) == sr.N_BLOCKS and len(oos) > 0
    # each fold's latest training label EXITS before its own test block's first decision (labels run 3 days here)
    assert all(s < t0 for s, t0 in zip(seen, starts, strict=True))


def test_a_label_exiting_after_the_cutoff_is_never_read(db):
    _populate(db, days=DAYS)
    _write(db, arm="A", day=DAYS - 1, k=99, r=5.0, x=_feats(np.random.default_rng(0)), exit_after=pd.Timedelta(days=9))
    rows, counts = sr.load_rows(db, broker="icm", cutoff=START + pd.Timedelta(days=DAYS + 1), holdout=None)
    assert "A-39-99" not in set(rows["candidate_id"])


def test_a_nominal_snapshot_is_not_strictly_point_in_time_and_never_trains(db):
    _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal")
    rows, counts = sr.load_rows(db, broker="icm", cutoff=START + pd.Timedelta(days=2), holdout=None)
    assert rows.empty and counts == {"labels": 1, "with_features": 0, "no_snapshot": 1, "controls": 0}


def test_a_candidate_inside_the_sealed_holdout_fails_the_run(db):
    _populate(db, days=2)
    hold = (START - pd.Timedelta(days=1), START + pd.Timedelta(days=1))
    with pytest.raises(ValueError, match="holdout"):
        _run(db, days=2, holdout=hold)


def test_the_stored_model_is_numbers_only_and_scores_the_same_after_a_round_trip(db):
    _populate(db)
    _run(db)
    act = sr.active_models(db, "icm")["A"]["model"]
    X = sr.design(pd.DataFrame([{**_feats(np.random.default_rng(2)), "side": -1}]))
    assert np.allclose(act.score(X), sr.LogitModel.model_validate_json(act.model_dump_json()).score(X))


# ---------------------------------------------------------------------------------------------- health, job, scope
def test_health_warns_when_off_missed_failed_or_empty_and_reports_counts_when_ok():
    now = pd.Timestamp("2026-11-30 12:00", tz="UTC")
    run = {"broker": "icm", "status": "ok", "run_ns": int((now - pd.Timedelta(days=1)).value), "n_labels": 900,
           "n_with_features": 800, "n_weights": 82, "n_active": 1, "n_arms": 2}
    assert sr.health([run], now, enabled=False)[0] == "warn"
    assert sr.health([], now, enabled=True)[0] == "warn"
    assert sr.health([run | {"run_ns": int((now - pd.Timedelta(days=9)).value)}], now, enabled=True)[0] == "warn"
    assert sr.health([run | {"status": "error"}], now, enabled=True)[0] == "warn"
    assert sr.health([run | {"n_labels": 0, "n_with_features": 0}], now, enabled=True)[0] == "warn"
    status, reason = sr.health([run], now, enabled=True)
    assert status == "ok" and "900 labels" in reason and "82 weights" in reason and "1 active" in reason


def test_the_job_is_scheduled_on_sunday_and_registered():
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings()
    assert "self_retrain" in JOBS
    assert s.scheduler.self_retrain.kind == "weekly" and s.scheduler.self_retrain.weekday == 6
    assert s.research.self_retrain.enabled and s.research.self_retrain.serve


def test_switched_off_the_job_records_a_disabled_run_and_demotes_every_active_model(tmp_path, db):
    from types import SimpleNamespace

    from goldbot.config import load_settings
    from goldbot.ops.jobs import self_retrain
    _populate(db)
    _run(db)
    s = load_settings()
    s = s.model_copy(update={"research": s.research.model_copy(update={
        "self_retrain": s.research.self_retrain.model_copy(update={"enabled": False})})})
    ctx = SimpleNamespace(settings=s, state_dir=tmp_path, accounts=[])
    out = self_retrain(ctx, pd.Timestamp("2027-01-03 15:00", tz="UTC"))     # type: ignore[arg-type]
    assert out["enabled"] is False and out["demoted"] == 1
    with db.connection() as conn:
        runs = sr.latest_runs(conn)
    assert runs and runs[0]["status"] == "disabled"
    assert sr.health(runs, pd.Timestamp("2027-01-03 16:00", tz="UTC"), enabled=False)[0] == "warn"


def test_only_the_engines_meta_filter_reads_the_self_retrain_tables_and_no_execution_or_risk_module_does():
    """The serving hook (goldbot/engine/meta_filter.py, tests/test_meta_filter_serve.py) is the one engine module that
    names them, read-only; execution and risk never do."""
    root = Path(__file__).resolve().parents[1] / "goldbot"
    for sub in ("engine", "execution", "risk"):
        for f in (root / sub).rglob("*.py"):
            text = f.read_text()
            ast.parse(text)
            if f.relative_to(root).as_posix() == "engine/meta_filter.py":
                assert "self_retrain_models" in text and "INSERT" not in text and "UPDATE " not in text
                continue
            assert "self_retrain_" not in text if sub == "engine" else "self_retrain" not in text, f


def test_the_pre_registered_constants_are_the_cards():
    """docs/research/prereg-2026-11-24-self-retrain.md sections 2-3: a change is a new SPEC_VERSION and card."""
    assert sr.SPEC_VERSION == "sr-v1" and len(sr.FEATURES) == 39 and len(sr.SIGNED) == 11
    assert (sr.C_REG, sr.DROP_SHARE, sr.N_BLOCKS, sr.EMBARGO, sr.MIN_TRAIN) == (
        0.1, 1 / 3, 4, pd.Timedelta(days=1), 100)
    assert (sr.MIN_OOS, sr.MIN_KEPT, sr.MIN_OOS_DAYS, sr.MIN_CONTROL, sr.MIN_EDGE_R, sr.ALPHA, sr.BOOT, sr.SEED) == (
        200, 100, 10, 50, 0.05, 0.10, 1000, 20261124)
    assert (sr.DEMOTE_EDGE_R, sr.MIN_FORWARD) == (0.0, 60)
    assert (Path(__file__).resolve().parents[1] / sr.PREREG).exists()

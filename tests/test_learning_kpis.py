"""scripts/learning_kpis.py (plan 2026-11-25 §1b K1, K2, K4; W1-C): usable rows by broker x class x tf with every
empty cell kept, model influence with its money effect, liveness and self-retrain drift, all read-only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.db.connection import Database
from goldbot.research import self_retrain as sr

NOW = pd.Timestamp("2026-11-27 21:00", tz="UTC")
CUTOFF = pd.Timestamp("2026-11-20 21:00", tz="UTC")


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("learning_kpis",
                                                  Path(__file__).parents[1] / "scripts" / "learning_kpis.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _model() -> sr.LogitModel:
    k = len(sr.INPUTS)
    return sr.LogitModel(inputs=sr.INPUTS, medians=(0.0,) * k, means=(0.0,) * k, scales=(1.0,) * k,
                         coef=(1.0,) + (0.0,) * (k - 1), intercept=0.0, tau=0.5)


def _seed(tmp_path: Path) -> None:
    """120 labelled RBR15 15m candidates with snapshots (60 before the cutoff, 60 after with every input shifted), 10
    more without a snapshot, 4 of them blocked by a model; one fill for money per R; one shadow self-retrain model."""
    db = Database.open(tmp_path, "aux")
    rng = np.random.default_rng(3)
    with db.write() as c:
        for i in range(130):
            t = CUTOFF + pd.Timedelta(hours=(i - 60) * 1.5 + 0.5 if i < 120 else 2)
            cid = f"c{i}"
            blocked = "model_drop" if 120 <= i < 124 else None
            c.execute("INSERT INTO exploration_obs (candidate_id, account_id, arm_id, family, style, tf, side, "
                      "is_control, decision_ns, session_day, taken, blocked_reason, meta_p, model_version, "
                      "recorded_ns, broker) VALUES (?, 'icm-demo', 'RBR15', 'f', 'scalp', '15m', 1, 0, ?, ?, ?, ?, "
                      "?, ?, ?, 'icm')",
                      (cid, int(t.value), (t + pd.Timedelta(days=i % 3)).strftime("%Y-%m-%d"), int(blocked is None),
                       blocked, 0.4 if blocked else None, "m1" if blocked else None, int(t.value)))
            c.execute("INSERT INTO exploration_replay (candidate_id, replay_version, label_exit_ns, sim_r, mid_r, "
                      "barrier, entry_price, exit_price, entry_charge_usd, exit_charge_usd, cost_model_version, "
                      "cost_effective_ns, labelled_ns) VALUES (?, 'r2', ?, ?, 0, 'stop', 1, 1, 0, 0, 'v', 0, ?)",
                      (cid, int(t.value) + 1, -1.0 if blocked else 0.5, int(t.value) + 2))
            if i < 120:
                shift = 0.0 if t <= CUTOFF else 3.0
                feats = {f: float(rng.normal() + shift) for f in sr.FEATURES}
                body = json.dumps(feats, sort_keys=True)
                sha = hashlib.sha256(f"{body}{i}".encode()).hexdigest()
                c.execute("INSERT INTO exploration_feature_vectors (sha256, feature_version, n_columns, features, "
                          "recorded_ns) VALUES (?, 'fv', ?, ?, 0)", (sha, len(feats), body))
                c.execute("INSERT INTO exploration_feature_snapshots (candidate_id, status, vector_sha256, "
                          "decision_ns, bars_as_of_ns, pit, recorded_ns) VALUES (?, 'ok', ?, ?, ?, 'written', 0)",
                          (cid, sha, int(t.value), int(t.value)))
        c.execute("INSERT INTO exploration_trades (client_order_id, account_id, arm_id, ts_ns, closed, body) "
                  "VALUES ('c0', 'icm-demo', 'RBR15', 0, 1, ?)", (json.dumps({"stop_loss": 4.0}),))
        c.execute("INSERT INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, status, "
                  "status_reason, gate_pass, cutoff_ns, activation_ns, n_train, n_features, n_weights, model, oos, "
                  "recorded_ns, status_ns) VALUES ('m1', 'r1', 'icm', 'RBR15', 'sr-v2', 'shadow', 'gate', 0, ?, ?, "
                  "30, 40, 41, ?, '{}', 0, 0)",
                  (int(CUTOFF.value), int(CUTOFF.value) + 1, _model().model_dump_json()))


def test_usable_rows_keep_every_class_and_all_nine_timeframes_with_empty_cells_at_zero(tmp_path):
    _seed(tmp_path)
    r = _script().build(tmp_path, until=NOW, days=7)
    k1 = pd.DataFrame(r["k1_usable_rows"])
    assert set(k1["tf"]) == {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}
    assert {"metal", "fx", "index", "energy", "bond", "agri", "crypto"} <= set(k1["asset_class"])
    gold = k1[(k1["asset_class"] == "metal") & (k1["tf"] == "15m")].iloc[0]
    assert (gold["candidates"], gold["labels"], gold["usable"]) == (130, 130, 120)
    assert int(k1[k1["asset_class"] == "fx"]["usable"].sum()) == 0
    assert (k1["history_rows"] == 0).all()


def test_influence_reports_scored_changed_and_the_money_of_the_changed_decisions(tmp_path):
    _seed(tmp_path)
    k2 = _script().build(tmp_path, until=NOW, days=7)["k2_influence"]
    assert k2["changed"] == 4 and k2["scored"] == 4
    # four blocked candidates each labelled -1 R: blocking them moved the book by +4 R, x 4.0 per R
    assert k2["effect_r"] == 4.0 and k2["effect_money"] == 16.0


def test_drift_is_computed_for_the_shadow_model_and_sees_the_shifted_inputs(tmp_path):
    _seed(tmp_path)
    mod = _script()
    r = mod.build(tmp_path, until=NOW, days=7)
    assert [d["model_id"] for d in r["drift"]] == ["m1"]
    d = r["drift"][0]
    assert d["n_ref"] == 60 and d["n_live"] == 60
    assert len(d["psi_size_down"]) >= 30 and d["score_shift"]


def test_the_report_lists_dead_learners_and_reads_without_writing(tmp_path):
    _seed(tmp_path)
    before = (tmp_path / "aux.db").stat().st_mtime_ns
    mod = _script()
    text = mod.markdown(mod.build(tmp_path, until=NOW, days=7))
    assert "K4 liveness" in text and "learning:cost_cells: 0 rows" in text
    assert "| metal |" in text and "| fx |" in text
    assert (tmp_path / "aux.db").stat().st_mtime_ns == before


def test_drift_watch_stores_every_self_retrain_model_so_health_sees_it_covered(tmp_path):
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.ops import health
    from goldbot.ops.checks import learning
    from goldbot.ops.jobs import JobContext, drift_watch
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.research.registry import TrialRegistry
    from tests.test_health import make_ctx
    _seed(tmp_path)
    s = load_settings()
    ctx = JobContext(settings=s, store=Store(tmp_path / "data"), state_dir=tmp_path,
                     models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "t.jsonl"),
                     accounts=[], population=Population(tmp_path / "population.json"))
    out = drift_watch(ctx, NOW)
    assert out["self_retrain"] == 1 and out["halted"] == [] and not out["system_halt"]
    d = json.loads((tmp_path / "drift.json").read_text())
    assert d["self_retrain"]["m1"]["score_shift"] and d["size_factor"] == {}     # reported, never sized or halted
    checks = {c.name: c for c in health.check_learning_liveness(make_ctx(tmp_path, now=NOW, settings=s))}
    assert checks["watch:drift_watch"].status == "ok", checks["watch:drift_watch"].reason
    assert learning.DRIFT_SR_KEY == "self_retrain"

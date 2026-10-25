"""Exploration arms, data lane (L3): the observation ledger, the leak-free training table, the nightly bandit weights."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to, migrate
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.ops import jobs
from goldbot.ops.gates_phase import load_closed_trades, phase_record
from goldbot.research import exploration_data as ex
from goldbot.research.population import agent_trades

T0 = pd.Timestamp("2026-10-01 08:00", tz="UTC")


def _db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _obs(db: Database, i: int, *, arm: str = "arm-a", family: str = "box", r_move: float = 2.0, cost: float = 0.0,
         decision: pd.Timestamp | None = None, hold: pd.Timedelta = pd.Timedelta(hours=2), side: int = 1,
         stop_dist: float = 4.0, censored: bool = False) -> bool:
    d = decision if decision is not None else T0 + pd.Timedelta(hours=6 * i)
    entry = 2000.0
    return ex.record_obs(db, obs_id=f"exp-{arm}-{i}", account_id="icm-demo", arm_id=arm, family=family, tf="1h",
                         side=side, decision_utc=d, entry_utc=d + pd.Timedelta(seconds=1), exit_utc=d + hold,
                         entry_price=entry, exit_price=entry + side * r_move, stop=entry - side * stop_dist,
                         cost_per_oz=cost, censored=censored, snapshot_ref=f"snap-{i}")


# ---------------------------------------------------------------------------------------------- migration
def test_migration_16_adds_the_exploration_tables_and_is_additive_so_a_rollback_still_opens_aux_db(tmp_path):
    m = AUX[15]
    assert (m.version, m.name, m.additive) == (16, "exploration", True)
    assert compat_back_to(AUX, 16) == compat_back_to(AUX, 15)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(exploration_obs)")}
        assert {"obs_id", "arm_id", "family", "tf", "side", "decision_ns", "entry_ns", "exit_ns", "entry_price",
                "exit_price", "stop", "r_net", "censored", "snapshot_ref", "quarantine", "recorded_ns"} <= cols
        assert {r[1] for r in conn.execute("PRAGMA table_info(exploration_weights)")} == {
            "run_ns", "arm_id", "weight", "detail"}
        conn.commit()
        assert migrate(conn, "aux", migrations=AUX[:15]) == []          # the older code accepts the newer file
        with pytest.raises(sqlite3.IntegrityError):                       # a share above 1 cannot be stored
            conn.execute("INSERT INTO exploration_weights VALUES (1, 'a', 1.5, '{}')")


# ---------------------------------------------------------------------------------------------- the ledger
def test_r_multiple_is_net_of_measured_costs_and_timestamps_are_epoch_ns(tmp_path):
    db = _db(tmp_path)
    assert _obs(db, 0, r_move=6.0, cost=0.4, stop_dist=4.0)
    [row] = ex.read_obs(db).to_dict("records")
    assert row["r_net"] == pytest.approx((6.0 - 0.4) / 4.0)
    assert row["decision_utc"] == T0 and str(pd.Series([row["exit_utc"]]).dt.tz) == "UTC"
    with db.connection() as conn:
        assert conn.execute("SELECT decision_ns FROM exploration_obs").fetchone()[0] == T0.value


def test_short_r_multiple_signs_the_move_by_side(tmp_path):
    db = _db(tmp_path)
    _obs(db, 0, side=-1, r_move=-2.0, cost=0.2, stop_dist=4.0)          # a short that lost 2 points
    assert ex.read_obs(db)["r_net"].iloc[0] == pytest.approx((-2.0 - 0.2) / 4.0)


def test_record_obs_is_idempotent_and_the_first_record_is_never_overwritten(tmp_path):
    db = _db(tmp_path)
    assert _obs(db, 0, r_move=4.0)
    assert not _obs(db, 0, r_move=-4.0)                                  # a revision: ignored, first value kept
    obs = ex.read_obs(db)
    assert len(obs) == 1 and obs["r_net"].iloc[0] == pytest.approx(1.0)


def test_an_invalid_observation_is_quarantined_not_dropped_and_never_trained_on(tmp_path):
    db = _db(tmp_path)
    d = T0
    assert ex.record_obs(db, obs_id="bad", account_id="icm-demo", arm_id="arm-a", family="box", tf="1h", side=1,
                         decision_utc=d, entry_utc=d, exit_utc=d + pd.Timedelta(hours=1), entry_price=2000.0,
                         exit_price=2001.0, stop=2004.0, cost_per_oz=0.1, censored=False)   # stop above a long
    _obs(db, 1)
    every = ex.read_obs(db, include_quarantined=True)
    assert set(every["obs_id"]) == {"bad", "exp-arm-a-1"}
    assert "stop" in str(every.set_index("obs_id").loc["bad", "quarantine"])
    assert list(ex.read_obs(db)["obs_id"]) == ["exp-arm-a-1"]


# ---------------------------------------------------------------------------------------------- training table
def _features(times: list[pd.Timestamp], n_cols: int = 3) -> pd.DataFrame:
    return pd.DataFrame({"available_utc": times,
                         **{f"f{j}": [float(i * 10 + j) for i in range(len(times))] for j in range(n_cols)}})


def test_features_available_before_decision_only(tmp_path):
    db = _db(tmp_path)
    d = T0 + pd.Timedelta(hours=10)
    _obs(db, 0, decision=d)
    # row 0: known an hour before the decision (used); row 1: known AT the decision instant (not strictly before);
    # row 2: a future release
    feats = _features([d - pd.Timedelta(hours=1), d, d + pd.Timedelta(minutes=1)])
    tab = ex.training_table(db, feats, arm_id="arm-a", as_of=d + pd.Timedelta(days=1))
    assert len(tab) == 1 and tab["f0"].iloc[0] == 0.0
    # the label is available at exit_utc only: before the exit the trade is not a training row
    assert ex.training_table(db, feats, arm_id="arm-a", as_of=d + pd.Timedelta(hours=1)).empty


def test_a_feature_frame_in_seconds_joins_the_same_as_one_in_ns(tmp_path):
    db = _db(tmp_path)
    d = T0 + pd.Timedelta(hours=10)
    _obs(db, 0, decision=d)
    feats = _features([d - pd.Timedelta(hours=1), d + pd.Timedelta(hours=1)])
    feats_s = feats.assign(available_utc=pd.DatetimeIndex(feats["available_utc"]).as_unit("s"))
    a = ex.training_table(db, feats, arm_id="arm-a", as_of=d + pd.Timedelta(days=1))
    b = ex.training_table(db, feats_s, arm_id="arm-a", as_of=d + pd.Timedelta(days=1))
    assert a["f0"].tolist() == b["f0"].tolist() == [0.0]


def test_more_than_40_features_rejected(tmp_path):
    db = _db(tmp_path)
    _obs(db, 0)
    feats = _features([T0 - pd.Timedelta(hours=1)], n_cols=41)
    with pytest.raises(ValueError, match="40"):
        ex.training_table(db, feats, arm_id="arm-a", as_of=T0 + pd.Timedelta(days=1))
    with pytest.raises(ValueError, match="40"):
        ex.training_table(db, feats, arm_id="arm-a", as_of=T0 + pd.Timedelta(days=1),
                          feature_cols=[f"f{j}" for j in range(41)])
    ok = ex.training_table(db, feats, arm_id="arm-a", as_of=T0 + pd.Timedelta(days=1),
                           feature_cols=[f"f{j}" for j in range(40)])
    assert len(ok) == 1


def test_the_training_table_is_one_arm_never_pooled(tmp_path):
    db = _db(tmp_path)
    _obs(db, 0, arm="arm-a")
    _obs(db, 1, arm="arm-b")
    feats = _features([T0 - pd.Timedelta(hours=1)])
    tab = ex.training_table(db, feats, arm_id="arm-b", as_of=T0 + pd.Timedelta(days=2))
    assert tab["arm_id"].unique().tolist() == ["arm-b"]
    with pytest.raises(ValueError, match="arm"):
        ex.training_table(db, feats, arm_id="", as_of=T0)


def test_purged_embargoed_folds_never_train_on_a_label_overlapping_the_test_window():
    dec = pd.DatetimeIndex([T0 + pd.Timedelta(hours=h) for h in range(0, 100, 2)])
    ext = dec + pd.Timedelta(hours=5)                                     # labels overlap their neighbours
    folds = ex.purged_folds(dec, ext, k=5, embargo=pd.Timedelta(hours=3))
    assert len(folds) == 5
    for train, test in folds:
        t0, t1 = dec[test].min(), ext[test].max()
        assert not set(train) & set(test)
        for i in train:
            assert ext[i] < t0 or dec[i] > t1 + pd.Timedelta(hours=3)


def test_cross_fit_scores_every_arm_separately(tmp_path):
    rng = np.random.default_rng(1)
    n = 120
    dec = pd.DatetimeIndex([T0 + pd.Timedelta(hours=4 * i) for i in range(n)])
    x = rng.normal(size=n)
    tab = pd.DataFrame({"arm_id": np.where(np.arange(n) % 2 == 0, "arm-a", "arm-b"), "decision_utc": dec,
                        "exit_utc": dec + pd.Timedelta(hours=2), "r_net": x + rng.normal(scale=0.5, size=n),
                        "f0": x, "f1": rng.normal(size=n)})
    res = ex.cross_fit_meta(tab, ["f0", "f1"], k=4, embargo=pd.Timedelta(hours=4))
    assert set(res["per_arm"]) == {"arm-a", "arm-b"}
    assert res["per_arm"]["arm-a"]["auc"] > 0.7                           # the informative feature is learned
    assert len(res["model"]["coef"]) == 2


# ---------------------------------------------------------------------------------------------- weights
def test_thompson_prefers_the_better_arm_and_shares_sum_to_at_most_one():
    good, bad = [0.8, 1.2, 0.5, 1.5, 0.9] * 6, [-0.6, -1.0, 0.2, -0.8, -0.4] * 6
    w = ex.thompson_shares({"good": good, "bad": bad, "new": []}, seed=7)
    assert w["good"] > w["new"] > w["bad"] and sum(w.values()) <= 1 + 1e-12
    # a backtest prior moves an arm with no live evidence
    w2 = ex.thompson_shares({"a": [], "b": []}, priors={"a": ex.ArmPrior(mean_r=0.5, sd_r=1.0, n_eff=10)}, seed=7)
    assert w2["a"] > w2["b"]


def test_weights_never_exceed_ceilings(tmp_path):
    db = _db(tmp_path)
    good = [1.0, 1.4, 0.9, 1.2] * 10
    ceilings = {"good": 0.3, "bad": 0.5}
    w = ex.thompson_shares({"good": good, "bad": [-1.0] * 40}, ceilings=ceilings, seed=3)
    assert w["good"] == pytest.approx(0.3) and all(w[a] <= ceilings[a] for a in w)   # capped, never re-normalised up
    ex.write_weights(db, T0.value, w)
    got = ex.weights(db, ["good", "bad"], ceilings=ceilings)
    assert all(0 <= got[a] <= ceilings[a] for a in got) and sum(got.values()) <= 1 + 1e-12
    # the reader clamps again: a stored share above a tighter ceiling is cut to it
    assert ex.weights(db, ["good", "bad"], ceilings={"good": 0.1, "bad": 0.1}) == {"good": 0.1, "bad": pytest.approx(min(w["bad"], 0.1))}
    # equal shares respect ceilings too
    assert ex.weights(db, ["x", "y"], ceilings={"x": 0.2}) == {"x": 0.2, "y": 0.5}


def test_corrupt_or_missing_weights_give_equal_shares(tmp_path):
    db = _db(tmp_path)
    arms = ["a", "b", "c", "d"]
    eq = {a: 0.25 for a in arms}
    assert ex.weights(db, arms) == eq                                      # no run yet
    ex.write_weights(db, 1, {"a": 0.9, "b": 0.9, "c": 0.0, "d": 0.0})     # sums above 1: corrupt
    assert ex.weights(db, arms) == eq
    ex.write_weights(db, 2, {"a": 0.4, "b": 0.3, "c": 0.1})               # an arm the run never scored
    assert ex.weights(db, arms) == eq
    ex.write_weights(db, 3, {"a": 0.4, "b": 0.3, "c": 0.1, "d": 0.1})
    assert ex.weights(db, arms) == {"a": 0.4, "b": 0.3, "c": 0.1, "d": 0.1}
    now = pd.Timestamp(3, unit="ns", tz="UTC") + ex.MAX_WEIGHTS_AGE + pd.Timedelta(seconds=1)
    assert ex.weights(db, arms, now=now) == eq                             # stale
    with db.write() as conn:
        conn.execute("DROP TABLE exploration_weights")
    assert ex.weights(db, arms) == eq
    garbage = tmp_path / "g"
    garbage.mkdir()
    (garbage / "aux.db").write_bytes(b"not a sqlite file" * 100)
    fake = Database.__new__(Database)                                     # a Database over an unreadable file
    fake.path, fake.kind, fake.busy_timeout_ms = garbage / "aux.db", "aux", 100
    assert ex.weights(fake, arms) == eq
    assert ex.weights(db, []) == {}


# ---------------------------------------------------------------------------------------------- isolation
def test_closed_exploration_trade_never_reaches_agent_trades_closed_trades_fills_phase_gate(tmp_path):
    db = _db(tmp_path)
    for i in range(3):
        _obs(db, i)
    assert len(ex.read_obs(db)) == 3
    book = ShadowBook(tmp_path, evidence_start=None)
    assert book.books == {} and agent_trades(book) == {}
    assert load_closed_trades(tmp_path) == ([], None)
    assert Store(tmp_path / "store").read("fills").empty
    assert cost_probe.probes(tmp_path) == []                              # the fills ledger is L2's to write
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not (tmp_path / "phase_state.json").exists() and phase_record(tmp_path) == phase_record(empty)
    # and structurally: the module never names a writer or reader of those populations
    src = Path(ex.__file__).read_text(encoding="utf-8")
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
    assert not names & {"ShadowBook", "append_closed_trade", "agent_trades", "record_evidence", "Store",
                        "cost_probes", "LEDGER"}


# ---------------------------------------------------------------------------------------------- nightly
def _bars_features(times: pd.DatetimeIndex, rng: np.random.Generator) -> pd.DataFrame:
    return pd.DataFrame({"available_utc": times, "f0": rng.normal(size=len(times)), "f1": rng.normal(size=len(times))})


def test_learn_writes_capped_weights_per_arm_and_reports_per_arm_scores(tmp_path):
    db = _db(tmp_path)
    for i in range(30):
        _obs(db, i, arm="arm-a", family="box", r_move=4.0 if i % 3 else -4.0)
        _obs(db, i, arm="arm-b", family="box", r_move=-4.0 if i % 3 else 4.0)
    rng = np.random.default_rng(0)
    times = pd.date_range(T0 - pd.Timedelta(days=1), T0 + pd.Timedelta(days=10), freq="1h")
    calls: list[tuple[str, str]] = []

    def features_for(family: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        calls.append((family, tf))
        return _bars_features(times, rng)

    as_of = T0 + pd.Timedelta(days=10)
    rep = ex.learn(db, features_for, as_of=as_of, ceilings={"arm-a": 0.4, "arm-b": 0.4}, seed=1)
    assert calls == [("box", "1h")]
    assert set(rep["weights"]) == {"arm-a", "arm-b"} and all(v <= 0.4 for v in rep["weights"].values())
    assert rep["weights"]["arm-a"] > rep["weights"]["arm-b"]
    assert set(rep["meta"]["box:1h"]["per_arm"]) <= {"arm-a", "arm-b"}
    assert ex.weights(db, ["arm-a", "arm-b"], now=as_of) == rep["weights"]


def test_learn_with_no_observations_writes_nothing(tmp_path):
    db = _db(tmp_path)
    rep = ex.learn(db, lambda *a: pd.DataFrame(), as_of=T0, seed=1)
    assert rep == {"arms": 0}
    assert ex.weights(db, ["a", "b"]) == {"a": 0.5, "b": 0.5}


def test_the_nightly_cost_job_runs_the_exploration_learner_and_a_failure_never_stops_it(tmp_path, monkeypatch):
    seen: list[Any] = []
    def fake(ctx: Any, slot: pd.Timestamp) -> dict[str, Any]:
        seen.append(slot)
        return {"arms": 0}
    monkeypatch.setattr(jobs, "exploration_learn", fake)
    ctx = SimpleNamespace(settings=SimpleNamespace(research=SimpleNamespace(cost_window_days=7, fills_window_days=7)),
                          accounts=[], upload_costs=None, state_dir=tmp_path)
    out = jobs.nightly_costs(ctx, T0)  # type: ignore[arg-type]
    assert seen == [T0] and out["exploration"] == {"arms": 0}

    def boom(ctx: Any, slot: Any) -> dict[str, Any]:
        raise RuntimeError("x")
    monkeypatch.setattr(jobs, "exploration_learn", boom)
    out = jobs.nightly_costs(ctx, T0)  # type: ignore[arg-type]
    assert out["exploration"] == {"error": "RuntimeError"}


@pytest.mark.integration
def test_exploration_learn_on_a_synthetic_store_stamps_features_at_bar_close_and_writes_weights(tmp_path):
    from goldbot.config import load_settings
    from goldbot.data.quality import check_bars
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.research.registry import TrialRegistry

    store = Store(tmp_path / "data")
    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2025-03-01", "2025-08-01", ticks_per_minute=1, seed=5)))
    for tf in ("1h", "4h", "1d"):
        store.append(f"bars_{tf}", resample_bars(b1, tf), source="synthetic")
    ctx = jobs.JobContext(settings=load_settings(), store=store, state_dir=tmp_path, accounts=[],
                          models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                          population=Population(tmp_path / "population.json"))
    slot = pd.Timestamp("2025-07-31", tz="UTC")
    feats = jobs.exploration_features(ctx, "box", "1h", pd.Timestamp("2025-07-01", tz="UTC"), slot)
    opens = set(pd.to_datetime(store.read("bars_1h", start="2025-01-01", end=slot)["ts_utc"], utc=True))
    assert set(feats["available_utc"] - pd.Timedelta(hours=1)) <= opens            # knowable at the bar's close
    assert feats["available_utc"].max() == max(o for o in opens if o + pd.Timedelta(hours=1) <= slot) + \
        pd.Timedelta(hours=1)
    assert feats["available_utc"].max() <= slot and len(ex.select_features(feats)) == ex.MAX_FEATURES
    db = _db(tmp_path)
    for i in range(40):
        d = pd.Timestamp("2025-07-01 10:00", tz="UTC") + pd.Timedelta(hours=12 * i)
        ex.record_obs(db, obs_id=f"o{i}", account_id="icm-demo", arm_id=f"arm-{i % 2}", family="box", tf="1h", side=1,
                      decision_utc=d, entry_utc=d, exit_utc=d + pd.Timedelta(hours=3), entry_price=2000.0,
                      exit_price=2000.0 + (3.0 if i % 3 else -3.0), stop=1997.0, cost_per_oz=0.3, censored=False)
    rep = jobs.exploration_learn(ctx, slot)
    assert rep["arms"] == 2 and "error" not in rep["meta"]["box:1h"]
    assert ex.weights(db, ["arm-0", "arm-1"], now=slot) == rep["weights"]


def test_learn_keeps_the_family_model_with_each_arms_weight_row(tmp_path):
    db = _db(tmp_path)
    for i in range(40):
        _obs(db, i, arm=f"arm-{i % 2}", r_move=4.0 if i % 3 else -4.0)
    rng = np.random.default_rng(2)
    times = pd.date_range(T0 - pd.Timedelta(days=1), T0 + pd.Timedelta(days=12), freq="1h")
    ex.learn(db, lambda *a: _bars_features(times, rng), as_of=T0 + pd.Timedelta(days=12), seed=1)
    import json
    with db.connection() as conn:
        rows = {a: json.loads(d) for a, d in conn.execute("SELECT arm_id, detail FROM exploration_weights")}
    assert set(rows) == {"arm-0", "arm-1"}
    assert rows["arm-0"]["model"]["features"] == ["f0", "f1"] and rows["arm-0"]["meta"]["n"] == 20

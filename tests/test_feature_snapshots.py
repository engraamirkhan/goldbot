"""ML review 2026-11-13 improvement B (docs/research/ml-review-2026-11-13.md), lane ML1 item 3: a point-in-time feature
snapshot for every exploration candidate (goldbot/research/feature_snapshots.py; aux.db migration 32). Acceptance:
recomputing from stored bars cut at each decision shows 0 look-ahead mismatches, and a shuffled-label model reads an
AUC of about 0.50."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to
from goldbot.research import exploration_data as ex
from goldbot.research import feature_snapshots as fs

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold")     # made-up arm ids read as gold (tests/conftest.py)
START = pd.Timestamp("2026-09-01", tz="UTC")
DAYS = 40


def _minutes(days: int = DAYS, seed: int = 7) -> pd.DataFrame:
    """1m bid/ask bars of a random walk (every minute, visible at its close)."""
    n = days * 1440
    rng = np.random.default_rng(seed)
    close = 2000.0 + np.cumsum(rng.normal(0, 0.25, n))
    opn = np.r_[close[0], close[:-1]]
    hi = np.maximum(opn, close) + rng.uniform(0, 0.2, n)
    lo = np.minimum(opn, close) - rng.uniform(0, 0.2, n)
    ts = START + pd.to_timedelta(np.arange(n), unit="min")
    out = pd.DataFrame({"ts_utc": ts})
    for side, off in (("bid", -0.1), ("ask", 0.1)):
        out[f"{side}_open"], out[f"{side}_high"] = opn + off, hi + off
        out[f"{side}_low"], out[f"{side}_close"] = lo + off, close + off
    out["tick_count"] = rng.integers(5, 40, n).astype(float)
    out["spread_mean"], out["spread_max"] = 0.2, 0.3
    out["visible_at"] = out["ts_utc"] + pd.Timedelta(minutes=1)
    return out


@pytest.fixture(scope="module")
def minutes() -> pd.DataFrame:
    return _minutes()


DECISIONS = [START + pd.Timedelta(days=36, hours=h) for h in range(0, 72, 6)]      # 12 cuts, inside the history


def _cand(db: Database, cid: str, at: pd.Timestamp, *, account: str = "icm-probe", tf: str = "1h") -> None:
    ex.record_candidate(db, candidate_id=cid, account_id=account, broker="icm", arm_id="I1", family="box",
                        style="intraday", tf=tf, side=1, decision_utc=at, stop_dist=2.0, target_dist=2.0,
                        max_hold_s=3600, taken=False, mu=0.0, branch="blocked", u=0.9, blocked_reason="cap")


# ---------------------------------------------------------------------------------------------- migration
def test_the_snapshot_migration_adds_the_snapshot_tables_additively_so_a_rollback_still_opens_aux_db(tmp_path):
    m = next(m for m in AUX if m.name == "exploration_feature_snapshots")
    assert m.additive and m.version == 32  # later additive aux migrations (33 long_tf_decisions) follow it
    assert compat_back_to(AUX, m.version) == compat_back_to(AUX, m.version - 1)
    db = Database.open(tmp_path, "aux")
    with db.connection() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"exploration_feature_snapshots", "exploration_feature_vectors"} <= tables


# ---------------------------------------------------------------------------------------------- the vector
def test_a_snapshot_is_the_engines_default_feature_row_of_the_last_bar_visible_at_the_decision(minutes):
    at = DECISIONS[0]
    snap = fs.decision_features(minutes, "1h", at)
    assert snap is not None
    assert snap.available_utc_max <= at
    assert snap.available_utc_max == at                       # the 1h bar closing at the decision is the last one
    assert snap.feature_version and len(snap.features) > 40   # the whole default set: a model picks <= 40 of them
    assert any(c.startswith("d1_") for c in snap.features) and any(c.startswith("h4_") for c in snap.features)
    assert "ts_utc" not in snap.features and "visible_at" not in snap.features


def test_too_few_bars_visible_at_the_decision_gives_no_snapshot(minutes):
    assert fs.decision_features(minutes, "1h", START + pd.Timedelta(days=2)) is None


def test_recomputing_every_stored_snapshot_from_bars_cut_at_its_decision_shows_zero_lookahead_mismatches(
        tmp_path, minutes):
    """Review B, honest test: "recompute 12 cuts from bars as of the decision, and require 0 mismatches". The store
    holds every minute up to the end of the history (days after each decision); the job must read none of them."""
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes.drop(columns=["visible_at"]), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    for i, at in enumerate(DECISIONS):
        _cand(db, f"c{i}", at)
    _cand(db, "same-bar", DECISIONS[3])                       # two arms at one bar share one stored vector
    slot = START + pd.Timedelta(days=DAYS)
    rep = fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm"}, symbol_of={"icm-probe": "XAUUSD"},
                              as_of=slot, max_decisions=50)
    assert rep["written"] == len(DECISIONS) + 1 and rep["decisions"] == len(DECISIONS)
    stored = fs.read_snapshots(db)
    assert len(stored) == len(DECISIONS) + 1
    mismatches = 0
    for i, at in enumerate(DECISIONS):
        cut = minutes[minutes["visible_at"] <= at]           # only what existed at the decision
        again = fs.decision_features(cut, "1h", at)
        assert again is not None
        row = stored.loc[f"c{i}"].to_dict()
        for col, v in again.features.items():
            got = row[col]
            same = (v is None and pd.isna(got)) or (v is not None and np.isclose(v, got, rtol=1e-9, atol=1e-12))
            mismatches += 0 if same else 1
    assert mismatches == 0
    assert (stored.loc["same-bar"].fillna(-1.0) == stored.loc["c3"].fillna(-1.0)).all()
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exploration_feature_vectors").fetchone()[0] == len(DECISIONS)
    # the check has power: one more bar of history changes the vector
    leak = fs.decision_features(minutes, "1h", DECISIONS[0] + pd.Timedelta(hours=1))
    first = stored.loc["c0"].to_dict()
    assert leak is not None and any(
        not np.isclose(np.nan if v is None else v, first[c], equal_nan=True) for c, v in leak.features.items())


def test_the_job_is_bounded_newest_first_and_never_redoes_a_candidate(tmp_path, minutes):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes, source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    for i, at in enumerate(DECISIONS[:4]):
        _cand(db, f"c{i}", at)
    _cand(db, "early", START + pd.Timedelta(days=1))          # too little history: retried (test_feature_snapshots_pit)
    kw: dict[str, Any] = {"broker_of": {"icm-probe": "icm"}, "symbol_of": {"icm-probe": "XAUUSD"},
                          "as_of": START + pd.Timedelta(days=DAYS)}
    first = fs.snapshot_pending(db, store, max_decisions=2, **kw)
    assert first["decisions"] == 2 and set(fs.read_snapshots(db).index) == {"c2", "c3"}
    second = fs.snapshot_pending(db, store, max_decisions=10, **kw)
    assert second["written"] == 2 and second["status"] == {"ok": 2, "insufficient_bars": 1}
    assert fs.snapshot_pending(db, store, max_decisions=10, **kw)["written"] == 0
    cov = fs.coverage(db, since=START)
    assert cov == {"candidates": 5, "snapshots": 4, "with_features": 4, "written_pit": 0,    # no write times stored
                   "ratio": pytest.approx(0.8)}


def test_an_account_with_no_bars_is_skipped_and_retried_another_night(tmp_path):
    db = Database.open(tmp_path, "aux")
    _cand(db, "x", DECISIONS[0])
    rep = fs.snapshot_pending(db, Store(tmp_path / "data"), broker_of={"icm-probe": "icm"},
                              symbol_of={"icm-probe": "XAUUSD"}, as_of=START + pd.Timedelta(days=DAYS))
    assert rep["written"] == 0 and rep["no_bars"] == ["icm-probe"]
    assert fs.coverage(db, since=START)["snapshots"] == 0


def test_a_candidate_on_a_non_gold_arm_is_left_out(tmp_path, minutes, monkeypatch):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes, source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "g", DECISIONS[0])
    rep = fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm"}, symbol_of={"icm-probe": "XAUUSD"},
                              as_of=START + pd.Timedelta(days=DAYS), instruments={"I1": "EURUSD"})
    assert rep["written"] == 0


# ---------------------------------------------------------------------------------------------- shuffled labels
def test_a_model_on_shuffled_labels_reads_an_auc_of_about_one_half_and_a_real_label_does_not(minutes):
    """Review B: "Shuffle-label AUC within 0.50 +- 0.02". Default-feature rows of every 15m bar of the history and
    the next bar's direction; shuffled (mean of five seeds) the out-of-fold AUC is 0.50 +- 0.02, while a label the
    columns do carry reads above 0.9 (the check has power)."""
    from goldbot.data.resample import mid, resample_bars
    from goldbot.features import build_features
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
    m = mid(resample_bars(minutes, "15m"))
    X = build_features(m, DEFAULT_FEATURE_NAMES).iloc[200:].reset_index(drop=True)
    X = X.drop(columns=["ts_utc"]).select_dtypes("number")
    y = (X["ret_1"].shift(-1) > 0).astype(int).iloc[:-1] if "ret_1" in X else None
    assert y is not None
    X = X.iloc[:-1]
    aucs = [fs.shuffled_label_auc(X, y.to_numpy(), seed=s) for s in range(5)]
    assert abs(float(np.mean(aucs)) - 0.5) <= 0.02
    informative = (X["ret_1"] > X["ret_1"].median()).astype(int).to_numpy()
    assert fs.label_auc(X, informative) > 0.9


def test_the_sanity_model_never_reads_more_than_forty_columns(minutes):
    X = pd.DataFrame(np.random.default_rng(0).normal(size=(200, 45)), columns=[f"f{i}" for i in range(45)])
    with pytest.raises(ValueError):
        fs.label_auc(X, np.r_[np.zeros(100), np.ones(100)].astype(int), columns=list(X.columns))
    assert 0.0 <= fs.label_auc(X, np.r_[np.zeros(100), np.ones(100)].astype(int)) <= 1.0


# ---------------------------------------------------------------------------------------------- isolation
def test_no_engine_module_reads_or_writes_the_snapshots_so_they_never_touch_a_decision():
    root = Path(__file__).resolve().parents[1] / "goldbot" / "engine"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "feature_snapshots" not in text and "exploration_feature_" not in text, path
    tree = ast.parse(Path(fs.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith("goldbot.engine") or m.startswith("goldbot.execution") for m in imported)


def test_the_snapshot_window_is_the_engines_memory_and_feature_window():
    from goldbot.engine.runner import EngineConfig
    fields = EngineConfig.model_fields
    assert fs.ENGINE_MINUTES == fields["max_bars_in_memory"].default
    assert fs.FEATURE_WINDOW == fields["feature_window"].default


def test_a_corrupt_vector_row_is_refused_by_sqlite(tmp_path):
    db = Database.open(tmp_path, "aux")
    with pytest.raises(sqlite3.IntegrityError):
        with db.write() as conn:
            conn.execute("INSERT INTO exploration_feature_vectors (sha256, feature_version, n_columns, features, "
                         "recorded_ns) VALUES ('x', 'v', 1, 'not json', 0)")


# ---------------------------------------------------------------------------------------------- the nightly slot
def test_the_nightly_cost_job_snapshots_after_the_learner_and_a_snapshot_failure_stops_nothing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from goldbot.ops import jobs
    order: list[str] = []
    def learn(ctx: Any, slot: Any) -> dict[str, Any]:
        order.append("learn")
        return {"arms": 0}
    monkeypatch.setattr(jobs, "exploration_learn", learn)

    def boom(ctx: Any, slot: Any) -> dict[str, Any]:
        order.append("snap")
        raise RuntimeError("x")
    monkeypatch.setattr(jobs, "feature_snapshots", boom)
    ctx = SimpleNamespace(settings=SimpleNamespace(research=SimpleNamespace(cost_window_days=7, fills_window_days=7)),
                          accounts=[], upload_costs=None, state_dir=tmp_path)
    out = jobs.nightly_costs(ctx, START)  # type: ignore[arg-type]
    assert order == ["learn", "snap"] and out["exploration"] == {"arms": 0}
    assert out["feature_snapshots"] == {"error": "RuntimeError"}


def test_the_snapshot_job_writes_nothing_when_switched_off_and_reports_the_days_coverage_when_on(tmp_path, minutes):
    from types import SimpleNamespace

    from goldbot.config import load_settings
    from goldbot.ops import jobs
    s = load_settings()
    off = s.model_copy(update={"research": s.research.model_copy(update={
        "exploration": s.research.exploration.model_copy(update={"feature_snapshots": False})})})
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes, source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "c0", DECISIONS[0])
    acc = SimpleNamespace(account_id="icm-probe", broker="icm", symbol="XAUUSD")
    slot = DECISIONS[0] + pd.Timedelta(hours=2)
    off_ctx = SimpleNamespace(settings=off, store=store, state_dir=tmp_path, accounts=[acc])
    assert jobs.feature_snapshots(off_ctx, slot) == {"enabled": False}  # type: ignore[arg-type]
    on_ctx = SimpleNamespace(settings=s, store=store, state_dir=tmp_path, accounts=[acc])
    rep = jobs.feature_snapshots(on_ctx, slot)  # type: ignore[arg-type]
    assert rep["written"] == 1 and rep["coverage_1d"]["with_features"] == 1

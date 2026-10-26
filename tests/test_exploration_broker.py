"""Exploration evidence and costs are keyed by BROKER (ADR 0043 amendment, quant 2026-10-26; program-director plan
2026-10-26 lane L1): "never pooled across brokers; IC Markets hosts may pool with each other only because the cost
basis is the same". Every candidate row carries its broker, labels are replayed with that broker's own cost table
(a broker without enough measured fills pays the conservative prior, never another broker's measurements), and the
H-61 weights and H-62 models are fitted and read per (broker, style)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to, migrate
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat
from goldbot.ops import jobs
from goldbot.research import exploration_data as ex

T0 = pd.Timestamp("2026-10-05 08:00", tz="UTC")
ICM_COSTS = ex.ReplayCosts(broker="icm", version="c-icm", effective_utc=T0 - pd.Timedelta(days=1),
                           slippage_usd=0.05, commission_rt_per_oz=0.07)
VANTAGE_COSTS = ex.ReplayCosts(broker="vantage", version="c-vantage", effective_utc=T0 - pd.Timedelta(days=1),
                               slippage_usd=0.50, commission_rt_per_oz=0.20)
ACCOUNT = {"icm": "icm-probe", "vantage": "vantage-demo"}


def _db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _cand(db: Database, cid: str, broker: str, *, arm: str = "S1a", decision: pd.Timestamp = T0,
          is_control: bool = False, **kw: Any) -> bool:
    return ex.record_candidate(db, candidate_id=cid, account_id=ACCOUNT[broker], broker=broker, arm_id=arm,
                               family="vwap", style="scalp", tf="1m", side=1, decision_utc=decision, stop_dist=2.0,
                               target_dist=2.0, max_hold_s=600, taken=True, mu=0.5, branch="random", u=0.9,
                               is_control=is_control, **kw)


def _label(db: Database, cid: str, sim: float) -> None:
    with db.write() as conn:
        dec = conn.execute("SELECT decision_ns FROM exploration_obs WHERE candidate_id = ?", (cid,)).fetchone()[0]
        conn.execute("UPDATE exploration_obs SET label_sim_r = ?, label_mid_r = ?, label_exit_ns = ?, "
                     "cost_model_version = 'c' WHERE candidate_id = ?", (sim, sim, dec + 600 * 10**9, cid))


def _evidence(db: Database, broker: str, outcome: dict[str, float], n: int = 40) -> None:
    for i in range(n):
        for arm, r in outcome.items():
            cid = f"{broker}-{arm}-{i}"
            _cand(db, cid, broker, arm=arm, decision=T0 + pd.Timedelta(hours=3 * i))
            _label(db, cid, r + (0.3 if i % 2 else -0.3))


def _m1(start: pd.Timestamp, n: int, price: float = 2000.0, spread: float = 0.2) -> pd.DataFrame:
    rows = []
    for i in range(n):
        r: dict[str, Any] = {"ts_utc": start + pd.Timedelta(minutes=i)}
        for side, off in (("bid", -spread / 2), ("ask", spread / 2)):
            r.update({f"{side}_open": price + off, f"{side}_high": price + off, f"{side}_low": price + off,
                      f"{side}_close": price + off})
        rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------- schema
def test_migration_17_keys_exploration_rows_by_broker_additively_and_keeps_the_icm_weights_run(tmp_path):
    m = AUX[16]
    assert (m.version, m.name, m.additive) == (17, "exploration_broker", True)
    assert compat_back_to(AUX, 17) == compat_back_to(AUX, 16)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux", migrations=AUX[:16])
        conn.execute("INSERT INTO exploration_weights VALUES (5, 'S1a', 'scalp', 0.3, '{}')")
        conn.commit()
        migrate(conn, "aux")
        assert "broker" in {r[1] for r in conn.execute("PRAGMA table_info(exploration_obs)")}
        assert "broker" in {r[1] for r in conn.execute("PRAGMA table_info(exploration_models)")}
        # every exploration host before this migration was on IC Markets: its last run stays the ICM run
        assert conn.execute("SELECT run_ns, broker, arm_id, style, weight FROM exploration_broker_weights"
                            ).fetchall() == [(5, "icm", "S1a", "scalp", 0.3)]
        conn.commit()
        assert migrate(conn, "aux", migrations=AUX[:16]) == []          # the older code accepts the newer file


def test_every_candidate_row_records_its_broker_and_a_row_without_one_is_refused(tmp_path):
    db = _db(tmp_path)
    _cand(db, "a", "icm")
    _cand(db, "b", "vantage")
    assert dict(zip(ex.read_obs(db)["candidate_id"], ex.read_obs(db)["broker"], strict=True)) == \
        {"a": "icm", "b": "vantage"}
    assert list(ex.read_obs(db, broker="vantage")["candidate_id"]) == ["b"]
    with pytest.raises(ValueError, match="broker"):
        ex.record_candidate(db, candidate_id="c", account_id="icm-probe", broker="", arm_id="S1a", family="vwap",
                            style="scalp", tf="1m", side=1, decision_utc=T0, stop_dist=2.0, target_dist=2.0,
                            max_hold_s=600, taken=True)


def test_rows_recorded_before_the_broker_key_are_stamped_from_the_accounts_broker_once(tmp_path):
    db = _db(tmp_path)
    _cand(db, "old", "icm")
    _cand(db, "orphan", "vantage")
    with db.write() as conn:
        conn.execute("UPDATE exploration_obs SET broker = NULL")
    assert ex.stamp_brokers(db, {"icm-probe": "icm"}) == 1
    assert ex.stamp_brokers(db, {"icm-probe": "vantage"}) == 0               # a recorded broker is never rewritten
    got = ex.read_obs(db).set_index("candidate_id")["broker"]
    assert got["old"] == "icm" and pd.isna(got["orphan"])                     # unknown account: stays unkeyed


# ---------------------------------------------------------------------------------------------- labels
def test_a_brokers_cost_model_labels_only_that_brokers_rows(tmp_path):
    db = _db(tmp_path)
    _cand(db, "i", "icm")
    _cand(db, "v", "vantage")
    bars = _m1(T0, 30)
    cutoff = T0 + pd.Timedelta(hours=1)
    assert ex.label_candidates(db, bars, VANTAGE_COSTS, cutoff=cutoff) == 1
    obs = ex.read_obs(db).set_index("candidate_id")
    assert pd.isna(obs.loc["i", "label_sim_r"]) and obs.loc["v", "cost_model_version"] == "c-vantage"
    assert ex.label_candidates(db, bars, ICM_COSTS, cutoff=cutoff) == 1
    obs = ex.read_obs(db).set_index("candidate_id")
    assert obs.loc["i", "cost_model_version"] == "c-icm"
    assert obs.loc["v", "label_sim_r"] < obs.loc["i", "label_sim_r"]          # vantage paid its own, dearer costs


def _table(account: str, *, slip: float, n_fills: int, commission: float = 3.5) -> CostTable:
    return CostTable(account_id=account, built_utc=T0 - pd.Timedelta(days=2),
                     spread={"london": SpreadStat(median=0.2, p90=0.3, n=100)},
                     slippage={"london:market": SlippageStat(mean=slip, n=n_fills, from_prior=n_fills < 50)},
                     commission_per_lot_side_usd=commission, slippage_prior_usd=0.15, n_fills=n_fills)


def _ctx(tmp_path: Path, accounts: list[Any], bars: dict[str, pd.DataFrame] | None = None) -> Any:
    store = SimpleNamespace(read=lambda table, source=None, **_: (bars or {}).get(source, pd.DataFrame()))
    return SimpleNamespace(settings=load_settings(), store=store, state_dir=tmp_path, accounts=accounts)


def _acc(account_id: str, broker: str, arms: tuple[Any, ...] = ()) -> Any:
    return SimpleNamespace(account_id=account_id, broker=broker, server_tz="Europe/Athens",
                           exploration=SimpleNamespace(arms=arms))


def test_a_broker_without_its_own_cost_table_pays_the_conservative_prior_never_icms_measured_costs(tmp_path):
    _table("icm-demo", slip=0.40, n_fills=400).save(tmp_path / "costs_icm-demo.json")
    ctx = _ctx(tmp_path, [_acc("icm-demo", "icm"), _acc("vantage-demo", "vantage")])
    s = ctx.settings.costs
    v = jobs.exploration_costs(ctx, T0, "vantage")
    assert v.broker == "vantage" and v.version == "settings-priors:vantage"
    assert v.slippage_usd == s.slippage_prior_usd
    assert v.commission_rt_per_oz == pytest.approx(2 * max(s.commission_per_lot_side_usd.values()) / 100)
    i = jobs.exploration_costs(ctx, T0, "icm")                                # ICM: its measured table, as before
    assert i.broker == "icm" and i.version.startswith("costs:icm-demo:") and i.slippage_usd == pytest.approx(0.40)


def test_a_brokers_own_table_below_the_minimum_fills_still_charges_the_prior_slippage(tmp_path):
    _table("vantage-demo", slip=0.01, n_fills=12, commission=3.0).save(tmp_path / "costs_vantage-demo.json")
    ctx = _ctx(tmp_path, [_acc("vantage-demo", "vantage")])
    v = jobs.exploration_costs(ctx, T0, "vantage")
    assert v.version.startswith("costs:vantage-demo:")
    assert v.slippage_usd == ctx.settings.costs.slippage_prior_usd           # 12 fills: a lucky 0.01 never counts
    assert v.commission_rt_per_oz == pytest.approx(0.06)                      # its own commission


# ---------------------------------------------------------------------------------------------- the bandit
def test_vantage_evidence_never_changes_icm_weights_and_icm_evidence_never_changes_vantage_weights(tmp_path):
    as_of = pd.Timestamp("2026-10-12 23:00", tz="UTC")
    icm_only, both = _db(tmp_path / "a"), _db(tmp_path / "b")
    icm_outcome = {"S1a": 0.5, "S1b": -0.5, "S5a": 0.0, "S5b": 0.1}
    vantage_outcome = {"S1a": -0.8, "S1b": 0.9, "S5a": -0.2, "S5b": 0.0}      # the same arm ids, opposite results
    _evidence(icm_only, "icm", icm_outcome)
    _evidence(both, "icm", icm_outcome)
    _evidence(both, "vantage", vantage_outcome)
    alone = ex.learn(icm_only, broker="icm", as_of=as_of, seed=1)["weights"]
    w_icm = ex.learn(both, broker="icm", as_of=as_of, seed=1)["weights"]
    w_van = ex.learn(both, broker="vantage", as_of=as_of, seed=1)["weights"]
    assert w_icm == alone                                                     # vantage rows changed nothing
    assert w_icm["S1a"] == pytest.approx(0.30) and w_van["S1b"] == pytest.approx(0.30)
    assert w_van["S1a"] < w_icm["S1a"]
    arms = list(icm_outcome)
    assert ex.weights(both, arms, broker="icm", now=as_of) == w_icm           # one run, two brokers, both kept
    assert ex.weights(both, arms, broker="vantage", now=as_of) == w_van
    assert ex.weights(both, arms, broker="pepperstone", now=as_of) == ex.equal_shares(arms)   # no run: equal shares


def test_h62_models_are_fitted_and_served_per_broker(tmp_path):
    db = _db(tmp_path)
    fit: dict[str, Any] = {"style": "scalp", "folds": [], "calibrator": None, "features": ["f0"], "per_arm": {},
                           "auc_net": None, "auc_gross": None, "n": 0}
    cutoff, act = ex.model_cutoff(pd.Timestamp("2026-10-11 01:00", tz="UTC"))
    v_icm = ex.save_model(db, tmp_path / "models", fit, broker="icm", cutoff=cutoff, activation=act)
    v_van = ex.save_model(db, tmp_path / "models", fit, broker="vantage", cutoff=cutoff, activation=act)
    assert v_icm != v_van and "vantage" in v_van
    with db.write() as conn:
        conn.execute("UPDATE exploration_models SET status = 'champion' WHERE model_version = ?", (v_icm,))
    got = ex.serving_model(db, "scalp", act, broker="icm")
    assert got is not None and got["model_version"] == v_icm
    assert ex.serving_model(db, "scalp", act, broker="vantage") is None        # ICM's champion never serves Vantage
    other = _db(tmp_path / "other")                       # ICM has fitted past its labels; Vantage never has
    ex.save_model(other, tmp_path / "models2", fit, broker="icm", cutoff=cutoff, activation=act)
    _evidence(other, "icm", {"S1a": 0.5}, n=3)
    _evidence(other, "vantage", {"S1a": -0.5}, n=2)
    assert len(ex.training_table(other, style="scalp", broker="vantage", as_of=cutoff)) == 2
    assert ex.retrain_due(other, "scalp", cutoff, broker="icm")[1:] == (0, 3)
    assert ex.retrain_due(other, "scalp", cutoff, broker="vantage")[1:] == (2, 2)


# ---------------------------------------------------------------------------------------------- the nightly run
def test_the_nightly_learner_labels_and_allocates_each_broker_on_its_own_costs_and_evidence(tmp_path):
    db = _db(tmp_path)
    _cand(db, "i1", "icm", arm="S1a")
    _cand(db, "v1", "vantage", arm="S1a")
    _cand(db, "v2", "vantage", arm="V9")
    arm = SimpleNamespace
    accounts = [_acc("icm-probe", "icm", (arm(arm_id="S1a", style="scalp", control_share=None),
                                          arm(arm_id="S2", style="scalp", control_share=None))),
                _acc("vantage-demo", "vantage", (arm(arm_id="V9", style="scalp", control_share=None),))]
    bars = {a: _m1(T0, 120) for a in ("icm-probe", "vantage-demo")}
    rep = jobs.exploration_learn(_ctx(tmp_path, accounts, bars), T0 + pd.Timedelta(hours=1))
    assert rep["labelled"] == {"icm-probe": 1, "vantage-demo": 2}
    assert rep["cost_model"] == {"icm": "settings-priors:icm", "vantage": "settings-priors:vantage"}
    assert set(rep["brokers"]["icm"]["weights"]) == {"S1a", "S2"}            # vantage's V9 is not an ICM arm
    assert set(rep["brokers"]["vantage"]["weights"]) == {"S1a", "V9"}
    obs = ex.read_obs(db).set_index("candidate_id")
    assert obs.loc["v1", "cost_model_version"] == "settings-priors:vantage"
    assert obs.loc["i1", "cost_model_version"] == "settings-priors:icm"

"""Exploration arms, data lane (L3; H-60/H-61/H-62): the candidate ledger, the bar-replayed label, the leak-free
training table, H-62 cross-fitting and the H-61 bandit weights."""
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
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.ops import jobs
from goldbot.ops.gates_phase import load_closed_trades, phase_record
from goldbot.research import exploration_data as ex
from goldbot.research.population import agent_trades

T0 = pd.Timestamp("2026-10-05 08:00", tz="UTC")          # a Monday after the holdout
COSTS = ex.ReplayCosts(broker="icm", version="c1", effective_utc=T0 - pd.Timedelta(days=1), slippage_usd=0.05,
                       commission_rt_per_oz=0.07)


def _db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _cand(db: Database, cid: str, *, arm: str = "S1a", style: str = "scalp", tf: str = "1m",
          decision: pd.Timestamp = T0, taken: bool = True, mu: float | None = 0.5, side: int = 1,
          stop: float = 2.0, target: float = 2.0, hold_s: int = 600, features: dict[str, float] | None = None,
          avail: pd.Timestamp | None = None, is_control: bool = False, branch: str = "random", **kw: Any) -> bool:
    return ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker="icm", arm_id=arm, family="vwap", style=style,
                               tf=tf, side=side, decision_utc=decision, stop_dist=stop, target_dist=target,
                               max_hold_s=hold_s, taken=taken, mu=mu, branch=branch, u=0.9, is_control=is_control,
                               features=features, feature_list_id=ex.STYLE_FEATURE_LIST.get(style),
                               available_utc_max=avail, **kw)


def _label(db: Database, cid: str, sim: float, *, mid: float | None = None,
           exit_: pd.Timestamp | None = None) -> None:
    """Test shortcut for a replayed label (the replay itself is tested below)."""
    with db.write() as conn:
        dec = conn.execute("SELECT decision_ns FROM exploration_obs WHERE candidate_id = ?", (cid,)).fetchone()[0]
        e = exit_.value if exit_ is not None else dec + 10 * 60 * 10**9
        conn.execute("UPDATE exploration_obs SET label_sim_r = ?, label_mid_r = ?, label_exit_ns = ?, "
                     "cost_model_version = 'c1' WHERE candidate_id = ?", (sim, sim if mid is None else mid, e, cid))


def _m1(start: pd.Timestamp, mids: list[tuple[float, float, float]], spread: float = 0.2) -> pd.DataFrame:
    """1m bid/ask bars from (open, high, low) mid triples, stamped at their open."""
    rows = []
    for i, (o, h, lo) in enumerate(mids):
        r: dict[str, Any] = {"ts_utc": start + pd.Timedelta(minutes=i)}
        for side, off in (("bid", -spread / 2), ("ask", spread / 2)):
            r.update({f"{side}_open": o + off, f"{side}_high": h + off, f"{side}_low": lo + off,
                      f"{side}_close": o + off})
        rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------- migration
def test_migration_16_adds_the_exploration_tables_and_is_additive_so_a_rollback_still_opens_aux_db(tmp_path):
    m = AUX[15]
    assert (m.version, m.name, m.additive) == (16, "exploration", True)
    assert compat_back_to(AUX, 16) == compat_back_to(AUX, 15)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(exploration_obs)")}
        assert {"candidate_id", "arm_id", "style", "tf", "side", "decision_ns", "session_day", "taken", "mu", "branch",
                "u", "tau_arm", "pi_rand_arm", "meta_p", "model_version", "feature_version", "feature_list_id",
                "features", "available_utc_max_ns", "snapshot_ref", "label_sim_r", "label_mid_r", "label_fill_r",
                "label_exit_ns", "cost_model_version", "quarantine"} <= cols
        for t in ("exploration_weights", "exploration_priors", "exploration_models"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
        conn.commit()
        assert migrate(conn, "aux", migrations=AUX[:15]) == []          # the older code accepts the newer file
        with pytest.raises(sqlite3.IntegrityError):                       # a share above 1 cannot be stored
            conn.execute("INSERT INTO exploration_weights VALUES (1, 'a', 'scalp', 1.5, '{}')")


# ---------------------------------------------------------------------------------------------- candidates
def test_candidates_taken_or_not_are_written_once_with_their_propensity(tmp_path):
    db = _db(tmp_path)
    assert _cand(db, "c1", taken=True, mu=0.8, tau_arm=0.55, pi_rand_arm=0.3, meta_p=0.6, model_version="m0",
                 bandit_version="b0")
    assert _cand(db, "c2", taken=False, mu=0.3)
    assert _cand(db, "c3", taken=False, mu=0.0, blocked_reason="arm_cap", branch="blocked")   # outside OPE, labelled
    assert not _cand(db, "c1", taken=False, mu=0.1)                       # a revision: the first record stands
    obs = ex.read_obs(db).set_index("candidate_id")
    assert obs.loc["c1", "taken"] and obs.loc["c1", "mu"] == pytest.approx(0.8) and obs.loc["c1", "tau_arm"] == 0.55
    assert not obs.loc["c2", "taken"] and obs.loc["c3", "blocked_reason"] == "arm_cap"
    assert obs.loc["c1", "decision_utc"] == T0 and obs.loc["c1", "session_day"] == "2026-10-05"
    with db.connection() as conn:
        assert conn.execute("SELECT decision_ns FROM exploration_obs WHERE candidate_id='c1'").fetchone()[0] == T0.value


def test_1m_and_5m_timeframes_are_accepted(tmp_path):
    db = _db(tmp_path)
    _cand(db, "a", tf="1m")
    _cand(db, "b", tf="5m")
    assert set(ex.read_obs(db)["candidate_id"]) == {"a", "b"} and {"1m", "5m"} <= set(ex.TIMEFRAMES)


def test_30m_candidates_of_the_mb30_mirror_arm_are_accepted(tmp_path):
    db = _db(tmp_path)
    _cand(db, "m", tf="30m")
    assert set(ex.read_obs(db)["candidate_id"]) == {"m"}


@pytest.mark.parametrize("kw, reason", [
    ({"tf": "2m"}, "timeframe"),
    ({"side": 0}, "side"),
    ({"stop": -1.0}, "stop"),
    ({"taken": True, "mu": 0.0}, "mu 0"),
    ({"features": {"x": 1.0}, "avail": T0}, "misses columns"),
    ({"avail": T0 + pd.Timedelta(seconds=1)}, "row invariant"),
])
def test_an_invalid_candidate_is_quarantined_not_dropped_and_never_trained_on(tmp_path, kw, reason):
    db = _db(tmp_path)
    _cand(db, "bad", **kw)
    _cand(db, "good")
    every = ex.read_obs(db, include_quarantined=True).set_index("candidate_id")
    assert reason in str(every.loc["bad", "quarantine"])
    assert list(ex.read_obs(db)["candidate_id"]) == ["good"]


def test_the_fill_r_is_a_diagnostic_written_once_and_never_the_training_label(tmp_path):
    db = _db(tmp_path)
    _cand(db, "c1", taken=True)
    _cand(db, "c2", taken=False, mu=0.4)
    assert ex.record_fill(db, candidate_id="c1", entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=5),
                          entry_price=2000.0, exit_price=2003.0, cost_per_oz=0.2, censored=False)
    assert not ex.record_fill(db, candidate_id="c1", entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=5),
                              entry_price=2000.0, exit_price=1990.0, cost_per_oz=0.2, censored=False)
    assert not ex.record_fill(db, candidate_id="c2", entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=5),
                              entry_price=2000.0, exit_price=2003.0, cost_per_oz=0.2, censored=False)   # untaken
    _label(db, "c1", -0.3)                                               # the replay lost; the fill won
    tab = ex.training_table(db, style="scalp", broker="icm", as_of=T0 + pd.Timedelta(days=1))
    row = tab.set_index("candidate_id").loc["c1"]
    assert row["label_fill_r"] == pytest.approx((3.0 - 0.2) / 2.0) and row["y"] == 0


# ---------------------------------------------------------------------------------------------- label replay
def test_a_flat_market_pays_the_spread_once_plus_floored_slippage_and_commission():
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0)] * 15)
    for side in (1, -1):
        rep = ex.replay_label(bars, side=side, decision_utc=T0, stop_dist=2.0, target_dist=2.0, max_hold_s=600,
                              costs=COSTS)
        assert rep is not None and rep.barrier == "time" and rep.exit_utc == T0 + pd.Timedelta(minutes=10)
        # spread 0.2 once (ask in, bid out), slippage max(0.15, 0.05) on each leg, commission 0.07: no second spread
        assert rep.sim_r == pytest.approx(-(0.2 + 2 * 0.15 + 0.07) / 2.0)
        assert rep.mid_r == pytest.approx(0.0)


def test_target_stop_first_gap_fill_and_no_favourable_slippage():
    flat = (2000.0, 2000.0, 2000.0)
    kw: dict[str, Any] = dict(side=1, decision_utc=T0, stop_dist=2.0, target_dist=2.0, max_hold_s=600, costs=COSTS)
    tgt = ex.replay_label(_m1(T0, [flat, (2000.0, 2003.0, 2000.0)] + [flat] * 10), **kw)
    assert tgt is not None and tgt.barrier == "target" and tgt.sim_r == pytest.approx((2.0 - 0.3 - 0.07) / 2.0)
    assert tgt.mid_r == pytest.approx(1.0) and tgt.exit_utc == T0 + pd.Timedelta(minutes=2)
    both = ex.replay_label(_m1(T0, [flat, (2000.0, 2003.0, 1997.0)] + [flat] * 10), **kw)
    assert both is not None and both.barrier == "stop" and both.sim_r == pytest.approx((-2.0 - 0.37) / 2.0)
    gap = ex.replay_label(_m1(T0, [flat, (1990.0, 1990.0, 1990.0)] + [flat] * 10), **kw)
    assert gap is not None and gap.sim_r == pytest.approx((1989.9 - 2000.1 - 0.37) / 2.0)     # filled at the open
    up = ex.replay_label(_m1(T0, [flat, (2010.0, 2010.0, 2010.0)] + [flat] * 10), **kw)
    assert up is not None and up.sim_r == pytest.approx((2.0 - 0.37) / 2.0)                  # the target, not 2009.9


def test_a_label_without_bars_to_its_time_limit_is_unresolved():
    assert ex.replay_label(_m1(T0, [(2000.0, 2000.0, 2000.0)] * 5), side=1, decision_utc=T0, stop_dist=2.0,
                           target_dist=2.0, max_hold_s=600, costs=COSTS) is None


def test_swap_is_charged_per_rollover():
    start = pd.Timestamp("2026-01-12 21:50", tz="UTC")       # Monday; Europe/Athens midnight is 22:00 UTC in winter
    bars = _m1(start, [(2000.0, 2000.0, 2000.0)] * 45)
    swap = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=20.0, triple_weekday=2, server_tz="Europe/Athens")
    costs = COSTS.model_copy(update={"swap": swap, "effective_utc": start - pd.Timedelta(days=1)})
    long_ = ex.replay_label(bars, side=1, decision_utc=start, stop_dist=2.0, target_dist=2.0, max_hold_s=1800,
                            costs=costs)
    short = ex.replay_label(bars, side=-1, decision_utc=start, stop_dist=2.0, target_dist=2.0, max_hold_s=1800,
                            costs=costs)
    assert long_ is not None and short is not None
    assert long_.sim_r == pytest.approx(-(0.57 + 0.6) / 2.0)       # one night at 60 $/lot = 0.6 $/oz
    assert short.sim_r == pytest.approx(-0.57 / 2.0)               # a swap credit is never counted


def test_label_candidates_labels_taken_and_untaken_once_point_in_time(tmp_path):
    db = _db(tmp_path)
    _cand(db, "t", taken=True)
    _cand(db, "u", taken=False, mu=0.2)
    _cand(db, "late", decision=T0 + pd.Timedelta(minutes=20))       # resolves only after the cutoff
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0)] * 60)
    cutoff = T0 + pd.Timedelta(minutes=25)
    assert ex.label_candidates(db, bars, COSTS, cutoff=cutoff) == 2
    assert ex.label_candidates(db, bars, COSTS, cutoff=cutoff) == 0             # written once
    obs = ex.read_obs(db).set_index("candidate_id")
    assert obs.loc["t", "label_sim_r"] == obs.loc["u", "label_sim_r"] == pytest.approx(-0.285)
    assert pd.isna(obs.loc["late", "label_sim_r"]) and obs.loc["t", "cost_model_version"] == "c1"
    with pytest.raises(ValueError, match="point in time"):
        ex.label_candidates(db, bars, COSTS.model_copy(update={"effective_utc": cutoff + pd.Timedelta(1, "s")}),
                            cutoff=cutoff)


# ---------------------------------------------------------------------------------------------- training table
def test_the_feature_lists_are_the_frozen_v1_lists_of_40_inputs():
    assert set(ex.FEATURE_LISTS) == {"h62-v1-scalp", "h62-v1-intraday", "h62-v1-swing"}
    for lid, cols in ex.FEATURE_LISTS.items():
        assert len(cols) == len(set(cols)) == 38, lid
        assert ex.model_columns(lid)[-2:] == ["side", "arm_id"] and len(ex.model_columns(lid)) == 40
    assert ex.FEATURE_LISTS["h62-v1-scalp"][0] == "em_atr_cost" and ex.FEATURE_LISTS["h62-v1-swing"][-1] == "d1_rv_20"
    assert ex.FEATURE_LISTS["h62-v1-intraday"][-1] == "d1_atr_ratio_14_100"


def _snap(style: str, v: float) -> dict[str, float]:
    return {c: v for c in ex.FEATURE_LISTS[ex.STYLE_FEATURE_LIST[style]]}


def test_the_snapshot_is_read_when_present_and_the_label_is_label_sim_r(tmp_path):
    db = _db(tmp_path)
    _cand(db, "c", features=_snap("scalp", 3.0), avail=T0)
    _label(db, "c", 0.4, mid=-0.1)
    tab = ex.training_table(db, style="scalp", broker="icm", as_of=T0 + pd.Timedelta(days=1))
    assert tab["em_atr_cost"].tolist() == [3.0] and tab["y"].tolist() == [1] and tab["label_mid_r"].tolist() == [-0.1]


def test_features_available_before_decision_only(tmp_path):
    db = _db(tmp_path)
    _cand(db, "c")                                                       # no snapshot: a history-style join
    _label(db, "c", 0.1)
    cols = ["f0"]
    # rule B: the decision bar's own row is available AT the decision (used); a later release is not
    feats = pd.DataFrame({"available_utc": [T0 - pd.Timedelta(minutes=1), T0, T0 + pd.Timedelta(seconds=1)],
                          "f0": [1.0, 2.0, 3.0]})
    tab = ex.training_table(db, style="scalp", broker="icm", as_of=T0 + pd.Timedelta(days=1), features=feats, feature_cols=cols)
    assert tab["f0"].tolist() == [2.0] and tab["available_utc_max"].iloc[0] == T0
    # a feature table in seconds joins the same as one in ns
    feats_s = feats.assign(available_utc=pd.DatetimeIndex(feats["available_utc"]).as_unit("s"))
    tab_s = ex.training_table(db, style="scalp", broker="icm", as_of=T0 + pd.Timedelta(days=1), features=feats_s, feature_cols=cols)
    assert tab_s["f0"].tolist() == [2.0]


def test_a_row_breaking_the_availability_invariant_fails_the_build(tmp_path):
    db = _db(tmp_path)
    _cand(db, "c", features=_snap("scalp", 1.0), avail=T0)
    _label(db, "c", 0.1)
    with db.write() as conn:                                             # corrupted after the write
        conn.execute("UPDATE exploration_obs SET available_utc_max_ns = ?", ((T0 + pd.Timedelta(minutes=1)).value,))
    with pytest.raises(ValueError, match="L-2"):
        ex.training_table(db, style="scalp", broker="icm", as_of=T0 + pd.Timedelta(days=1))


def test_unresolved_holdout_and_other_style_rows_are_not_training_rows(tmp_path):
    db = _db(tmp_path)
    _cand(db, "ok")
    _label(db, "ok", 0.2)
    _cand(db, "open")                                                    # no label yet
    _cand(db, "later")
    _label(db, "later", 0.2, exit_=T0 + pd.Timedelta(days=3))            # resolves after as_of
    _cand(db, "holdout", decision=pd.Timestamp("2026-01-05 10:00", tz="UTC"))
    _label(db, "holdout", 0.2)
    _cand(db, "swing", style="swing", tf="4h")
    _label(db, "swing", 0.2)
    tab = ex.training_table(db, style="scalp", broker="icm", as_of=T0 + pd.Timedelta(days=1))
    assert tab["candidate_id"].tolist() == ["ok"]


def test_more_than_40_features_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError, match="40"):
        ex.training_table(db, style="scalp", broker="icm", as_of=T0, feature_cols=[f"f{j}" for j in range(39)])   # + side + arm_id
    assert ex.training_table(db, style="scalp", broker="icm", as_of=T0, feature_cols=[f"f{j}" for j in range(38)]).empty


# ---------------------------------------------------------------------------------------------- cross-fitting
def test_purged_embargoed_session_day_folds():
    dec = pd.DatetimeIndex([T0 + pd.Timedelta(hours=6 * i) for i in range(80)])
    ext = dec + pd.Timedelta(hours=30)                                   # labels overlap their neighbours
    hold, emb = pd.Timedelta(hours=30), pd.Timedelta(days=5)
    folds = ex.purged_folds(dec, ext, max_hold=hold, embargo=emb)
    days = ex.session_day(dec)
    assert len(folds) == 5
    seen: set[int] = set()
    for train, test in folds:
        assert not set(train) & set(test)
        seen |= set(test)
        assert set(np.flatnonzero(np.isin(days, days[test]))) == set(test)          # a session day never splits
        lo, hi = dec[test].min() - hold, ext[test].max() + hold
        for i in train:
            assert ext[i] < lo or dec[i] > hi                                        # purged by max hold
            assert not (ext[test].max() < dec[i] <= ext[test].max() + emb)          # embargoed 5 days (swing)
    assert seen == set(range(80))


def test_cross_fit_scores_every_arm_on_net_and_gross(tmp_path):
    rng = np.random.default_rng(1)
    n = 600
    dec = pd.DatetimeIndex([T0 + pd.Timedelta(hours=2 * i) for i in range(n)])
    x = rng.normal(size=n)
    r = x + rng.normal(scale=0.5, size=n)
    tab = pd.DataFrame({"arm_id": np.where(np.arange(n) % 2 == 0, "S1a", "S1b"), "decision_utc": dec,
                        "label_exit_utc": dec + pd.Timedelta(minutes=30), "max_hold_s": 1800, "side": 1,
                        "label_sim_r": r, "label_mid_r": r + 0.1, "y": (r > 0).astype(int), "f0": x,
                        "f1": rng.normal(size=n)})
    res = ex.cross_fit_meta(tab, "scalp", feature_cols=["f0", "f1"],
                            params={**ex.H62_PARAMS, "min_child_samples": 20})
    assert set(res["per_arm"]) == {"S1a", "S1b"} and len(res["folds"]) == 5
    assert res["per_arm"]["S1a"]["auc_net"] > 0.7 and res["per_arm"]["S1a"]["auc_gross"] is not None
    assert res["calibrator"] is not None and res["features"] == ["f0", "f1", "side", "arm_id"]


def test_the_model_cutoff_is_friday_close_and_activation_the_next_sunday_open():
    cutoff, act = ex.model_cutoff(pd.Timestamp("2026-10-11 01:00", tz="UTC"))     # a Sunday
    assert cutoff == pd.Timestamp("2026-10-09 21:00", tz="UTC") and act == pd.Timestamp("2026-10-11 22:00", tz="UTC")


# ---------------------------------------------------------------------------------------------- priors and bandit
def test_priors_use_2019_to_2025_09_only_shrunk_by_half_worth_n_over_100():
    ok = pd.DataFrame({"decision_utc": pd.date_range("2019-01-02", "2025-09-29", periods=2000, tz="UTC"),
                       "r_net": np.full(2000, 0.2)})
    p = ex.prior_from_history("S1a", ok, prior_version="h60-v1")
    assert p.mean_r == pytest.approx(0.1) and p.n_eff == pytest.approx(20.0)
    big = pd.DataFrame({"decision_utc": pd.date_range("2019-01-02", "2025-09-29", periods=9000, tz="UTC"),
                        "r_net": np.zeros(9000)})
    assert ex.prior_from_history("x", big, prior_version="v").n_eff == 50.0
    with pytest.raises(ValueError, match="holdout"):
        ex.prior_from_history("x", pd.concat([ok, pd.DataFrame({"decision_utc": [pd.Timestamp("2025-10-01", tz="UTC")],
                                                                 "r_net": [0.0]})]), prior_version="v")
    with pytest.raises(ValueError, match="2019"):
        ex.prior_from_history("x", pd.DataFrame({"decision_utc": [pd.Timestamp("2018-12-31", tz="UTC")],
                                                 "r_net": [0.0]}), prior_version="v")


def test_thompson_nig_prefers_the_better_arm_and_a_history_prior_moves_an_arm_without_live_data(tmp_path):
    good, bad = [0.8, 1.2, 0.5, 1.5, 0.9] * 6, [-0.6, -1.0, 0.2, -0.8, -0.4] * 6
    styles = {a: "scalp" for a in ("good", "bad", "new", "n2")}
    w = ex.thompson_shares({"good": good, "bad": bad, "new": [], "n2": []}, styles, seed=7)
    assert w["good"] >= w["new"] >= w["bad"] and sum(w.values()) == pytest.approx(1.0)
    db = _db(tmp_path)
    hist = pd.DataFrame({"decision_utc": pd.date_range("2020-01-01", "2025-01-01", periods=3000, tz="UTC"),
                         "r_net": np.random.default_rng(0).normal(0.6, 1.0, 3000)})
    ex.write_prior(db, ex.prior_from_history("a", hist, prior_version="h60-v1"))
    pri = ex.read_priors(db)
    sty = {a: "intraday" for a in "abcd"}
    w2 = ex.thompson_shares({a: [] for a in "abcd"}, sty, priors=pri, seed=7)
    assert w2["a"] > max(w2["b"], w2["c"], w2["d"])


def test_weights_never_exceed_ceilings(tmp_path):
    """H-61: within a style every live arm keeps >= 5% and <= 30%; an arm's own ceiling only lowers its cap."""
    db = _db(tmp_path)
    r = {"star": [1.5] * 50, **{f"dud{i}": [-1.0] * 50 for i in range(5)}, "w0": [0.5] * 10, "w1": [-0.5] * 10}
    styles = {**{a: "scalp" for a in r if a != "w0" and a != "w1"}, "w0": "swing", "w1": "swing"}
    w = ex.thompson_shares(r, styles, ceilings={"dud0": 0.02}, seed=3)
    scalp = {a: v for a, v in w.items() if styles[a] == "scalp"}
    assert scalp["star"] == pytest.approx(0.30) and scalp["dud0"] == pytest.approx(0.02)
    assert all(v >= ex.FLOOR_SHARE - 1e-12 for a, v in scalp.items() if a != "dud0")
    assert all(v <= ex.CAP_SHARE + 1e-12 for v in w.values()) and sum(scalp.values()) <= 1 + 1e-12
    assert w["w0"] == pytest.approx(0.30) and w["w1"] == pytest.approx(0.30)    # two arms: capped, never raised
    ex.write_weights(db, T0.value, w, styles, broker="icm")
    got = ex.weights(db, list(scalp), broker="icm")
    assert all(ex.FLOOR_SHARE - 1e-12 <= v <= ex.CAP_SHARE + 1e-12 for a, v in got.items() if a != "dud0")
    assert ex.weights(db, list(scalp), broker="icm", ceilings={"star": 0.1})["star"] == pytest.approx(0.1)
    for n in (2, 3, 6, 12):
        eq = ex.equal_shares([f"a{i}" for i in range(n)])
        assert all(ex.FLOOR_SHARE <= v <= ex.CAP_SHARE for v in eq.values()) and sum(eq.values()) <= 1 + 1e-12


def test_corrupt_or_missing_weights_give_equal_shares(tmp_path):
    db = _db(tmp_path)
    arms = ["a", "b", "c", "d"]
    st = {a: "scalp" for a in [*arms, "z"]}
    eq = {a: 0.25 for a in arms}
    assert ex.weights(db, arms, broker="icm") == eq                                      # no run yet
    ex.write_weights(db, 1, {"a": 0.3, "b": 0.3, "c": 0.3, "d": 0.3}, st, broker="icm")  # sums above 1
    assert ex.weights(db, arms, broker="icm") == eq
    ex.write_weights(db, 2, {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}, st, broker="icm")  # a share above the cap
    assert ex.weights(db, arms, broker="icm") == eq
    ex.write_weights(db, 3, {"a": 0.3, "b": 0.3, "c": 0.3}, st, broker="icm")  # an arm the run never scored
    assert ex.weights(db, arms, broker="icm") == eq
    ex.write_weights(db, 4, {"a": 0.3, "b": 0.3, "c": 0.3, "d": 0.01}, st, broker="icm")  # below the floor
    assert ex.weights(db, arms, broker="icm") == eq
    ex.write_weights(db, 5, {"a": 0.3, "b": 0.3, "c": 0.3, "d": 0.1}, {**st, "d": "swing"},
                     broker="icm")                                     # styles mixed
    assert ex.weights(db, arms, broker="icm") == eq
    ex.write_weights(db, 6, {"a": 0.3, "b": 0.3, "c": 0.3, "d": 0.1}, st, broker="icm")
    assert ex.weights(db, arms, broker="icm") == {"a": 0.3, "b": 0.3, "c": 0.3, "d": 0.1}
    now = pd.Timestamp(6, unit="ns", tz="UTC") + ex.MAX_WEIGHTS_AGE + pd.Timedelta(seconds=1)
    assert ex.weights(db, arms, broker="icm", now=now) == eq                             # stale
    with db.write() as conn:
        conn.execute("DROP TABLE exploration_broker_weights")
    assert ex.weights(db, arms, broker="icm") == eq
    garbage = tmp_path / "g"
    garbage.mkdir()
    (garbage / "aux.db").write_bytes(b"not a sqlite file" * 100)
    fake = Database.__new__(Database)                                     # a Database over an unreadable file
    fake.path, fake.kind, fake.busy_timeout_ms = garbage / "aux.db", "aux", 100
    assert ex.weights(fake, arms, broker="icm") == eq
    assert ex.weights(db, [], broker="icm") == {}


# ---------------------------------------------------------------------------------------------- isolation
def test_closed_exploration_trade_never_reaches_agent_trades_closed_trades_fills_phase_gate(tmp_path):
    db = _db(tmp_path)
    for i in range(3):
        _cand(db, f"c{i}", decision=T0 + pd.Timedelta(hours=i))
        ex.record_fill(db, candidate_id=f"c{i}", entry_utc=T0 + pd.Timedelta(hours=i),
                       exit_utc=T0 + pd.Timedelta(hours=i, minutes=5), entry_price=2000.0, exit_price=2002.0,
                       cost_per_oz=0.1, censored=False)
    assert ex.read_obs(db)["label_fill_r"].notna().all()
    book = ShadowBook(tmp_path, evidence_start=None)
    assert book.books == {} and agent_trades(book) == {}
    assert load_closed_trades(tmp_path) == ([], None)
    assert Store(tmp_path / "store").read("fills").empty
    assert cost_probe.probes(tmp_path) == []                              # the fills ledger is L2's to write
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not (tmp_path / "phase_state.json").exists() and phase_record(tmp_path) == phase_record(empty)
    src = Path(ex.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"ShadowBook", "append_closed_trade", "agent_trades", "record_evidence", "Store",
                        "cost_probes", "LEDGER"}


# ---------------------------------------------------------------------------------------------- nightly
def test_learn_allocates_strategy_arms_per_style_never_controls_and_retrains_when_due(tmp_path, monkeypatch):
    db = _db(tmp_path)
    for i in range(40):
        d = T0 + pd.Timedelta(hours=3 * i)
        for arm, r in (("S1a", 0.5), ("S1b", -0.5), ("S5a", 0.0), ("S5b", 0.1), ("S0", 0.0)):
            cid = f"{arm}-{i}"
            _cand(db, cid, arm=arm, decision=d, is_control=arm == "S0",
                  features=_snap("scalp", float(i % 7)), avail=d)
            _label(db, cid, r + (0.3 if i % 2 else -0.3))
    monkeypatch.setattr(ex, "RETRAIN_MIN_NEW", 50)
    monkeypatch.setattr(ex, "RETRAIN_MIN_TOTAL", 100)
    monkeypatch.setattr(ex, "H62_PARAMS", {**ex.H62_PARAMS, "min_child_samples": 5})
    as_of = pd.Timestamp("2026-10-11 23:00", tz="UTC")                  # a Sunday: retrain day
    rep = ex.learn(db, broker="icm", as_of=as_of, arms={"S9": "scalp"}, controls=["S0"], seed=1, retrain=True,
                   models_dir=tmp_path / "models")
    w = rep["weights"]
    assert set(w) == {"S1a", "S1b", "S5a", "S5b", "S9"} and "S0" not in w
    assert w["S1a"] == pytest.approx(0.30) and all(ex.FLOOR_SHARE - 1e-12 <= v <= 0.30 + 1e-12 for v in w.values())
    assert ex.weights(db, list(w), broker="icm", now=as_of) == w
    m = rep["models"]["scalp"]
    assert m["version"] == "h62-icm-scalp-20261009T2100" and rep["models"]["swing"]["due"] is False
    with db.connection() as conn:
        path, sha, act = conn.execute("SELECT path, sha256, activation_ns FROM exploration_models").fetchone()
    import hashlib
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha
    assert act == pd.Timestamp("2026-10-18 22:00", tz="UTC").value
    # every Sunday fit is a shadow: nothing is served (champion zero) until one is marked champion
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM exploration_models").fetchone()[0] == "shadow"
    late = pd.Timestamp("2026-10-20", tz="UTC")
    assert ex.serving_model(db, "scalp", late, broker="icm") is None


def test_the_serving_read_returns_only_an_active_champion_with_a_matching_digest(tmp_path):
    db = _db(tmp_path)
    fit: dict[str, Any] = {"style": "scalp", "folds": [], "calibrator": None, "features": ["f0"], "per_arm": {}, "auc_net": None,
           "auc_gross": None, "n": 0}
    cutoff, act = ex.model_cutoff(pd.Timestamp("2026-10-11 01:00", tz="UTC"))
    v = ex.save_model(db, tmp_path / "models", fit, broker="icm", cutoff=cutoff, activation=act)
    assert ex.serving_model(db, "scalp", act + pd.Timedelta(days=1), broker="icm") is None          # shadow: champion zero
    with db.write() as conn:
        conn.execute("UPDATE exploration_models SET status = 'champion' WHERE model_version = ?", (v,))
    assert ex.serving_model(db, "scalp", act - pd.Timedelta(minutes=1), broker="icm") is None       # not active yet
    got = ex.serving_model(db, "scalp", act, broker="icm")
    assert got is not None and got["model_version"] == v and got["features"] == ["f0"]
    assert ex.serving_model(db, "swing", act, broker="icm") is None
    with db.connection() as conn:
        path = conn.execute("SELECT path FROM exploration_models").fetchone()[0]
    Path(path).write_bytes(b"tampered")
    assert ex.serving_model(db, "scalp", act, broker="icm") is None                                  # digest mismatch
    with pytest.raises(sqlite3.IntegrityError):
        with db.write() as conn:
            conn.execute("UPDATE exploration_models SET status = 'live'")


@pytest.mark.parametrize("broker", ["icm\n", "ICM", "icm/../x", "", "icm vantage"])
def test_a_broker_key_must_be_a_lowercase_slug_in_full_so_no_trailing_newline_or_path_part_names_a_model(tmp_path,
                                                                                                          broker):
    """The broker key is a path segment (models/h62/<broker>/) and the key evidence is read under: the whole string is
    the slug, never a prefix of it (`re.match` with `$` let "icm\\n" through)."""
    fit: dict[str, Any] = {"style": "scalp", "folds": [], "calibrator": None, "features": ["f0"], "per_arm": {},
                           "auc_net": None, "auc_gross": None, "n": 0}
    cutoff, act = ex.model_cutoff(pd.Timestamp("2026-10-11 01:00", tz="UTC"))
    with pytest.raises(ValueError, match="lowercase slug"):
        ex.save_model(_db(tmp_path), tmp_path / "models", fit, broker=broker, cutoff=cutoff, activation=act)
    assert not (tmp_path / "models").exists()


def test_learn_with_no_candidates_writes_nothing(tmp_path):
    db = _db(tmp_path)
    assert ex.learn(db, broker="icm", as_of=T0, seed=1) == {"arms": 0}
    assert ex.weights(db, ["a", "b", "c", "d"], broker="icm") == {a: 0.25 for a in "abcd"}


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


def test_exploration_arms_reads_the_accounts_block_by_attribute():
    arm = SimpleNamespace
    acc = SimpleNamespace(exploration=SimpleNamespace(arms=(
        arm(arm_id="S1a", style="scalp", control_share=None, max_share=0.2),
        arm(arm_id="S0", style="scalp", control_share=0.1))))
    ctx = SimpleNamespace(accounts=[acc, SimpleNamespace()])
    assert jobs.exploration_arms(ctx) == ({"S1a": "scalp"}, ["S0"], {"S1a": 0.2})  # type: ignore[arg-type]


@pytest.mark.integration
def test_exploration_learn_labels_candidates_from_the_accounts_own_1m_bars(tmp_path):
    from goldbot.config import load_settings
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.research.registry import TrialRegistry

    store = Store(tmp_path / "data")
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0)] * 120).assign(spread=0.2, tick_count=10)
    store.append("bars_1m", bars, source="icm-probe")
    ctx = jobs.JobContext(settings=load_settings(), store=store, state_dir=tmp_path, accounts=[],
                          models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                          population=Population(tmp_path / "population.json"))
    db = _db(tmp_path)
    _cand(db, "a", arm="S1a")
    _cand(db, "b", arm="S1b", taken=False, mu=0.3)
    rep = jobs.exploration_learn(ctx, T0 + pd.Timedelta(hours=1))
    assert rep["labelled"] == {"icm-probe": 2} and rep["cost_model"] == {"icm": "settings-priors:icm"}
    assert set(rep["brokers"]["icm"]["weights"]) == {"S1a", "S1b"}
    lab = ex.read_obs(db)["label_sim_r"]
    assert lab.notna().all() and (lab < 0).all()                         # a flat market loses its costs


@pytest.mark.integration
def test_every_v1_column_is_built_by_the_served_decision_frame_at_the_frozen_feature_version():
    from goldbot.data.quality import check_bars
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.features.mtf import TF_LABEL, context_tfs
    from goldbot.research.pipeline import build_decision_frame

    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2025-01-01", "2025-04-01", ticks_per_minute=1, seed=5)))
    for style, tf in (("scalp", "1m"), ("intraday", "15m"), ("swing", "4h")):
        dec = b1.iloc[-3000:] if tf == "1m" else resample_bars(b1, tf)
        _, X = build_decision_frame(dec.reset_index(drop=True),
                                    {TF_LABEL[t]: resample_bars(b1, t) for t in context_tfs(tf)})
        assert X.attrs["feature_version"] == ex.FEATURE_VERSION_V1
        assert [c for c in ex.FEATURE_LISTS[ex.STYLE_FEATURE_LIST[style]] if c not in X.columns] == [], style


def test_the_replay_enters_at_the_open_of_the_minute_holding_the_send_delay():
    """Live == history (SRE 2026-10-27): a quarter-hour scalp order goes out a few seconds after the close, inside the
    first minute, so the replay's entry is that minute's open; only a delay past the minute would move it."""
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0), (2004.0, 2004.0, 2004.0)] + [(2004.0, 2004.0, 2004.0)] * 13)
    kw: dict[str, Any] = dict(side=1, decision_utc=T0, stop_dist=20.0, target_dist=20.0, max_hold_s=600, costs=COSTS)
    now = ex.replay_label(bars, **kw)
    soon = ex.replay_label(bars, entry_delay_s=7.0, **kw)
    late = ex.replay_label(bars, entry_delay_s=61.0, **kw)
    assert now is not None and soon == now
    assert late is not None and late.mid_r == pytest.approx(0.0) and now.mid_r == pytest.approx(4.0 / 20.0)


def test_label_candidates_replays_each_candidate_with_its_arms_send_delay(tmp_path, monkeypatch):
    """The labels apply the delay the engine sends with (`arms.send_delay_s` of the candidate's arm, timeframe and
    decision close), so a delay past the minute would move the label's entry here as it moves the order."""
    from goldbot.research import arms as A
    db = _db(tmp_path)
    _cand(db, "qh", stop=20.0, target=20.0)
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0), (2004.0, 2004.0, 2004.0)] + [(2004.0, 2004.0, 2004.0)] * 40)
    seen: list[tuple[str, str, pd.Timestamp]] = []

    def delay(arm_id: str, tf: str, close: pd.Timestamp) -> float:
        seen.append((arm_id, tf, close))
        return 61.0
    monkeypatch.setattr(A, "send_delay_s", delay)
    assert ex.label_candidates(db, bars, COSTS, cutoff=T0 + pd.Timedelta(minutes=30)) == 1
    assert seen == [("S1a", "1m", T0)]
    assert ex.read_obs(db).set_index("candidate_id").loc["qh", "label_mid_r"] == pytest.approx(0.0)

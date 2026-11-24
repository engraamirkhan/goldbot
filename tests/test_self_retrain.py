"""Weekly self-retrain of the per-arm meta-label filter (goldbot/research/self_retrain.py, spec sr-v2;
docs/research/prereg-2026-11-24-self-retrain.md). Synthetic aux.db: candidates, r1 and r2 replay labels and snapshots
written the way the nightly jobs write them; the gate and the null rate on pure frames."""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to
from goldbot.research import exploration_data as ex
from goldbot.research import self_retrain as sr

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold", "_test_arms_prenamed")
PRENAMED_REAL = sr.PRENAMED                          # the card's list, before the fixture adds the made-up test arms


@pytest.fixture
def _test_arms_prenamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The made-up arm ids of these tests are judged like the card's pre-named arms."""
    monkeypatch.setattr(sr, "PRENAMED", (*PRENAMED_REAL, "A", "N", "B", *(f"A{i}" for i in range(10))))
START = pd.Timestamp("2026-10-26 08:00", tz="UTC")
DAYS = 40
PER_DAY = 16


def _write(db: Database, *, arm: str, day: int, k: int, r: float, x: dict[str, float], side: int = 1,
           is_control: bool = False, exit_after: pd.Timedelta = pd.Timedelta(hours=2), pit: str = "written",
           start: pd.Timestamp = START, hold_s: int = 7200) -> str:
    at = start + pd.Timedelta(days=day, minutes=30 * k)
    cid = f"{arm}-{day}-{k}"
    ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker="icm", arm_id=arm, family="fam",
                        style="intraday", tf="15m", side=side, decision_utc=at, stop_dist=3.0, target_dist=3.0,
                        max_hold_s=hold_s, taken=False, mu=0.0, branch="blocked", u=0.5, blocked_reason="cap",
                        is_control=is_control)
    body = json.dumps(x, sort_keys=True)
    sha = hashlib.sha256(f"f-test|{cid}|{body}".encode()).hexdigest()
    ext = int((at + exit_after).value)
    with db.write() as conn:
        conn.execute("UPDATE exploration_obs SET label_exit_ns = ?, label_sim_r = ?, label_mid_r = ?, "
                     "cost_model_version = 'costs:test', cost_effective_ns = ?, labelled_ns = ? WHERE candidate_id = ?",
                     (ext, r + 0.02, r + 0.1, int(start.value), ext, cid))
        conn.execute("INSERT INTO exploration_replay (candidate_id, replay_version, label_exit_ns, sim_r, mid_r, "
                     "barrier, entry_price, exit_price, entry_charge_usd, exit_charge_usd, cost_model_version, "
                     "cost_effective_ns, labelled_ns) VALUES (?, 'r2', ?, ?, ?, 'target', 2000, 2003, 0.2, 0.2, "
                     "'r2:test', ?, ?)", (cid, ext, r, r + 0.1, int(start.value), ext))
        conn.execute("INSERT INTO exploration_feature_vectors (sha256, feature_version, n_columns, features, "
                     "recorded_ns) VALUES (?, 'f-test', ?, ?, 0)", (sha, len(x), body))
        conn.execute("INSERT INTO exploration_feature_snapshots (candidate_id, status, vector_sha256, decision_ns, "
                     "available_utc_max_ns, bars_as_of_ns, pit, nominal_minutes, recorded_ns) VALUES "
                     "(?, 'ok', ?, ?, ?, ?, ?, ?, 0)", (cid, sha, int(at.value), int(at.value), int(at.value), pit,
                                                        0 if pit == "written" else 5))
    return cid


def _feats(rng: np.random.Generator) -> dict[str, float]:
    return {c: float(rng.normal()) for c in sr.FEATURES}


def _signal_r(x: dict[str, float]) -> float:
    return 1.0 if x["em_atr_cost"] > -0.4 else -1.0


def _populate(db: Database, *, day_from: int = 0, days: int = DAYS, seed: int = 1, signal: bool = True,
              arms: tuple[str, ...] = ("A", "N")) -> None:
    """Arm A: net R driven by em_atr_cost (when `signal`); arm N: noise; control C: random, -0.1 R."""
    rng = np.random.default_rng(seed + day_from)
    for day in range(day_from, day_from + days):
        for k in range(PER_DAY):
            for arm in arms:
                x = _feats(rng)
                r = _signal_r(x) if (arm == "A" and signal) else float(rng.choice([1.0, -1.0]))
                _write(db, arm=arm, day=day, k=k, r=r - 0.1, x=x)
            _write(db, arm="C", day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng), is_control=True)


def _run(db: Database, *, days: int = DAYS, serve: bool = True, holdout: Any = None,
         rules: dict[str, dict[str, Any]] | None = None) -> dict:
    cutoff = START + pd.Timedelta(days=days + 1)
    return sr.run_broker(db, broker="icm", slot=cutoff + pd.Timedelta(days=2), cutoff=cutoff,
                         activation=cutoff + pd.Timedelta(days=2), holdout=holdout, serve=serve, named_controls={},
                         rules=rules)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _models(db: Database) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    with db.connection() as conn:
        for arm, v, st in conn.execute("SELECT arm_id, model_version, status FROM self_retrain_models "
                                       "ORDER BY cutoff_ns, model_version"):
            out.setdefault(arm, []).append((v, st))
    return out


def _detail(db: Database) -> dict[str, Any]:
    with db.connection() as conn:
        return json.loads(conn.execute("SELECT detail FROM self_retrain_runs ORDER BY cutoff_ns DESC").fetchone()[0])


# ---------------------------------------------------------------------------------------------- pure frames
def _frame(rng: np.random.Generator, *, arms: int, days: int, per_day: int, ctrl_per_day: int,
           day_sd: float = 0.6, sd: float = 1.2, start: pd.Timestamp = START) -> pd.DataFrame:
    """A null broker: every arm's and the control's net R = a shared day effect + noise (total sd `sd`) - 0.1."""
    eps_sd = float(np.sqrt(sd ** 2 - day_sd ** 2))
    rows = []
    for d in range(days):
        eff = rng.normal(0, day_sd)
        for a in [*(f"A{i}" for i in range(arms)), "CTRL"]:
            for k in range(ctrl_per_day if a == "CTRL" else per_day):
                at = start + pd.Timedelta(days=d, minutes=20 * k)
                r = eff + rng.normal(0, eps_sd) - 0.1
                rows.append({"candidate_id": f"{a}-{d}-{k}", "arm_id": a, "style": "intraday",
                             "is_control": a == "CTRL", "decision_utc": at, "label_exit_utc": at + pd.Timedelta(hours=1),
                             "session_day": ex.session_day(at), "side": int(rng.choice([1, -1])), "max_hold_s": 3600,
                             "label_sim_r": r, "label_mid_r": r + 0.1, "label_r1": r, "stop_dist": 3.0,
                             "tf": "15m", **_feats(rng)})
    return pd.DataFrame(rows)


def _passing_read(broker: str, p: float, **extra: Any) -> dict[str, Any]:
    m = {"n_oos": 400, "n_kept": 260, "n_days": 20, "n_control": 200, "n_rows": 800, "edge_vs_nomodel": 0.2,
         "edge_vs_control": 0.3, "edge_gross": 0.2, "mean_kept": 0.1, "p_nomodel": p, "p_control": p}
    m.update(extra.pop("metrics", {}))
    arm = {"metrics": m, "model": object(), "active": None, "forward": None,
           "first_pass": {"model_version": "x"}, "confirm": None, **extra}
    return {"broker": broker, "arms": {"A": arm}, "counts": {}, "active": {}}


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


def test_the_pre_registered_constants_are_the_cards():
    """docs/research/prereg-2026-11-24-self-retrain.md sections 2-5: a change is a new SPEC_VERSION and card."""
    assert sr.SPEC_VERSION == "sr-v2" and len(sr.FEATURES) == 39 and len(sr.SIGNED) == 11
    assert (sr.C_REG, sr.DROP_SHARE, sr.N_BLOCKS, sr.EMBARGO, sr.MIN_TRAIN, sr.MAX_SEND_DELAY) == (
        0.1, 1 / 3, 4, pd.Timedelta(days=1), 100, pd.Timedelta(seconds=60))
    assert (sr.MIN_OOS, sr.MIN_KEPT, sr.MIN_OOS_DAYS, sr.MIN_CONTROL, sr.MIN_EDGE_R, sr.ALPHA, sr.BOOT, sr.SEED) == (
        200, 100, 10, 50, 0.05, 0.10, 10_000, 20261124)
    assert (sr.DEMOTE_EDGE_R, sr.MIN_FORWARD, sr.MIN_CONFIRM, sr.MAX_JUDGED, sr.STOP_MATCH) == (0.0, 60, 30, 10, 0.10)
    assert PRENAMED_REAL == ("RBR15", "Z3D", "Z1P", "Z9D", "Z10D", "S1A", "S1B", "F1A", "Z2P", "Z5P")
    assert not {"RBR1H", "RBR4H", "G1A", "RB15"} & set(PRENAMED_REAL) and sr.HISTORY_OF == {"RB15": "RBR15"}
    assert sr.NOMINAL_FROM == pd.Timestamp("2026-11-15", tz="UTC")
    assert (Path(__file__).resolve().parents[1] / sr.PREREG).exists()


# ---------------------------------------------------------------------------------------------- gate (store)
def test_a_predictive_arm_becomes_active_only_after_two_consecutive_passes_and_noise_stays_shadow(db):
    _populate(db)
    out = _run(db)
    assert out["labels"] == DAYS * PER_DAY * 3 and out["with_features"] == out["labels"]
    assert out["features"] == 40 and out["weights"] == 2 * 41
    assert {a: v[-1][1] for a, v in _models(db).items()} == {"A": "shadow", "N": "shadow"}   # control: no model
    first = _detail(db)["decisions"]["A"]
    assert first["reason"].startswith("passed; awaiting") and first["edge_vs_nomodel"] >= sr.MIN_EDGE_R
    assert first["edge_r1"] is not None and first["boot_no_kept"] == 0
    _populate(db, day_from=DAYS, days=7)
    _run(db, days=DAYS + 7)
    assert {a: v[-1][1] for a, v in _models(db).items()} == {"A": "active", "N": "shadow"}
    second = _detail(db)["decisions"]["A"]
    assert second["confirm"]["n"] >= sr.MIN_CONFIRM and second["confirm"]["edge_vs_nomodel"] >= 0


def test_shuffled_labels_never_pass(db):
    rng = np.random.default_rng(5)
    for day in range(DAYS):
        for k in range(PER_DAY):
            for arm in ("A", "B", "N"):
                _write(db, arm=arm, day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng))
            _write(db, arm="C", day=day, k=k, r=float(rng.choice([1.0, -1.0])) - 0.1, x=_feats(rng), is_control=True)
    _run(db)
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM self_retrain_models WHERE gate_pass = 1").fetchone()[0] == 0


def test_too_few_candidates_is_insufficient_and_stays_shadow(db):
    _populate(db, days=12)
    _run(db, days=12)
    d = _detail(db)["decisions"]["A"]
    assert d["decision"] == "shadow" and d["reason"].startswith("insufficient")


def test_serving_switched_off_keeps_a_twice_passing_model_in_shadow(db):
    _populate(db)
    _run(db, serve=False)
    _populate(db, day_from=DAYS, days=7)
    _run(db, days=DAYS + 7, serve=False)
    assert _models(db)["A"][-1][1] == "shadow"


def test_an_active_model_whose_edge_decays_is_demoted_and_nothing_is_left_active(db):
    _populate(db)
    _run(db)
    _populate(db, day_from=DAYS, days=7)
    _run(db, days=DAYS + 7)
    assert _models(db)["A"][-1][1] == "active"
    _populate(db, day_from=DAYS + 7, days=60, signal=False, arms=("A",))
    _run(db, days=DAYS + 67)
    hist = _models(db)["A"]
    assert hist[1][1] == "demoted" and hist[-1][1] == "shadow"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM self_retrain_models WHERE status = 'active'").fetchone()[0] == 0


def test_an_active_model_whose_arm_has_no_rows_in_a_run_is_demoted_and_health_counts_the_table(db, monkeypatch):
    _populate(db)
    _run(db)
    _populate(db, day_from=DAYS, days=7)
    _run(db, days=DAYS + 7)
    with db.connection() as conn:
        assert sr.latest_runs(conn)[0]["n_active"] == 1
    monkeypatch.setattr(sr, "PRENAMED", ("N",))           # A leaves the list and is retired: no rows this run
    _run(db, days=DAYS + 8, rules={"A": {"retired_day": "2026-12-01", "control_mode": "broker", "role": "strategy"}})
    assert _models(db)["A"][-1][1] == "demoted"
    assert _detail(db)["decisions"]["A"]["reason"] == "no rows in run"
    with db.connection() as conn:
        assert sr.latest_runs(conn)[0]["n_active"] == 0


# ---------------------------------------------------------------------------------------------- gate (pure, R1-R4, R7)
def test_holm_runs_over_the_whole_run_so_two_brokers_at_p_006_both_fail_and_one_alone_passes():
    both = sr.decide([_passing_read("icm", 0.06), _passing_read("vantage", 0.06)], serve=True)
    assert not any(d["gate_pass"] for d in both.values())
    assert all(d["p_holm"] == pytest.approx(0.12) for d in both.values())
    alone = sr.decide([_passing_read("icm", 0.06)], serve=True)
    assert alone[("icm", "A")]["gate_pass"] and alone[("icm", "A")]["status"] == "active"


def test_the_gate_reads_the_larger_of_the_two_p_values():
    d = sr.decide([_passing_read("icm", 0.01, metrics={"p_control": 0.2})], serve=True)[("icm", "A")]
    assert not d["gate_pass"] and d["p_holm"] == pytest.approx(0.2)


def test_a_bootstrap_p_is_never_zero_and_a_draw_with_no_kept_row_counts_against():
    assert sr._p_value(np.full(10_000, 0.3)) == pytest.approx(1 / 10_001)
    assert sr._p_value(np.array([np.nan, 0.3, 0.3])) == pytest.approx(2 / 4)


def test_a_filter_that_only_loses_less_fails_the_gate():
    """Quant S2: arm -0.30, control -0.20, kept -0.12 R: both edges clear +0.05 R, but kept candidates still lose."""
    rd = _passing_read("icm", 0.001, metrics={"mean_all": -0.30, "mean_control": -0.20, "mean_kept": -0.12,
                                              "edge_vs_nomodel": 0.18, "edge_vs_control": 0.08})
    d = sr.decide([rd], serve=True)[("icm", "A")]
    assert not d["gate_pass"] and "kept mean -0.12 R <= 0" in d["reason"]


def test_a_held_run_activates_nothing(db):
    """Quant (a): a broker read failed, so the run's Holm family is incomplete: a passer stays shadow."""
    _populate(db)
    _run(db)
    _populate(db, day_from=DAYS, days=7)
    cutoff = START + pd.Timedelta(days=DAYS + 8)
    read = sr.compute_broker(db, broker="icm", cutoff=cutoff, holdout=None, named_controls={}, rules={})
    dec = sr.decide([read], serve=True)
    assert dec[("icm", "A")]["status"] == "active"
    sr.apply(db, read, dec, slot=cutoff, cutoff=cutoff, activation=cutoff + pd.Timedelta(days=2), k_ok=True,
             hold="broker read failed this run (vantage)")
    assert _models(db)["A"][-1][1] == "shadow"


def test_a_pass_needs_the_run_exactly_one_week_before(db):
    """Quant (b): two passes a fortnight apart are not consecutive."""
    _populate(db)
    _run(db)
    _populate(db, day_from=DAYS, days=14)
    _run(db, days=DAYS + 14)
    assert _models(db)["A"][-1][1] == "shadow"
    assert _detail(db)["decisions"]["A"]["reason"].startswith("passed; awaiting")


def test_a_negative_gross_edge_fails_the_gate():
    d = sr.decide([_passing_read("icm", 0.001, metrics={"edge_gross": -0.01})], serve=True)[("icm", "A")]
    assert not d["gate_pass"] and "gross edge" in d["reason"]


def test_a_first_pass_whose_forward_edge_turned_negative_never_activates():
    rd = _passing_read("icm", 0.001, confirm={"n": 40, "n_kept": 25, "edge_vs_nomodel": -0.02})
    d = sr.decide([rd], serve=True)[("icm", "A")]
    assert d["gate_pass"] and d["status"] == "shadow"
    rd = _passing_read("icm", 0.001, confirm={"n": 20, "n_kept": 12, "edge_vs_nomodel": -0.5})
    assert sr.decide([rd], serve=True)[("icm", "A")]["status"] == "active"      # under MIN_CONFIRM: not read


def test_a_decayed_active_model_is_demoted_first_and_forces_the_new_passer_to_shadow():
    rd = _passing_read("icm", 0.001, active={"model_version": "old"},
                       forward={"n": 80, "n_kept": 50, "edge_vs_nomodel": -0.05})
    d = sr.decide([rd], serve=True)[("icm", "A")]
    assert d["status"] == "shadow" and d["reason"].startswith("forced shadow")
    assert d["previous"][0] == "demoted"


def test_at_most_max_judged_pairs_are_judged_ranked_by_candidate_count():
    reads = [_passing_read(f"b{i:02d}", 0.001, metrics={"n_rows": 1000 + i}) for i in range(12)]
    d = sr.decide(reads, serve=True)
    judged = sorted(k[0] for k, v in d.items() if v["judged"])
    assert judged == [f"b{i:02d}" for i in range(2, 12)]
    assert d[("b00", "A")]["reason"].startswith("not judged")


# ---------------------------------------------------------------------------------------------- point in time
def test_walk_forward_trains_only_on_horizons_and_labels_ended_before_each_test_block(monkeypatch):
    tab = _frame(np.random.default_rng(3), arms=1, days=DAYS, per_day=PER_DAY, ctrl_per_day=0)
    tab = tab[tab["arm_id"] == "A0"].reset_index(drop=True)
    tab["label_exit_utc"] = tab["decision_utc"] + pd.Timedelta(days=3)
    seen: list[pd.Timestamp] = []
    real_fit = sr.fit_logit

    def spy(X: pd.DataFrame, y: np.ndarray) -> sr.LogitModel:
        seen.append(pd.Timestamp(tab["label_exit_utc"].to_numpy()[X.index.to_numpy(dtype=int)].max()))
        return real_fit(X, y)
    monkeypatch.setattr(sr, "fit_logit", spy)
    oos = sr.walk_forward(tab)
    starts = oos.groupby("block")["decision_utc"].min().sort_index().tolist()
    assert len(seen) == sr.N_BLOCKS == len(starts)
    assert all(s < t0 for s, t0 in zip(seen, starts, strict=True))


def test_a_fold_is_invariant_to_every_label_resolved_or_decided_at_or_after_its_start():
    tab = _frame(np.random.default_rng(4), arms=1, days=DAYS, per_day=PER_DAY, ctrl_per_day=0)
    tab = tab[tab["arm_id"] == "A0"].reset_index(drop=True)
    tab["label_exit_utc"] = tab["decision_utc"] + pd.Timedelta(hours=5)
    base = sr.walk_forward(tab)
    for block in range(sr.N_BLOCKS):
        t0 = base.loc[base["block"] == block, "decision_utc"].min()
        later = (tab["decision_utc"] >= t0) | (tab["label_exit_utc"] >= t0)
        pert = tab.copy()
        pert.loc[later, "label_sim_r"] = -pert.loc[later, "label_sim_r"] * 3
        got = sr.walk_forward(pert)
        a, b = base[base["block"] == block], got[got["block"] == block]
        assert np.allclose(a["p"].to_numpy(), b["p"].to_numpy()) and (a["kept"].to_numpy() == b["kept"].to_numpy()).all()


def test_a_label_resolved_before_the_cutoff_whose_horizon_ends_after_it_is_never_read(db):
    _populate(db, days=3)
    cutoff = START + pd.Timedelta(days=4)
    cid = _write(db, arm="D1", day=3, k=0, r=1.0, x=_feats(np.random.default_rng(0)),
                 exit_after=pd.Timedelta(minutes=30), hold_s=86_400, start=START + pd.Timedelta(hours=8))
    rows, counts = sr.load_rows(db, broker="icm", cutoff=cutoff, holdout=None)
    assert cid not in set(rows["candidate_id"]) and counts["open_horizon"] >= 1


def test_a_label_exiting_after_the_cutoff_is_excluded_as_unresolved(db):
    _populate(db, days=3)
    cid = _write(db, arm="A", day=2, k=99, r=5.0, x=_feats(np.random.default_rng(0)), exit_after=pd.Timedelta(days=9))
    rows, _ = sr.load_rows(db, broker="icm", cutoff=START + pd.Timedelta(days=4), holdout=None)
    assert cid not in set(rows["candidate_id"])


def test_nominal_snapshots_are_admitted_only_from_nominal_from_and_only_when_audited_clean(db):
    late = sr.NOMINAL_FROM + pd.Timedelta(hours=8)
    early_id = _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal")
    late_id = _write(db, arm="A", day=0, k=1, r=1.0, x=_feats(np.random.default_rng(1)), pit="nominal", start=late)
    cut = late + pd.Timedelta(days=2)
    rows, counts = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None)
    assert rows.empty and counts["nominal_not_audited_clean"] == 1          # quant S5: no audit, no nominal row
    rows, counts = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, nominal_ok={late_id, early_id})
    assert set(rows["candidate_id"]) == {late_id} and early_id not in set(rows["candidate_id"])
    assert counts["nominal"] == 1 and counts["no_snapshot"] == 1


def test_the_audit_excludes_a_nominal_snapshot_with_any_unstamped_minute_in_its_last_day(db, monkeypatch):
    from goldbot.research import snapshot_audit as sa
    at = sr.NOMINAL_FROM + pd.Timedelta(days=2)
    a = _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal", start=at)
    b = _write(db, arm="A", day=0, k=1, r=1.0, x=_feats(np.random.default_rng(1)), pit="nominal", start=at)
    ts = pd.date_range(at - pd.Timedelta(days=2), at + pd.Timedelta(hours=1), freq="1min", tz="UTC")
    written = np.ones(len(ts), dtype=bool)
    written[(ts > at + pd.Timedelta(minutes=5)) & (ts <= at + pd.Timedelta(minutes=20))] = False   # only b reads it
    written[ts < at - pd.Timedelta(days=1, hours=1)] = False                                        # outside a's day
    v = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "written": written, "rank": 0})
    monkeypatch.setattr(sa, "minute_versions", lambda store, **kw: v)
    got = sa.audit(db, object(), since=sr.NOMINAL_FROM)
    assert got["clean"] == {a} and got["excluded"] == {b} and got["counts"]["nominal"] == 2


def test_every_row_of_a_capped_book_day_is_dropped(db):
    _populate(db, days=3)
    day = (START + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    cut = START + pd.Timedelta(days=4)
    rows, counts = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, capped={("icm-probe", day)})
    days = pd.to_datetime(rows["decision_utc"], utc=True).dt.strftime("%Y-%m-%d")
    assert day not in set(days) and counts["capped_day"] == PER_DAY * 3
    journal = pd.DataFrame({"ts_utc": [START + pd.Timedelta(days=1, hours=9)], "action": [sr.CAP_ACTION],
                            "detail": [json.dumps({"day": day, "cap": 200})], "agent_id": [None]})
    assert sr.capped_days(_Store(journal), {"icm-probe"}, start=START, end=cut) == {("icm-probe", day)}


def test_a_candidate_inside_the_sealed_holdout_fails_the_run(db):
    _populate(db, days=2)
    with pytest.raises(ValueError, match="holdout"):
        _run(db, days=2, holdout=(START - pd.Timedelta(days=1), START + pd.Timedelta(days=1)))


def test_retired_replay_only_arms_are_neither_judged_nor_baseline():
    """Quant C1 (review of #435): a retired arm that is NOT pre-named (S2A, retired 2026-10-30) trains nothing and is
    no judged arm; a retired control's rows after its retired_day are no baseline."""
    rules = sr.arm_rules()
    assert rules["S2A"]["retired_day"] is not None and rules["I0"]["role"] == "control"
    assert "S2A" not in PRENAMED_REAL
    rng = np.random.default_rng(8)
    f = _frame(rng, arms=1, days=30, per_day=10, ctrl_per_day=10, start=pd.Timestamp("2026-11-14 08:00", tz="UTC"))
    f.loc[f["arm_id"] == "A0", "arm_id"] = "S2A"
    f.loc[f["arm_id"] == "CTRL", "arm_id"] = "I0"
    assert int((f["arm_id"] == "S2A").sum()) == 300
    got = sr.read_arms(f, named_controls={}, rules=rules, cutoff=pd.Timestamp("2026-12-20", tz="UTC"), boot=200)
    assert "S2A" not in got
    f.loc[f["arm_id"] == "I0", "is_control"] = True
    f2 = pd.concat([f, f[f["arm_id"] == "S2A"].assign(arm_id="LIVE1", candidate_id=lambda d: "L" + d["candidate_id"])])
    ctrl = sr.control_rows(f2, "LIVE1", named={}, since=pd.Timestamp("2026-11-01", tz="UTC"), rules=rules)
    assert ctrl.empty                                          # I0 retired 2026-11-06: every row after it is out


def test_a_pre_named_retired_arm_is_judged_out_of_sample_only_after_its_retired_day():
    """PD + research-director 2026-11-24: S1A (retired 2026-11-13) is pre-named; its rows before the retirement were
    already read, so they only train; every OOS row is decided after its retired_day."""
    rules = sr.arm_rules()
    assert rules["S1A"]["retired_day"] == "2026-11-13" and "S1A" in PRENAMED_REAL
    f = _frame(np.random.default_rng(11), arms=1, days=40, per_day=20, ctrl_per_day=10,
               start=pd.Timestamp("2026-10-27 08:00", tz="UTC"))
    f.loc[f["arm_id"] == "A0", "arm_id"] = "S1A"
    f.loc[f["arm_id"] == "CTRL", "arm_id"] = "LIVECTRL"
    got = sr.read_arms(f, named_controls={"S1A": "LIVECTRL"}, rules=rules, cutoff=pd.Timestamp("2026-12-20", tz="UTC"),
                       boot=200)
    assert "S1A" in got and got["S1A"]["metrics"]["n_oos"] > 0
    oos = sr.walk_forward(f[f["arm_id"] == "S1A"], oos_from=pd.Timestamp("2026-11-14", tz="UTC"))
    assert (oos["decision_utc"] >= pd.Timestamp("2026-11-14", tz="UTC")).all()
    assert 0 in set(oos["block"])                  # the first block trains on the pre-retirement rows alone
    pre = f[(f["arm_id"] == "S1A") & (f["decision_utc"] < pd.Timestamp("2026-11-14", tz="UTC"))]
    assert not set(pre["candidate_id"]) & set(oos["candidate_id"])


def test_rb15_rows_are_rbr15_history_that_trains_and_is_never_out_of_sample():
    """RB15 is hard-stopped at its end_utc 2026-11-22; its successor RBR15 (PR #435) records the forward rows. RB15's
    rows train RBR15's filter only: every OOS row is RBR15's own and decided after the last RB15 row."""
    f = _frame(np.random.default_rng(13), arms=1, days=40, per_day=20, ctrl_per_day=10,
               start=pd.Timestamp("2026-10-20 08:00", tz="UTC"))
    a0 = f["arm_id"] == "A0"
    f.loc[a0 & (f["decision_utc"] < pd.Timestamp("2026-11-22", tz="UTC")), "arm_id"] = "RB15"
    f.loc[a0 & (f["decision_utc"] >= pd.Timestamp("2026-11-24", tz="UTC")), "arm_id"] = "RBR15"
    f = f[f["arm_id"] != "A0"]
    rules = {"RB15": {"retired_day": "2026-11-22", "control_mode": "broker", "role": "learning"}}   # RBR15 unknown yet
    got = sr.read_arms(f, named_controls={"RBR15": "CTRL"}, rules=rules,
                       cutoff=pd.Timestamp("2026-12-20", tz="UTC"), boot=200)
    assert set(got) == {"RBR15"}
    assert got["RBR15"]["metrics"]["n_rows"] == int(f["arm_id"].isin(["RB15", "RBR15"]).sum())
    own = f[f["arm_id"] == "RBR15"]
    assert 0 < got["RBR15"]["metrics"]["n_oos"] <= len(own)


def test_the_baseline_is_the_named_or_a_matched_stop_control_never_a_style_fallback():
    f = _frame(np.random.default_rng(12), arms=1, days=5, per_day=5, ctrl_per_day=5)
    since = START - pd.Timedelta(days=1)
    f.loc[f["arm_id"] == "CTRL", "stop_dist"] = 1.0                   # a small-stop control of the same style
    assert sr.control_rows(f, "A0", named={}, since=since).empty
    f.loc[f["arm_id"] == "CTRL", "stop_dist"] = 3.2                   # within 10 % of the arm's 3.0
    assert len(sr.control_rows(f, "A0", named={}, since=since)) == 25
    f.loc[f["arm_id"] == "CTRL", "tf"] = "1h"                          # another timeframe: no match
    assert sr.control_rows(f, "A0", named={}, since=since).empty


def test_the_stored_model_is_numbers_only_and_scores_the_same_after_a_round_trip(db):
    _populate(db)
    _run(db)
    with db.connection() as conn:
        m = sr.LogitModel.model_validate_json(conn.execute(
            "SELECT model FROM self_retrain_models WHERE arm_id = 'A'").fetchone()[0])
    X = sr.design(pd.DataFrame([{**_feats(np.random.default_rng(2)), "side": -1}]))
    assert np.allclose(m.score(X), sr.LogitModel.model_validate_json(m.model_dump_json()).score(X))


# ---------------------------------------------------------------------------------------------- the null rate (R9)
def _null_single(seed: int) -> tuple[bool, bool]:
    """Quant S4, m = 1: one broker, one pre-named arm with oos_from, 20 pre-retirement days (training only), 16 then 21
    post-retirement days at 13 rows a day, a named control at 13 a day; every label null (mean 0, day-clustered sd
    1.2 R: the strictest null for the kept-mean condition). (pass at 16 days, pass at 16 and at 21 days)."""
    rng = np.random.default_rng(seed)
    f = _frame(rng, arms=1, days=41, per_day=13, ctrl_per_day=13, start=pd.Timestamp("2026-10-23 08:00", tz="UTC"))
    f[["label_sim_r", "label_mid_r", "label_r1"]] += 0.1                       # mean 0 instead of -0.1
    f = f.replace({"arm_id": {"A0": "Z3D", "CTRL": "ZC"}})
    rules = {"Z3D": {"retired_day": "2026-11-12", "control_mode": "replay_only", "role": "learning"}}
    out = []
    for post_days in (16, 21):
        end = pd.Timestamp("2026-11-13", tz="UTC") + pd.Timedelta(days=post_days)
        fr = f[f["decision_utc"] < end]
        arms = sr.read_arms(fr, named_controls={"Z3D": "ZC"}, rules=rules, cutoff=end)
        out.append(any(d["gate_pass"] for d in sr.decide([{"broker": "b0", "counts": {}, "active": {},
                                                             "arms": arms}], serve=True).values()))
    return out[0], out[0] and out[1]


@pytest.mark.selftest  # minutes of Monte Carlo: run by selftest.yml, not the 20-min integration shards
def test_m1_null_single_run_pass_rate_is_at_most_ten_percent():
    """Quant S4: measured 2026-11-24 (card section 3)."""
    import multiprocessing as mp
    sims = 200
    with mp.get_context("fork").Pool(4) as pool:
        got = pool.map(_null_single, range(7000, 7000 + sims))
    assert sum(a for a, _ in got) / sims <= 0.10, got


def _null_run(seed: int) -> int:
    """One null run (4 brokers x 10 arms, 20 days, day-clustered noise sd 1.2 R): its number of gate passes."""
    rng = np.random.default_rng(seed)
    reads = []
    for b in range(4):
        f = _frame(rng, arms=10, days=20, per_day=25, ctrl_per_day=10)
        reads.append({"broker": f"b{b}", "counts": {}, "active": {},
                      "arms": sr.read_arms(f, named_controls={}, rules={}, cutoff=START + pd.Timedelta(days=30))})
    return sum(bool(d["gate_pass"]) for d in sr.decide(reads, serve=True).values())


@pytest.mark.selftest  # minutes of Monte Carlo: run by selftest.yml, not the 20-min integration shards
def test_under_the_null_a_run_passes_the_gate_at_most_twelve_percent_of_the_time():
    """Quant R9: ~200 null runs; measured 2026-11-24 on seeds 1000-1199: 0 runs with a pass (card section 3)."""
    import multiprocessing as mp
    sims = 200
    with mp.get_context("fork").Pool(4) as pool:
        got = pool.map(_null_run, range(20261124, 20261124 + sims))
    assert sum(g > 0 for g in got) / sims <= 0.12, got


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


class _Trials:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    def record(self, **kw: Any) -> dict[str, Any]:
        from goldbot.research.registry import config_hash
        if self.fail:
            raise PermissionError("read-only mirror")
        self.rows.append({**kw, "config_hash": config_hash(kw["config"])})
        return {"trial": len(self.rows)}

    def by_family(self, family: str) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["family"] == family]


def _ctx(tmp_path: Path, trials: _Trials, *, enabled: bool = True, clock: Any = None) -> Any:
    from goldbot.config import load_settings
    s = load_settings()
    s = s.model_copy(update={"research": s.research.model_copy(update={
        "self_retrain": s.research.self_retrain.model_copy(update={"enabled": enabled})})})
    return SimpleNamespace(settings=s, state_dir=tmp_path, accounts=[], trials=trials, clock=clock, store=_Store())


class _Store:
    """No journal rows and no minutes: no capped day, no nominal snapshot audited."""

    def __init__(self, decisions: pd.DataFrame | None = None) -> None:
        self.decisions = decisions

    def read(self, table: str, **_: Any) -> pd.DataFrame:
        return self.decisions if (table == "decisions" and self.decisions is not None) else pd.DataFrame()


def test_the_job_books_one_k_row_per_judged_pair_and_fails_closed_when_it_cannot(tmp_path, db):
    from goldbot.ops.jobs import self_retrain
    _populate(db)
    t1 = _Trials()
    slot1 = START + pd.Timedelta(days=DAYS + 6, hours=7)          # a Sunday 15:00 after the data
    out = self_retrain(_ctx(tmp_path, t1, clock=lambda: slot1), slot1)
    assert out["k_ok"] and out["n_judged"] == len(t1.rows) == out["k_rows"] >= 1
    assert all(r["results"]["n_judged"] == out["n_judged"] for r in t1.rows)
    _populate(db, day_from=DAYS, days=7)
    slot2 = slot1 + pd.Timedelta(days=7)
    out2 = self_retrain(_ctx(tmp_path, _Trials(fail=True), clock=lambda: slot2), slot2)
    assert out2["k_ok"] is False
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM self_retrain_models WHERE status = 'active'").fetchone()[0] == 0
    assert _detail(db)["k_ok"] is False


def test_a_rerun_of_the_same_slot_books_no_second_k_row(tmp_path, db):
    """Code review L4: K rows are keyed by the run's cutoff and the pair."""
    from goldbot.ops.jobs import self_retrain
    _populate(db)
    trials = _Trials()
    slot = START + pd.Timedelta(days=DAYS + 6, hours=7)
    self_retrain(_ctx(tmp_path, trials, clock=lambda: slot), slot)
    n = len(trials.rows)
    assert n >= 1
    self_retrain(_ctx(tmp_path, trials, clock=lambda: slot), slot)
    assert len(trials.rows) == n


def test_a_broker_whose_read_fails_has_its_active_models_demoted_and_the_run_holds(tmp_path, db, monkeypatch):
    """Code review L1 and quant (a): fail closed on a failed broker read."""
    from goldbot.ops.jobs import self_retrain
    _populate(db)
    _run(db)
    _populate(db, day_from=DAYS, days=7)
    _run(db, days=DAYS + 7)
    assert _models(db)["A"][-1][1] == "active"

    def boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("store unreadable")
    monkeypatch.setattr(sr, "compute_broker", boom)
    slot = pd.Timestamp("2027-01-03 15:00", tz="UTC")
    out = self_retrain(_ctx(tmp_path, _Trials(), clock=lambda: slot), slot)
    assert out["hold"] and out["brokers"]["icm"]["demoted"] == 1
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM self_retrain_models WHERE status = 'active'").fetchone()[0] == 0


def test_a_catch_up_run_activates_at_the_next_sunday_open_after_now(tmp_path, db):
    from goldbot.ops.jobs import self_retrain
    slot = pd.Timestamp("2026-11-22 15:00", tz="UTC")
    now = pd.Timestamp("2026-11-24 22:30", tz="UTC")
    out = self_retrain(_ctx(tmp_path, _Trials(), clock=lambda: now), slot)
    assert out["activation"] == pd.Timestamp("2026-11-29 22:00", tz="UTC").isoformat()


def test_switched_off_the_job_records_a_disabled_run_and_demotes_every_active_model(tmp_path, db):
    from goldbot.ops.jobs import self_retrain
    _populate(db)
    _run(db)
    _populate(db, day_from=DAYS, days=7)
    _run(db, days=DAYS + 7)
    slot = pd.Timestamp("2027-01-03 15:00", tz="UTC")
    out = self_retrain(_ctx(tmp_path, _Trials(), enabled=False, clock=lambda: slot), slot)
    assert out["enabled"] is False and out["demoted"] == 1
    with db.connection() as conn:
        runs = sr.latest_runs(conn)
    assert runs and runs[0]["status"] == "disabled"
    assert sr.health(runs, slot, enabled=False)[0] == "warn"


def test_a_model_row_a_newer_release_wrote_with_an_extra_field_still_reads():
    """AGENT_STANDARDS S12 / ADR 0010: a stored model with a field this release does not know is read, not refused."""
    m = sr.fit_logit(sr.design(_frame(np.random.default_rng(14), arms=1, days=8, per_day=20, ctrl_per_day=0)),
                     np.r_[np.zeros(80), np.ones(80)].astype(int))
    newer = json.dumps({**json.loads(m.model_dump_json()), "calibrator": {"kind": "platt"}})
    assert sr._model(newer) == m


def test_no_engine_or_execution_module_reads_the_self_retrain_tables():
    root = Path(__file__).resolve().parents[1] / "goldbot"
    for sub in ("engine", "execution", "risk"):
        for f in (root / sub).rglob("*.py"):
            text = f.read_text()
            assert "self_retrain" not in text, f
            ast.parse(text)

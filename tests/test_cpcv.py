"""M16: combinatorial purged cross-validation (6 groups, 2 test: 15 purged splits, 5 backtest paths), PBO, and CPCV
recorded as evidence on an existing trial, never as a new trial."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from goldbot.research import cpcv
from goldbot.research.registry import TrialRegistry, quarter_of

T0 = pd.Timestamp("2022-01-01", tz="UTC")
T1 = pd.Timestamp("2024-01-01", tz="UTC")


def _labels(n: int = 1500, seed: int = 0, max_hours: int = 96) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Candidates spread over two years, lives of 1..max_hours hours (many cross a group boundary); feature x carries
    the outcome with noise, z is noise."""
    rng = np.random.default_rng(seed)
    ts = pd.DatetimeIndex(np.sort(rng.uniform(T0.value, T1.value - 5 * 86400e9, n)).astype("int64"), tz="UTC")
    te = ts + pd.to_timedelta(rng.integers(1, max_hours + 1, n), unit="h")
    hit = rng.uniform(size=n) < 0.4
    labels = pd.DataFrame({"ts_utc": ts, "ts_exit": te, "target_hit": hit.astype(int),
                           "ret": np.where(hit, 0.002, -0.001), "risk": 0.001, "atr_sig": 1.0, "stop_atr": 1.0,
                           "target_atr": 2.0, "weight": 1.0})
    feats = pd.DataFrame({"x": hit + rng.normal(0, 0.6, n), "z": rng.normal(0, 1, n), "side": rng.choice([-1, 1], n)})
    return labels, feats


def test_there_are_exactly_15_splits_and_5_paths_covering_every_group_once_per_path():
    labels, _ = _labels()
    edges = cpcv.group_edges(T0, T1)
    splits = cpcv.cpcv_splits(labels, edges, purge_days=2, embargo_days=1)
    paths = cpcv.backtest_paths()
    assert len(edges) == 7 and len(splits) == 15 and len(paths) == 5
    assert len({sp.test_groups for sp in splits}) == 15 and all(len(sp.test_groups) == 2 for sp in splits)
    for path in paths:
        assert len(path) == 6                                           # one prediction source per group
        assert all(g in splits[k].test_groups for g, k in enumerate(path))
    # 15 splits x 2 test groups = 30 = 5 paths x 6 groups: every split's every test group lands in exactly one path
    used = sorted((k, g) for path in paths for g, k in enumerate(path))
    assert used == sorted((sp.k, g) for sp in splits for g in sp.test_groups)


def test_no_split_trains_on_a_test_row_or_on_a_label_whose_life_overlaps_one():
    labels, _ = _labels(max_hours=200)
    edges = cpcv.group_edges(T0, T1)
    groups = cpcv.assign_groups(labels, edges)
    for sp in cpcv.cpcv_splits(labels, edges, purge_days=2, embargo_days=1):
        assert not set(sp.train_idx) & set(sp.test_idx)
        assert not np.isin(groups[sp.train_idx], sp.test_groups).any()
        assert len(cpcv.overlaps(labels, sp)) == 0
        assert len(sp.train_idx) + len(sp.test_idx) < len(labels)      # the purge removed boundary rows


def test_purge_and_embargo_are_exact_at_the_group_boundaries():
    edges = cpcv.group_edges(T0, T0 + pd.Timedelta(days=60))          # six 10-day groups
    day = pd.Timedelta(days=1)
    rows = [  # (signal, exit) in days from T0; test group 2 = days 20..30
        (17.0, 17.5),   # 0 group 1, exits 2.5 d before the test group: kept (purge 2 d)
        (17.0, 18.5),   # 1 group 1, exits 1.5 d before it: inside the purge, dropped
        (19.0, 20.5),   # 2 group 1, life runs into the test group: dropped
        (25.0, 31.0),   # 3 test row whose life ends 1 d after the group's end
        (31.5, 31.8),   # 4 group 3, starts within 1 d (embargo) after that last test exit (31 + 1 = 32): dropped
        (32.5, 33.0),   # 5 group 3, starts after the embargo: kept
        (30.5, 30.9),   # 6 group 3, starts during the test row's life: dropped
    ]
    labels = pd.DataFrame({"ts_utc": [T0 + a * day for a, _ in rows], "ts_exit": [T0 + b * day for _, b in rows]})
    groups = cpcv.assign_groups(labels, edges)
    assert list(groups) == [1, 1, 1, 2, 3, 3, 3]
    train = cpcv.purged_train(labels, groups, (2, 5), edges, purge_days=2, embargo_days=1)
    assert list(train) == [0, 5]


def test_a_synthetic_leak_is_caught(monkeypatch):
    labels, _ = _labels(max_hours=200)
    edges = cpcv.group_edges(T0, T1)
    groups = cpcv.assign_groups(labels, edges)
    # an unpurged split (train = every other group) trains on labels that overlap the test labels: detected
    naive = cpcv.CpcvSplit(k=0, test_groups=(1, 2), test_idx=np.flatnonzero(np.isin(groups, (1, 2))),
                           train_idx=np.flatnonzero(~np.isin(groups, (1, 2))))
    leaked = cpcv.overlaps(labels, naive)
    assert len(leaked) > 0
    assert set(groups[leaked]) <= {0, 3}                              # the neighbours of the test block
    # and cpcv_splits refuses to hand out a leaking split
    monkeypatch.setattr(cpcv, "purged_train", lambda lab, g, tg, *a: np.flatnonzero(~np.isin(g, tg)))
    with pytest.raises(ValueError, match="overlap"):
        cpcv.cpcv_splits(labels, edges, purge_days=2, embargo_days=1)


def test_run_cpcv_reports_the_path_distribution_with_cross_fitted_selection():
    labels, feats = _labels()
    res = cpcv.run_cpcv(labels, feats, ["side", "x", "z"], edges=cpcv.group_edges(T0, T1), purge_days=2,
                        embargo_days=1, extra_cost_usd=0.0)
    assert res["n_splits"] == 15 and res["n_paths"] == 5 and all(s["fitted"] for s in res["splits"])
    assert len(res["paths"]) == 5 and set(res["sharpe"]) == {"mean", "std", "min", "median", "max"}
    # calibration per path from earlier groups only: the first group has no calibrator, so it never trades
    assert all(gr[0] == 0.0 for gr in res["group_r"]) and all(any(v != 0 for v in gr[1:]) for gr in res["group_r"])
    # an informative feature makes money on every path; the distribution is reported, not one number
    assert res["share_paths_positive"] == 1.0 and res["mean_r"]["min"] > 0


def _pbo(perf: np.ndarray) -> dict:
    out = cpcv.pbo(perf)
    assert out is not None
    return out


def test_pbo_is_zero_for_a_dominant_config_one_for_anti_persistence_and_none_for_one_config():
    assert _pbo(np.array([[1.0] * 6, [0.0] * 6]))["pbo"] == 0.0
    flip = np.array([[1, 1, 1, -1, -1, -1], [-1, -1, -1, 1, 1, 1]], dtype=float)
    assert _pbo(flip)["pbo"] == 1.0                                # the in-sample winner always loses out of sample
    noise = _pbo(np.random.default_rng(0).normal(size=(30, 6)))
    assert noise["n_combinations"] == 20 and 0.2 <= noise["pbo"] <= 0.8
    assert cpcv.pbo(np.ones((1, 6))) is None


def test_cpcv_is_evidence_on_the_trial_not_a_new_trial(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    row = reg.record(agent_id="a", family="session_open", config={"x": 1}, feature_version="v", rationale="r",
                     results={"gates": {"passed": True}})
    before = (reg.n_trials, reg.n_trials_effective, reg.budget_used(quarter_of()))
    ev = reg.attach_evidence(row["trial"], cpcv.EVIDENCE_KIND, {"n_paths": 5})
    assert (reg.n_trials, reg.n_trials_effective, reg.budget_used(quarter_of())) == before
    assert len(reg._rows()) == 1 and ev["config_hash"] == row["config_hash"]
    assert reg.evidence(row["trial"], "cpcv")[0]["payload"] == {"n_paths": 5}
    assert reg.evidence_path.name == "trials.evidence.jsonl" and ev["quarter"] == quarter_of(datetime.now(timezone.utc))
    with pytest.raises(KeyError):
        reg.attach_evidence(99, "cpcv", {})

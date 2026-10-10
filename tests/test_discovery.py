"""Feature discovery (indicator survey 4b): fold-internal stability selection, the trial accounting
(n_trials_effective) and pre-registration rows in the trial registry."""
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.discovery import (
    DiscoveryConfig,
    auc_lower_bound,
    column_groups,
    reading_verdict,
    run_discovery,
    select_in_fold,
)
from goldbot.research.registry import (
    DISCOVERY,
    PREREGISTERED,
    TrialRegistry,
    is_trial,
    n_trials_effective,
    quarter_of,
)
from goldbot.research.registry_sync import merge_rows
from goldbot.research.walkforward import walk_forward_splits

WINDOW: dict[str, Any] = dict(train_months=12, test_months=4, step_months=4, purge_days=2, embargo_days=1)
NOISE = [f"noise_{i}" for i in range(8)]


def _synthetic(n_days: int = 760, per_day: int = 3, seed: int = 0, leak_from: pd.Timestamp | None = None):
    """Candidates with one informative feature (`info`), pure noise columns, and `leak`: noise everywhere except from
    `leak_from` on, where it is the label itself (plus a little noise)."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2021-01-04", periods=n_days * per_day, freq=f"{24 // per_day}h", tz="UTC")
    n = len(ts)
    info = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-2.0 * info))).astype(int)
    feats = pd.DataFrame({"info": info, **{c: rng.normal(size=n) for c in NOISE}})
    leak = rng.normal(size=n)
    if leak_from is not None:
        late = np.asarray(ts >= leak_from)
        leak[late] = y[late] + 0.05 * rng.normal(size=late.sum())
    feats["leak"] = leak
    feats["side"] = 1.0
    labels = pd.DataFrame({"ts_utc": ts, "ts_exit": ts + pd.Timedelta(hours=6), "target_hit": y,
                           "ret": np.where(y == 1, 0.004, -0.002), "risk": 0.002, "atr_sig": 1.0, "stop_atr": 1.0,
                           "target_atr": 2.0, "weight": 1.0})
    gross = labels[["ts_utc", "ts_exit", "ret", "risk"]].copy()
    return labels, gross, feats


POOL = ["info", *NOISE, "leak"]
CFG = DiscoveryConfig(n_subsamples=15, top_k=2, freq_threshold=0.6, stability_threshold=0.7)


def test_an_informative_feature_is_selected_and_a_feature_correlated_only_with_test_labels_is_not():
    labels, _, feats = _synthetic()
    folds = list(walk_forward_splits(labels, **WINDOW))
    f = folds[0]
    test = np.zeros(len(labels), dtype=bool)
    test[f.test_idx] = True
    # leak: pure noise on the training rows, the test labels themselves on the test rows
    rng = np.random.default_rng(1)
    feats.loc[test, "leak"] = labels.loc[test, "target_hit"].to_numpy() + 0.05 * rng.normal(size=test.sum())
    sel = select_in_fold(feats, labels, f, POOL, CFG, purge_days=WINDOW["purge_days"])
    assert sel.n_train == len(f.train_idx) and sel.n_subsamples == CFG.n_subsamples
    assert sel.frequency["info"] >= 0.9 and "info" in sel.selected
    assert "leak" not in sel.selected and sel.frequency["leak"] < CFG.freq_threshold


def test_selection_never_reads_a_test_row():
    labels, _, feats = _synthetic()
    f = list(walk_forward_splits(labels, **WINDOW))[0]
    before = select_in_fold(feats, labels, f, POOL, CFG.model_copy(update={"permutation": True}), purge_days=2)
    scrambled_f, scrambled_l = feats.copy(), labels.copy()
    rng = np.random.default_rng(7)
    scrambled_f.loc[f.test_idx, POOL] = rng.normal(size=(len(f.test_idx), len(POOL))) * 100
    scrambled_l.loc[f.test_idx, "target_hit"] = 1 - scrambled_l.loc[f.test_idx, "target_hit"]
    after = select_in_fold(scrambled_f, scrambled_l, f, POOL, CFG.model_copy(update={"permutation": True}), purge_days=2)
    assert before.frequency == after.frequency and before.selected == after.selected
    assert before.permutation == after.permutation and before.permutation is not None
    assert before.permutation["info"] > 0             # the inner validation confirms the informative feature


def test_full_discovery_reports_frequencies_stability_and_a_capped_selection_without_the_leak():
    folds = list(walk_forward_splits(_synthetic()[0], **WINDOW))
    # the leak equals the label only in the last test fold, which no fold ever trains on
    labels, gross, feats = _synthetic(leak_from=folds[-1].test_start)
    assert len(folds) >= 3
    d = run_discovery("synthetic-1", labels, gross, feats, folds, "f-test", WINDOW, POOL, CFG)
    assert [s.k for s in d.folds] == [f.k for f in folds]
    assert d.selected[0] == "info" and "leak" not in d.selected
    assert d.table.loc["info", "stability"] == 1.0 and d.table.loc["leak", "stability"] == 0.0
    assert {f"f{f.k}" for f in folds} <= set(d.table.columns)
    assert len(d.selected) <= CFG.max_selected and d.n_features_screened == len(POOL) == d.n_groups_screened
    # each fold's model used its own selection; the out-of-fold model sees the informative feature
    assert d.result.metrics["oof_auc"] > 0.7 and "gates" in d.result.metrics
    disc = d.result.metrics["discovery"]
    assert disc["n_groups_screened"] == len(POOL) and disc["selected"] == d.selected
    assert all("info" in fold["selected"] for fold in disc["folds"])
    json.dumps(d.result.metrics, default=str)                                   # registry-serialisable
    assert d.verdict["decision"] in ("continue", "inconclusive", "stop")


def test_the_selection_is_capped_at_forty_inputs_with_side():
    labels, gross, feats = _synthetic(n_days=500)
    rng = np.random.default_rng(3)
    wide = [f"w{i}" for i in range(60)]
    for c in wide:
        feats[c] = feats["info"] + 0.3 * rng.normal(size=len(feats))           # 60 informative near-copies
    folds = list(walk_forward_splits(labels, **WINDOW))
    cfg = DiscoveryConfig(n_subsamples=4, top_k=60, freq_threshold=0.5, stability_threshold=0.5)
    d = run_discovery("synthetic-2", labels, gross, feats, folds, "f-test", WINDOW, wide, cfg)
    assert len(d.selected) == 39 and all(len(s.selected) <= 39 for s in d.folds)
    assert d.result.model is None or len(d.result.model.feature_names) <= 40


def test_groups_and_reading_rule():
    assert column_groups(["a", "b"], "column") == {"a": "a", "b": "b"}
    with pytest.raises(ValueError):
        column_groups(["a"], "nonsense")
    assert reading_verdict(0.51, 0.49, 5000, 0.1, 300)["decision"] == "stop"
    assert reading_verdict(None, None, 0, None, 0)["decision"] == "stop"
    assert reading_verdict(0.55, 0.52, 5000, 0.05, 300)["decision"] == "continue"
    assert reading_verdict(0.55, 0.49, 5000, 0.05, 300)["decision"] == "inconclusive"   # lower bound not above 0.50
    assert reading_verdict(0.55, 0.52, 900, 0.05, 300)["decision"] == "inconclusive"    # too few events
    assert reading_verdict(0.55, 0.52, 5000, -0.01, 300)["decision"] == "inconclusive"  # not net positive
    ts = pd.date_range("2022-01-01", periods=400, freq="6h", tz="UTC")
    y = np.tile([0, 1], 200)
    assert auc_lower_bound(y, y + 0.0, ts) == pytest.approx(1.0)


def test_column_groups_map_columns_and_context_columns_to_their_feature():
    from goldbot.data.resample import mid, resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    m = mid(resample_bars(ticks_to_1m(synthetic_ticks("2024-01-01", "2024-01-20", ticks_per_minute=1, seed=2)), "1h"))
    g = column_groups(["atr14_pct", "h4_atr14_pct", "unknown_col"], "feature", m.reset_index(drop=True), ["atr"])
    assert g == {"atr14_pct": "atr", "h4_atr14_pct": "atr", "unknown_col": "unknown_col"}
    fam = column_groups(["atr14_pct"], "family", m.reset_index(drop=True), ["atr"])
    from goldbot.features import FEATURES
    assert fam == {"atr14_pct": FEATURES["atr"].family}


# ---------------------------------------------------------------------------------------------- registry
def _discovery_row(k: int) -> dict:
    return {"status": DISCOVERY, "results": {"discovery": {"n_groups_screened": k}}}


def test_n_trials_effective_adds_the_groups_every_discovery_screened():
    rows = [{"status": "evaluated"}, {"status": "screened"}, {"status": PREREGISTERED}, _discovery_row(35)]
    assert n_trials_effective(rows) == 3 + 35                 # the pre-registration is not a trial
    assert n_trials_effective([{"status": "evaluated"}]) == 1
    assert n_trials_effective(rows + [{"status": PREREGISTERED}, _discovery_row(12)]) == 4 + 35 + 12
    assert not is_trial({"status": PREREGISTERED}) and is_trial({"status": DISCOVERY})


def test_a_preregistration_precedes_its_result_and_is_neither_a_trial_nor_a_budget_slot(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    reg.record(agent_id="a", family="tsmom", config={"x": 1}, feature_version="v", rationale="", results={})
    cfg = {"specialist": "tsmom", "top_k": 10}
    pre = reg.preregister(agent_id="b", family="discovery_tsmom", config=cfg, feature_version="v", rationale="r",
                          reading_rule="continue if ...", plan={"features": ["atr"]})
    assert pre["status"] == PREREGISTERED and pre["trial"] == 2 and pre["reading_rule"] == "continue if ..."
    assert reg.n_trials == 1 and reg.budget_used(quarter_of()) == 1
    assert reg.preregistration("discovery_tsmom", cfg) == pre and reg.preregistration("discovery_tsmom", {}) is None
    res = reg.record(agent_id="b", family="discovery_tsmom", config=cfg, feature_version="v", rationale="r",
                     results={"discovery": {"n_groups_screened": 7}}, status=DISCOVERY, preregistration=pre)
    assert res["trial"] == 2 and res["preregistration"]["config_hash"] == pre["config_hash"] == res["config_hash"]
    assert reg.n_trials == 2 and reg.n_trials_effective == 2 + 7 and reg.budget_used(quarter_of()) == 2
    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == ["evaluated", PREREGISTERED, DISCOVERY]
    assert rows[1]["ts"] <= rows[2]["ts"]
    assert not reg.passed_gates("discovery_tsmom", cfg)        # a discovery is never promotable
    # the release merge numbers trials only; the pre-registration keeps its result's number
    merged = merge_rows(rows)
    assert [r["trial"] for r in merged] == [1, 2, 2]


# ---------------------------------------------------------------------------------------------- review fixes
def test_family_mode_with_feature_survivors_pays_for_every_feature_screened_and_group_survivors_for_groups():
    folds = list(walk_forward_splits(_synthetic()[0], **WINDOW))
    labels, gross, feats = _synthetic()
    groups = {c: ("fam_a" if c in ("info", "noise_0", "noise_1") else "fam_b" if c.startswith("noise") else "fam_c")
              for c in POOL}
    n_groups = len(set(groups.values()))
    d = run_discovery("synthetic-3", labels, gross, feats, folds, "f-test", WINDOW, POOL, CFG, groups, "family")
    disc = d.result.metrics["discovery"]
    assert d.survivor_unit == "feature" and disc["survivor_unit"] == "feature"
    assert disc["n_features_screened"] == len(POOL) and disc["n_groups_screened"] == n_groups == 3
    assert disc["k_eff"] == len(POOL) and d.k_eff == len(POOL)       # feature survivors pay for the features
    assert "info" in d.selected and "survivors are features" in d.verdict["rule"]
    row = {"status": DISCOVERY, "results": {"discovery": disc}}
    assert n_trials_effective([row]) == 1 + len(POOL)
    g = run_discovery("synthetic-4", labels, gross, feats, folds, "f-test", WINDOW, POOL,
                      CFG.model_copy(update={"survivor_unit": "group"}), groups, "family")
    gd = g.result.metrics["discovery"]
    assert gd["survivor_unit"] == "group" and gd["k_eff"] == n_groups
    assert "fam_a" in g.survivor_groups and set(g.selected) <= {c for c in POOL if groups[c] in g.survivor_groups}
    assert "survivors are whole groups" in g.verdict["rule"]
    assert n_trials_effective([{"status": DISCOVERY, "results": {"discovery": gd}}]) == 1 + n_groups


def test_a_legacy_discovery_row_without_k_eff_pays_for_its_features_when_it_recorded_them():
    legacy = {"status": DISCOVERY, "results": {"discovery": {"n_features_screened": 300, "n_groups_screened": 10}}}
    assert n_trials_effective([legacy]) == 1 + 300
    grp = {"status": DISCOVERY, "results": {"discovery": {"n_features_screened": 300, "n_groups_screened": 10,
                                                           "survivor_unit": "group"}}}
    assert n_trials_effective([grp]) == 1 + 10


def test_correlated_near_copies_no_longer_deflate_their_group():
    """Five near-copies of the informative feature split its importance; ranked by summed importance their group is
    still the most frequent, where the best single member's frequency would rank it below a weaker lone feature."""
    labels, _, feats = _synthetic()
    rng = np.random.default_rng(5)
    copies = [f"copy_{i}" for i in range(5)]
    for c in copies:
        feats[c] = feats["info"] + 0.05 * rng.normal(size=len(feats))
    y = labels["target_hit"].to_numpy()
    feats["weak"] = 0.6 * (y - 0.5) + rng.normal(size=len(feats))
    pool = [*copies, "weak", *NOISE]
    groups = {c: ("info_group" if c in copies else c) for c in pool}
    f = list(walk_forward_splits(labels, **WINDOW))[0]
    cfg = CFG.model_copy(update={"top_k": 1, "group_top_k": 1})
    sel = select_in_fold(feats, labels, f, pool, cfg, groups, purge_days=2)
    best_member = max(sel.frequency[c] for c in copies)
    assert sel.group_frequency["info_group"] >= 0.9
    assert sel.group_frequency["info_group"] > sel.group_frequency["weak"]
    assert best_member < sel.group_frequency["info_group"]          # the old "max member" rule deflated it
    assert sum(sel.group_frequency.values()) == pytest.approx(1.0)  # one group per subsample with group_top_k 1


def test_group_top_k_defaults_to_the_top_k_share_of_groups():
    from goldbot.research.discovery import group_top_k
    assert group_top_k(CFG.model_copy(update={"top_k": 10}), n_features=300, n_groups=10) == 1
    assert group_top_k(CFG.model_copy(update={"top_k": 10}), n_features=40, n_groups=40) == 10   # column mode
    assert group_top_k(CFG.model_copy(update={"top_k": 10}), n_features=120, n_groups=35) == 3
    assert group_top_k(CFG.model_copy(update={"top_k": 10, "group_top_k": 4}), n_features=300, n_groups=10) == 4


def test_column_groups_never_read_a_holdout_bar(monkeypatch):
    from goldbot.features import FEATURES
    from goldbot.features.registry import FeatureSpec
    seen: list[pd.Timestamp] = []

    def spy(m: pd.DataFrame, ctx: dict) -> pd.DataFrame:
        seen.extend(pd.to_datetime(m["ts_utc"], utc=True))
        return pd.DataFrame({"spy_col": np.zeros(len(m))})

    monkeypatch.setitem(FEATURES, "spy", FeatureSpec(name="spy", family="spyfam", fn=spy))
    ts = pd.date_range("2025-01-01", periods=5000, freq="1h", tz="UTC")
    m = pd.DataFrame({"ts_utc": ts, "close": 1.0})
    start = pd.Timestamp("2025-04-01", tz="UTC")
    g = column_groups(["spy_col", "h4_spy_col"], "family", m, ["spy"], holdout_start=start)
    assert g == {"spy_col": "spyfam", "h4_spy_col": "spyfam"}
    assert seen and max(seen) < start and len(seen) == 2000         # the 2000 bars before the holdout, none in it
    with pytest.raises(ValueError):
        column_groups(["spy_col"], "family", m[ts >= start], ["spy"], holdout_start=start)


def test_inner_split_purges_training_labels_that_outlive_the_validation_gap():
    from goldbot.research.discovery import inner_split
    ts = pd.DatetimeIndex(pd.date_range("2022-01-01", periods=400, freq="12h", tz="UTC"))
    te = ts + pd.Timedelta(days=10)                                 # labels live 10 days; the gap is 2
    fit, val = inner_split(ts, te, inner_val_frac=0.2, purge_days=2)
    cut = ts[int(len(ts) * 0.8)]
    before = np.asarray(ts < cut)
    dropped = before & ~fit
    assert val.sum() == len(ts) - int(len(ts) * 0.8) and not (fit & val).any()
    assert dropped.sum() == 24                                      # 12 days of 12h bars end within cut - 2d
    assert (te[fit] < cut - pd.Timedelta(days=2)).all() and (te[dropped] >= cut - pd.Timedelta(days=2)).all()


def test_merge_renumbering_rewrites_the_results_preregistration_link():
    pre = {"status": PREREGISTERED, "ts": "2026-10-10T10:00:00+00:00", "agent_id": "b", "config_hash": "h2",
           "feature_version": "v", "trial": 1}
    res: dict[str, Any] = {"status": DISCOVERY, "ts": "2026-10-10T11:00:00+00:00", "agent_id": "b", "config_hash": "h2",
           "feature_version": "v", "trial": 1,
           "preregistration": {"ts": pre["ts"], "trial": 1, "config_hash": "h2"}}
    other = {"status": "evaluated", "ts": "2026-10-09T09:00:00+00:00", "agent_id": "a", "config_hash": "h1",
             "feature_version": "v", "trial": 1}                    # recorded on the VPS, earlier in time
    merged = merge_rows([pre, res], [other])
    assert [r["trial"] for r in merged] == [1, 2, 2]
    assert merged[2]["preregistration"]["trial"] == 2 == merged[1]["trial"]
    assert res["preregistration"]["trial"] == 1                     # the input rows are not mutated
    assert merge_rows(merged, [other]) == merged

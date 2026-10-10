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

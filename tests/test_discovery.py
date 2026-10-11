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
                      CFG.model_copy(update={"survivor_unit": "group", "group_top_k": 3}), groups, "family")
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


def test_group_top_k_defaults_to_the_top_k_share_of_groups_with_a_floor_of_three():
    from goldbot.research.discovery import group_top_k
    assert group_top_k(CFG.model_copy(update={"top_k": 10}), n_features=300, n_groups=10) == 3        # share 1, floored at 3
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


def test_group_top_k_has_a_floor_so_a_second_family_can_survive():
    from goldbot.research.discovery import GROUP_TOP_K_FLOOR, DiscoveryConfig, group_top_k
    cfg = DiscoveryConfig()
    assert group_top_k(cfg, n_features=300, n_groups=10) >= GROUP_TOP_K_FLOOR       # was 1: frequencies capped at 1 total
    assert group_top_k(cfg, n_features=300, n_groups=2) == 2                         # never more groups than exist
    assert group_top_k(cfg.model_copy(update={"group_top_k": 1}), 300, 10) == 1      # explicit setting wins


def test_a_discovery_survivor_needs_a_passed_holdout_before_it_counts_as_passed(tmp_path):
    from goldbot.research.registry import FROM_DISCOVERY_KEY, TrialRegistry, config_hash
    reg = TrialRegistry(tmp_path / "r.jsonl")
    cfg = {"x": 1, FROM_DISCOVERY_KEY: True}
    h = config_hash(cfg)
    costs = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0}, "cost_source": "PRIORS ONLY: test"}
    rows = [{"status": "evaluated", "family": "tsmom", "config_hash": h, "results": {"gates": {"passed": True}, **costs}}]
    reg._rows = lambda: rows                                                          # type: ignore[method-assign]
    assert not reg.passed_gates("tsmom", cfg)                                         # gates alone are not enough
    rows.append({"status": "holdout", "family": "tsmom", "config_hash": h,
                 "results": {"holdout_verdict": {"passed": False}, **costs}})
    assert not reg.passed_gates("tsmom", cfg)
    rows.append({"status": "holdout", "family": "tsmom", "config_hash": h,
                 "results": {"holdout_verdict": {"passed": True}, **costs}})
    assert reg.passed_gates("tsmom", cfg)
    plain = {"x": 1}
    rows.append({"status": "evaluated", "family": "tsmom", "config_hash": config_hash(plain),
                 "results": {"gates": {"passed": True}, **costs}})
    assert reg.passed_gates("tsmom", plain)                                           # ordinary trials unchanged


def test_check_budget_reserved_lets_only_a_pending_queued_preregistration_spend_the_reservation(tmp_path, queued_prereg):
    from goldbot.research.director import reserved_trials
    from goldbot.research.registry import TrialBudgetExceeded
    reg = TrialRegistry(tmp_path / "r.jsonl")
    q = quarter_of()
    reg.record(agent_id="a", family="trend", config={}, feature_version="v", rationale="", results={})
    # budget 3, 2 held for the queue, 1 used: nothing left for a run that is not pre-registered
    with pytest.raises(TrialBudgetExceeded, match="held for the pre-registered queue"):
        reg.check_budget_reserved([("tsmom", {"x": 1})], 3, 2)
    pre = queued_prereg(reg, agent_id="b", family="tsmom", config={"x": 1}, feature_version="v", rationale="r",
                          reading_rule="continue if ...")
    quarter, (match,) = reg.check_budget_reserved([("tsmom", {"x": 1})], 3, 2)
    assert quarter == q and match == pre
    with pytest.raises(TrialBudgetExceeded):                     # another config does not match it
        reg.check_budget_reserved([("tsmom", {"x": 2})], 3, 2)
    with pytest.raises(TrialBudgetExceeded):                     # one pre-registered row covers one run
        reg.check_budget_reserved([("tsmom", {"x": 1}), ("tsmom", {"x": 1})], 3, 2)
    reg.record(agent_id="b", family="tsmom", config={"x": 1}, feature_version="v", rationale="r", results={},
               preregistration=match)
    assert reserved_trials(reg._rows(), q, 2).reserved == 1      # the reservation went down by one
    # a pre-registration a run writes for itself is not part of the queue: it neither holds nor uses the reservation
    jit = reg.preregister(agent_id="c", family="discovery_tsmom", config={"y": 1}, feature_version="v", rationale="r",
                          reading_rule="continue if ...", queue=False)
    assert jit["queue"] is False and reserved_trials(reg._rows(), q, 2).reserved == 1
    reg.record(agent_id="c", family="discovery_tsmom", config={"y": 1}, feature_version="v", rationale="r",
               results={}, status=DISCOVERY, preregistration=jit)
    assert reserved_trials(reg._rows(), q, 2).reserved == 1
    assert reg.check_budget_reserved([("discovery_tsmom", {"y": 1})], 5, 2)[1] == [None]   # never matched again


def test_a_link_to_a_preregistration_matches_its_timestamp_not_only_its_number(tmp_path, queued_prereg):
    from goldbot.research.director import reserved_trials
    reg = TrialRegistry(tmp_path / "r.jsonl")
    q = quarter_of()
    queued = queued_prereg(reg, agent_id="a", family="tsmom", config={"x": 1}, feature_version="v", rationale="r",
                             reading_rule="r")
    jit = reg.preregister(agent_id="b", family="discovery_tsmom", config={"y": 1}, feature_version="v",
                          rationale="r", reading_rule="r", queue=False)
    assert queued["trial"] == jit["trial"] == 1                 # both take the next trial number
    reg.record(agent_id="b", family="discovery_tsmom", config={"y": 1}, feature_version="v", rationale="r",
               results={}, status=DISCOVERY, preregistration=jit)
    r = reserved_trials(reg._rows(), q, 0)
    assert (r.run, r.pending, r.reserved) == (0, 1, 1)           # the queued row is still pending


def _stability_frames(sizes: dict[str, int], stab: dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Feature and group stability tables as `stability_tables` returns them (sorted by stability)."""
    cols = [(f"{g}_{i}", g) for g, n in sizes.items() for i in range(n)]
    feat = pd.DataFrame({"group": [g for _, g in cols], "mean_freq": 0.7, "stability": [stab[g] for _, g in cols]},
                        index=[c for c, _ in cols]).sort_values("stability", ascending=False, kind="stable")
    grp = pd.DataFrame({"n_features": list(sizes.values()), "mean_freq": 0.7,
                        "stability": [stab[g] for g in sizes]}, index=list(sizes)).sort_values(
        "stability", ascending=False, kind="stable")
    return feat, grp


def test_group_mode_forwards_whole_groups_in_stability_order_while_they_fit_and_records_the_rest():
    """Quant MEDIUM 4: K_eff = groups screened stays honest when only whole groups go forward; a group that does not
    fit (and every one after it) is recorded as skipped, never truncated."""
    from goldbot.research.discovery import survivor_selection
    cfg = DiscoveryConfig(survivor_unit="group", group_top_k=3, max_selected=39)
    feat, grp = _stability_frames({"sweeps": 25, "levels": 20, "jumps": 10, "noise": 5},
                                  {"sweeps": 0.9, "levels": 0.8, "jumps": 0.75, "noise": 0.1})
    groups, selected, n_features, skipped = survivor_selection(feat, grp, cfg)
    assert groups == ["sweeps"] and skipped == ["levels", "jumps"] and len(selected) == 25
    assert n_features == {"sweeps": 25}
    feat, grp = _stability_frames({"sweeps": 25, "levels": 14, "noise": 5}, {"sweeps": 0.9, "levels": 0.8, "noise": 0.1})
    groups, selected, n_features, skipped = survivor_selection(feat, grp, cfg)
    assert groups == ["sweeps", "levels"] and len(selected) == 39 and skipped == []
    # feature mode keeps the best max_selected features (K_eff = features screened is unaffected by the cut)
    feat, grp = _stability_frames({"a": 30, "b": 20}, {"a": 0.9, "b": 0.8})
    _, selected, _, skipped = survivor_selection(feat, grp, DiscoveryConfig(max_selected=39))
    assert len(selected) == 39 and skipped == []


def test_group_mode_refuses_a_group_over_max_selected_before_any_look_at_the_data(monkeypatch):
    from goldbot.research import discovery as disc_mod
    labels, gross, feats = _synthetic()
    folds = list(walk_forward_splits(labels, **WINDOW))
    looked: list[int] = []
    monkeypatch.setattr(disc_mod, "select_in_fold", lambda *a, **k: looked.append(1))
    big = {c: "one_group" for c in POOL}                       # 10 columns in one group, max_selected 3
    cfg = CFG.model_copy(update={"survivor_unit": "group", "group_top_k": 1, "max_selected": 3})
    with pytest.raises(ValueError, match=r"one_group has 10 columns.*max_selected 3"):
        run_discovery("synthetic-6", labels, gross, feats, folds, "f-test", WINDOW, POOL, cfg, big, "family")
    assert looked == []


def test_group_survivor_unit_needs_an_explicit_group_top_k():
    from goldbot.research.discovery import survivor_selection
    feat, grp = _stability_frames({"a": 2}, {"a": 0.9})
    with pytest.raises(ValueError, match="group_top_k"):
        survivor_selection(feat, grp, DiscoveryConfig(survivor_unit="group"))


def test_group_discovery_reports_the_feature_count_beside_each_survivor_group():
    from goldbot.research.discovery import render_discovery
    labels, gross, feats = _synthetic()
    folds = list(walk_forward_splits(labels, **WINDOW))
    groups = {c: ("fam_a" if c in ("info", "noise_0", "noise_1") else "fam_b" if c.startswith("noise") else "fam_c")
              for c in POOL}
    g = run_discovery("synthetic-5", labels, gross, feats, folds, "f-test", WINDOW, POOL,
                      CFG.model_copy(update={"survivor_unit": "group", "group_top_k": 3}), groups, "family")
    gd = g.result.metrics["discovery"]
    assert g.survivor_groups
    assert gd["survivor_groups_n_features"] == {s: sum(1 for c in POOL if groups[c] == s) for s in g.survivor_groups}
    from goldbot.research.discovery import group_map_hash
    assert gd["group_map_sha256"] == group_map_hash(groups) != group_map_hash({**groups, "info": "fam_c"})
    assert gd["group_members"] == {g_: [c for c in POOL if groups[c] == g_] for g_ in set(groups.values())}
    assert gd["skipped_survivor_groups"] == [] and gd["reading_verdict"]["decision"] in ("continue", "stop",
                                                                                         "inconclusive")
    text = render_discovery(g, {"specialist": "s", "from_year": 2021, "to_year": 2023, "tf": "4h", "trial": 1,
                                "prereg_ts": "t", "n_trials_effective": 4})
    assert all(f"{s} ({gd['survivor_groups_n_features'][s]} features)" in text for s in g.survivor_groups)


def test_cluster_grouping_maps_columns_to_the_preregistered_hypothesis_clusters_first_match_wins():
    """H-02 groups by hypothesis cluster (rows 6-9), so cluster stability is the group stability the slots rank by."""
    from goldbot.research.discovery import H02_CLUSTERS
    clusters = {"H-03": ["macro_gvz*"], "H-07": ["macro_vix*", "macro_spx*"], "H-10": ["jump_*"]}
    cols = ["macro_gvz", "d1_macro_gvz_chg20", "macro_vix_chg5", "macro_spx_dd20", "jump_z", "atr_14"]
    g = column_groups(cols, "cluster", clusters=clusters)
    assert g == {"macro_gvz": "H-03", "d1_macro_gvz_chg20": "H-03", "macro_vix_chg5": "H-07",
                 "macro_spx_dd20": "H-07", "jump_z": "H-10", "atr_14": "atr_14"}
    with pytest.raises(ValueError, match="clusters"):
        column_groups(cols, "cluster")
    assert list(H02_CLUSTERS) == ["H-03", "H-05", "H-07", "H-08", "H-10"]
    from goldbot.research.discovery import check_group_sizes
    cfg = DiscoveryConfig(max_selected=3, clusters=clusters)
    wide_family = {**{f"atr_{i}": "volatility" for i in range(9)}, "jump_z": "H-10"}
    check_group_sizes(wide_family, cfg, "cluster")             # a family group never becomes a trial's inputs
    with pytest.raises(ValueError, match="H-10 has 4 columns"):
        check_group_sizes({f"jump_{i}": "H-10" for i in range(4)}, cfg, "cluster")


def test_h02_clusters_follow_the_hypotheses_and_send_the_rest_to_their_family():
    """Freeze item (quant re-review): H-03 = GVZ + the VRP spread; H-05 = levels incl. prior day/week extremes; H-08 =
    sweeps + session/Asia/London extremes; H-10 = jumps only; realised vol, swings and breaks stay in their family."""
    from goldbot.research.discovery import H02_CLUSTERS, cluster_of
    owners = {"macro_gvz": "macro_drivers", "macro_gvz_chg20": "macro_drivers", "macro_vrp": "macro_drivers",
              "macro_vix": "macro_drivers", "macro_spx_dd20": "macro_drivers", "rv_20": "realised_vol",
              "yz_vol": "vol_estimators", "round_dist": "round_numbers", "sr_dist": "support_resistance",
              "pdh_dist_atr": "session_zones", "pwl_dist_atr": "session_zones", "sweep_high": "market_structure",
              "sweep_dir": "market_structure", "bos_event": "market_structure", "bars_since_bos": "market_structure",
              "asia_high_dist_atr": "session_zones", "london_low_dist_atr": "session_zones",
              "sess_high_dist_atr": "session_zones", "prev_sess_low_dist_atr": "session_zones",
              "newyork_high_dist_atr": "session_zones", "prev_sess_pos": "session_zones", "hh": "swings",
              "jump_z": "jumps", "min_to_next_tier1": "calendar_events"}
    want = {"macro_gvz": "H-03", "macro_gvz_chg20": "H-03", "macro_vrp": "H-03", "macro_vix": "H-07",
            "macro_spx_dd20": "H-07", "round_dist": "H-05", "sr_dist": "H-05", "pdh_dist_atr": "H-05",
            "pwl_dist_atr": "H-05", "sweep_high": "H-08", "sweep_dir": "H-08", "asia_high_dist_atr": "H-08",
            "london_low_dist_atr": "H-08", "sess_high_dist_atr": "H-08", "prev_sess_low_dist_atr": "H-08",
            "jump_z": "H-10"}
    for col, owner in owners.items():
        assert cluster_of(col, owner, H02_CLUSTERS) == want.get(col), col
    assert cluster_of("d1_macro_vrp", "macro_drivers", H02_CLUSTERS) == "H-03"       # a context column joins it


def test_cluster_mode_refuses_a_cluster_with_no_informative_column_before_any_look(monkeypatch):
    from goldbot.research import discovery as disc_mod
    labels, gross, feats = _synthetic()
    feats = feats.assign(flat=1.0, gone=np.nan)
    folds = list(walk_forward_splits(labels, **WINDOW))
    pool = [*POOL, "flat", "gone"]
    looked: list[int] = []
    real = disc_mod.select_in_fold
    def spy(*a: Any, **k: Any) -> Any:
        looked.append(1)
        return real(*a, **k)
    monkeypatch.setattr(disc_mod, "select_in_fold", spy)
    clusters = {"H-A": ["info"], "H-B": ["flat", "gone"]}
    groups = {c: ("H-A" if c == "info" else "H-B" if c in ("flat", "gone") else "noise") for c in pool}
    cfg = CFG.model_copy(update={"clusters": clusters, "group_top_k": 3})
    with pytest.raises(ValueError, match=r"H-B has no non-constant, non-NaN column"):
        run_discovery("synthetic-7", labels, gross, feats, folds, "f-test", WINDOW, pool, cfg, groups, "cluster")
    assert looked == []
    ok = {**groups, "noise_0": "H-B"}
    d = run_discovery("synthetic-8", labels, gross, feats, folds, "f-test", WINDOW, pool, cfg, ok, "cluster")
    cc = d.result.metrics["discovery"]["cluster_columns"]
    assert cc == {"H-A": {"columns": 1, "informative": 1}, "H-B": {"columns": 3, "informative": 1}}


def test_group_permutation_importance_is_size_neutral_and_redundancy_safe():
    """ADR 0007 amendment: a group's measure is the loss increase when ALL its members are permuted together, so
    padding a group with dead columns does not raise it, and near-copies that split one signal do not lower it."""
    from goldbot.research.discovery import group_permutation_importance
    rng = np.random.default_rng(0)
    n = 4000
    a = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-2 * a))).astype(int)
    noise = rng.normal(size=(n, 20))
    Z = pd.DataFrame({"a": a, "a1": a, "a2": a, **{f"z{i}": noise[:, i] for i in range(20)}})

    def predict(X: pd.DataFrame) -> np.ndarray:            # reads the signal as the mean of its copies
        return 1 / (1 + np.exp(-2 * X[["a", "a1", "a2"]].mean(axis=1).to_numpy()))
    alone = group_permutation_importance(predict, Z, y, [[0, 1, 2], list(range(3, 23))], np.random.default_rng(1))
    padded = group_permutation_importance(predict, Z, y, [list(range(23)), [3]], np.random.default_rng(1))
    split = group_permutation_importance(predict, Z, y, [[0], [1], [2]], np.random.default_rng(1))
    assert alone[0] > 0.05 and abs(alone[1]) < 1e-12               # dead columns have no measure
    assert np.isclose(padded[0], alone[0], rtol=0.15)             # 20 dead members do not raise the group
    assert split.max() < alone[0] / 2                             # each copy alone is a fraction of the group


def test_select_in_fold_ranks_groups_by_the_joint_permutation_measure():
    """A one-column informative group beats a 9-column noise group under group_top_k 1 (summed gain over many noise
    columns could otherwise rank by size)."""
    labels, gross, feats = _synthetic()
    folds = list(walk_forward_splits(labels, **WINDOW))
    groups = {c: ("sig" if c == "info" else "pad") for c in POOL}
    sel = select_in_fold(feats, labels, folds[0], POOL, CFG.model_copy(update={"group_top_k": 1}), groups)
    assert sel.group_frequency["sig"] >= 0.9 and sel.group_frequency.get("pad", 0.0) <= 0.1


def test_the_group_measure_rows_are_purged_by_label_exit_and_averaged_over_repeats():
    """Quant re-review LOW: the rows the joint permutation is scored on are the fold's training rows outside the
    subsample whose label interval [ts_utc, ts_exit] overlaps no subsample row's (fold-internal, by label exit, not
    whole weeks), and the measure averages `permutation_repeats` shuffles."""
    from goldbot.research.discovery import group_permutation_importance, purged_rest
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    ts = pd.DatetimeIndex([t0 + pd.Timedelta(days=d) for d in range(14)])
    te = ts + pd.Timedelta(days=2)                              # every label lives two days
    sub = np.zeros(14, dtype=bool)
    sub[[6, 7]] = True                                          # subsample labels cover day 6 .. day 9
    rest = purged_rest(ts, te, sub)
    # rows 4/5 exit on days 6/7 (overlap), 8/9 enter on days 8/9 (overlap); 0-3 and 10-13 are clean
    assert rest.tolist() == [0, 1, 2, 3, 10, 11, 12, 13]
    week = np.asarray(ts.isocalendar().week, dtype=int)                  # the old +-1 week purge kept far fewer rows
    near = np.isin(week, np.concatenate([week[sub] + d for d in (-1, 0, 1)]))
    assert len(rest) > int((~near).sum())
    assert purged_rest(ts, te, np.zeros(14, dtype=bool)).tolist() == list(range(14))
    rng = np.random.default_rng(0)
    a = rng.normal(size=600)
    y = (rng.random(600) < 1 / (1 + np.exp(-2 * a))).astype(int)
    Z = pd.DataFrame({"a": a, "b": rng.normal(size=600)})

    def predict(X: pd.DataFrame) -> np.ndarray:
        return 1 / (1 + np.exp(-2 * X["a"].to_numpy()))
    g = np.random.default_rng(0)                                # three shuffles drawn in turn from one stream
    one = [group_permutation_importance(predict, Z, y, [[0]], g)[0] for _ in range(3)]
    three = group_permutation_importance(predict, Z, y, [[0]], np.random.default_rng(0), repeats=3)[0]
    assert np.isclose(three, np.mean(one))

"""SEQ-1 pooled TCN (goldbot/research/seq1.py; SYNTH-ml R6, item 17): causal windows of L = 64 closed bars, judged
1h-1d with 1w pooled into 1d, the obj-v1 offset form, fixed seeds, the seed-SD rule, a deterministic CPU artifact
with a sha, and the prefix test that no bar after the decision enters. torch is optional: the numpy half runs
everywhere, the network half skips without it."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import objective as O
from goldbot.research import seq1 as S
from goldbot.research.windows import HoldoutSealed

ROOT = Path(__file__).resolve().parents[2]


def _bars(start: str, n: int, tf: str = "1h", seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(tf)
    ts = pd.date_range(start, periods=n, freq=step, tz="UTC")
    c = 1500 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    o = np.concatenate(([1500.0], c[:-1])) * np.exp(rng.normal(0, 0.0005, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.001, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.001, n)))
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + step, "open": o, "high": h, "low": lo, "close": c})


def _labels(bars: pd.DataFrame, tf: str, *, inst: str = "XAUUSD", every: int = 1, start: int = 80,
            seed: int = 5) -> pd.DataFrame:
    """Long label rows (both sides of T2) at the bar closes, gross R a noisy function of the last return."""
    rng = np.random.default_rng(seed)
    dec = bars["visible_at"].iloc[start::every].reset_index(drop=True)
    close = bars["close"]
    last_ret = np.log((close / close.shift(1)).to_numpy())[start::every]
    sig = np.sign(last_ret)
    rows = []
    for side in (1, -1):
        g = 0.6 * side * sig + rng.normal(0, 0.5, len(dec))
        rows.append(pd.DataFrame({"instrument": inst, "tf": tf, "decision_utc": dec, "side": side, "template": "T2",
                                  "gross_r_raw": g, "horizon_end_utc": dec + pd.Timedelta(days=1), "w": 1.0}))
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------------------------------------------ spec and windows
def test_the_spec_is_pinned_to_the_plan_window_64_causal_judged_1h_to_1d_and_obj_v1():
    assert S.SPEC.window == 64 and S.SPEC.objective_id == O.OBJECTIVE_ID
    assert S.SPEC.judged_tfs == ("1h", "2h", "4h", "1d") and S.judged_tf("1w") == "1d"
    assert S.SPEC.template == "T2" and len(S.SPEC.seeds) >= 5 and len(set(S.SPEC.seeds)) == len(S.SPEC.seeds)
    assert S.receptive_field(S.SPEC) >= S.SPEC.window


@pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m"])
def test_a_sub_hour_timeframe_is_refused_because_no_direction_model_is_judged_below_1h(tf: str):
    with pytest.raises(ValueError, match="1h"):
        S.judged_tf(tf)


def test_a_window_holds_only_bars_visible_by_the_decision_and_is_identical_on_the_prefix():
    bars = _bars("2012-01-02", 400)
    dec = pd.Series(bars["visible_at"].to_numpy()[[150, 200, 399]])
    full, ok = S.bar_windows(bars, dec)
    for i, d in enumerate(dec):
        prefix = bars[bars["visible_at"] <= d]
        w, ok1 = S.bar_windows(prefix, pd.Series([d]))
        assert ok[i] and ok1[0]
        np.testing.assert_array_equal(w[0], full[i])


def test_scrambling_every_bar_after_the_decision_leaves_the_window_unchanged():
    bars = _bars("2012-01-02", 300)
    d = pd.Series([bars["visible_at"].iloc[200]])
    a, _ = S.bar_windows(bars, d)
    late = bars["visible_at"] > d.iloc[0]
    scr = bars.copy()
    scr.loc[late, ["open", "high", "low", "close"]] *= 3.0
    b, _ = S.bar_windows(scr, d)
    np.testing.assert_array_equal(a, b)


def test_a_bar_still_open_at_the_decision_never_enters_even_if_its_open_time_is_before_it():
    bars = _bars("2012-01-02", 300)
    d = bars["visible_at"].iloc[200] - pd.Timedelta(minutes=30)     # mid-bar: bar 200 not closed yet
    w, _ = S.bar_windows(bars, pd.Series([d]))
    w199, _ = S.bar_windows(bars, pd.Series([bars["visible_at"].iloc[199]]))
    np.testing.assert_array_equal(w, w199)


def test_a_short_history_is_left_padded_with_a_zero_valid_channel_and_warm_up_is_not_ok():
    bars = _bars("2012-01-02", 300)
    w, ok = S.bar_windows(bars, pd.Series(bars["visible_at"].to_numpy()[[5, 40]]))
    assert not ok[0]                                  # ATR warm-up not done: no row
    assert ok[1]
    valid = w[1, S.CHANNELS.index("valid")]
    assert valid[-1] == 1 and valid[0] == 0 and valid.sum() == 40   # 41 bars seen, the first has no previous close


def test_bid_ask_bars_are_read_at_mid():
    bars = _bars("2012-01-02", 200)
    ba = bars.drop(columns=["open", "high", "low", "close"])
    for k in ("open", "high", "low", "close"):
        ba[f"bid_{k}"] = bars[k] - 0.1
        ba[f"ask_{k}"] = bars[k] + 0.1
    d = pd.Series([bars["visible_at"].iloc[150]])
    np.testing.assert_allclose(S.bar_windows(ba, d)[0], S.bar_windows(bars, d)[0], atol=1e-6)


def test_design_pairs_both_sides_on_one_window_and_codes_1w_as_1d():
    b1h, b1w = _bars("2012-01-02", 300), _bars("2012-01-02", 140, tf="7D")
    lab = pd.concat([_labels(b1h, "1h"), _labels(b1w, "1w", start=60)], ignore_index=True)
    data = S.design(lab, {("XAUUSD", "1h"): b1h, ("XAUUSD", "1w"): b1w.assign()})
    assert data.X.shape[1:] == (len(S.CHANNELS) + len(S.SPEC.judged_tfs), S.SPEC.window)
    assert data.y.shape == data.w.shape == (len(data.keys), 2)
    assert set(data.keys["judged_tf"]) == {"1h", "1d"}
    one_d = data.keys["judged_tf"].to_numpy() == "1d"
    tf_rows = data.X[:, len(S.CHANNELS):, -1]
    assert (tf_rows[one_d, S.SPEC.judged_tfs.index("1d")] == 1).all()
    np.testing.assert_allclose(data.y, np.clip(data.y, -O.CLIP_R, O.CLIP_R))


def test_design_refuses_a_holdout_row_and_any_row_outside_learn_2010_to_2017():
    bars = _bars("2017-12-20", 400)                    # labels run into January 2018 (confirm)
    lab = _labels(bars, "1h")
    with pytest.raises(ValueError, match="learn"):
        S.design(lab, {("XAUUSD", "1h"): bars})
    hold = _labels(_bars("2025-10-02", 200), "1h")
    with pytest.raises(HoldoutSealed):
        S.design(hold, {("XAUUSD", "1h"): _bars("2025-10-02", 200)})


def test_the_validation_fold_is_the_last_part_of_the_training_span_by_time_with_an_embargo_gap():
    bars = _bars("2012-01-02", 600)
    data = S.design(_labels(bars, "1h"), {("XAUUSD", "1h"): bars})
    fit_rows, val = S.inner_split(data)
    v0 = pd.DatetimeIndex(val.keys["decision_utc"]).min()
    end = pd.DatetimeIndex(fit_rows.keys["horizon_end_utc"])
    assert len(fit_rows) and len(val) and end.max() + pd.Timedelta(days=1) <= v0
    assert 0.1 < len(val) / len(data) < 0.3


def test_the_spec_regularises_with_dropout_weight_decay_and_a_validation_stop_and_five_seeds():
    assert 0 < S.SPEC.dropout < 0.5 and S.SPEC.weight_decay > 0
    assert S.SPEC.patience >= 1 and S.SPEC.max_epochs > S.SPEC.patience and len(S.SPEC.seeds) >= 5


def test_the_seed_rule_is_inconclusive_at_or_above_half_the_minimum_edge():
    ok = S.seed_check([0.010, 0.012, 0.011, 0.013, 0.009])
    assert not ok.inconclusive and ok.threshold == O.MIN_S_R / 2
    bad = S.seed_check([0.00, 0.06, -0.04, 0.03, 0.08])
    assert bad.inconclusive
    assert S.seed_check([0.02]).inconclusive                  # one seed has no spread: never conclusive


def test_the_seed_rule_agrees_with_the_descriptive_battery():
    from goldbot.research import diagnostics
    for v in ([0.01, 0.02, 0.015], [0.0, 0.1, -0.1]):
        assert S.seed_check(v).inconclusive == diagnostics.seed_spread(v).inconclusive


def test_split_purges_every_training_label_that_ends_within_its_embargo_of_the_test_start():
    bars = _bars("2012-01-02", 600)
    data = S.design(_labels(bars, "1h"), {("XAUUSD", "1h"): bars})
    at = pd.Timestamp(data.keys["decision_utc"].iloc[300])
    tr, te = S.split(data, at)
    end = pd.DatetimeIndex(tr.keys["horizon_end_utc"])
    hold = end - pd.DatetimeIndex(tr.keys["decision_utc"])
    assert all(e + max(h, pd.Timedelta(days=1)) <= at for e, h in zip(end, hold, strict=True))
    assert (pd.DatetimeIndex(te.keys["decision_utc"]) >= at).all() and len(tr) and len(te)


def test_seq1_imports_without_torch_and_no_core_module_imports_torch():
    tree = ast.parse((ROOT / "goldbot/research/seq1.py").read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name for n in top for a in n.names} | {n.module for n in top if isinstance(n, ast.ImportFrom)}
    assert not any(str(x).startswith("torch") for x in names)
    users = [p for p in (ROOT / "goldbot").rglob("*.py")
             if p.name != "seq1.py" and "import torch" in p.read_text()]
    assert users == []


def test_seq1_is_on_the_obj_v1_guarded_list():
    assert "goldbot.research.seq1" in O.GUARDED_MODULES
    assert O.guard_violations("goldbot.research.seq1") == []


# ------------------------------------------------------------------------------------------------ the network
def _has_torch() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


needs_torch = pytest.mark.skipif(not _has_torch(),
                                 reason="the optional dl group (torch) is not installed")


@pytest.fixture(scope="module")
def small() -> tuple[S.Seq1Data, S.Seq1Spec]:
    bars = _bars("2012-01-02", 700)
    data = S.design(_labels(bars, "1h"), {("XAUUSD", "1h"): bars})
    return data, S.SPEC.model_copy(update={"max_epochs": 2, "batch": 128, "threads": 1})


@needs_torch
def test_the_network_has_about_sixty_thousand_parameters():
    assert 45_000 <= S.param_count(S.SPEC) <= 75_000


@needs_torch
def test_the_convolutions_are_causal_a_later_position_never_moves_an_earlier_output():
    import torch
    net = S.build_net(S.SPEC, seed=1).eval()            # eval: dropout off, as when scoring
    x = torch.randn(2, S.n_inputs(S.SPEC), S.SPEC.window)
    y = x.clone()
    y[:, :, 40:] += 5.0
    with torch.no_grad():
        a, b = net.sequence(x), net.sequence(y)
    assert torch.equal(a[:, :, :40], b[:, :, :40])
    assert not torch.equal(a[:, :, 40:], b[:, :, 40:])


@needs_torch
def test_a_fit_is_deterministic_on_cpu_and_its_sha_names_the_weights(small):
    data, spec = small
    a, b = S.fit(data, seed=spec.seeds[0], spec=spec), S.fit(data, seed=spec.seeds[0], spec=spec)
    c = S.fit(data, seed=spec.seeds[1], spec=spec)
    assert a.sha256 == b.sha256 and a.sha256 != c.sha256
    assert a.sha256 == a.compute_sha256() and a.objective_id == O.OBJECTIVE_ID


@needs_torch
def test_the_fit_stops_on_the_validation_loss_and_records_the_best_epoch(small):
    data, _ = small
    spec = S.SPEC.model_copy(update={"max_epochs": 12, "patience": 2, "batch": 128, "threads": 1})
    art = S.fit(data, seed=spec.seeds[0], spec=spec)
    assert 1 <= art.best_epoch <= 12 and np.isfinite(art.val_loss) and art.n_val > 0


@needs_torch
def test_the_artifact_round_trips_through_json_and_refuses_a_tampered_blob(small, tmp_path: Path):
    data, spec = small
    art = S.fit(data, seed=spec.seeds[0], spec=spec)
    p = S.save(art, tmp_path / "seq1.json")
    back = S.load(p)
    np.testing.assert_array_equal(S.score(back, data.X[:50]), S.score(art, data.X[:50]))
    raw = p.read_text().replace(art.sha256, "0" * 64)
    p.write_text(raw)
    with pytest.raises(ValueError, match="sha"):
        S.load(p)


@needs_torch
def test_score_row_matches_the_batch_score(small):
    data, spec = small
    art = S.fit(data, seed=spec.seeds[0], spec=spec)
    batch = S.score(art, data.X[:20])
    for i in range(20):
        np.testing.assert_allclose(S.score_row(art, data.X[i]), batch[i], rtol=1e-5, atol=1e-6)


@needs_torch
def test_the_full_path_from_bars_to_score_ignores_bars_after_the_decision(small):
    data, spec = small
    art = S.fit(data, seed=spec.seeds[0], spec=spec)
    bars = _bars("2012-01-02", 700)
    d = pd.Series([bars["visible_at"].iloc[500]])
    w_full, _ = S.bar_windows(bars, d)
    w_pref, _ = S.bar_windows(bars[bars["visible_at"] <= d.iloc[0]], d)
    tf = S.tf_channels(["1h"])
    np.testing.assert_array_equal(S.score(art, np.concatenate([w_full, tf], axis=1)),
                                  S.score(art, np.concatenate([w_pref, tf], axis=1)))


@needs_torch
def test_fitting_learns_a_planted_signal_in_the_offset_form(small):
    data, _ = small
    spec = S.SPEC.model_copy(update={"max_epochs": 15, "patience": 15, "batch": 64, "threads": 1})
    art = S.fit(data, seed=spec.seeds[0], spec=spec)
    pred = S.score(art, data.X)
    for j in (0, 1):
        assert np.corrcoef(pred[:, j], data.y[:, j])[0, 1] > 0.3


@needs_torch
def test_the_walk_forward_read_runs_every_seed_and_marks_a_wide_seed_spread_inconclusive(small):
    data, spec = small
    spec = spec.model_copy(update={"seeds": spec.seeds[:3]})
    at = pd.Timestamp(data.keys["decision_utc"].iloc[400])
    tr, te = S.split(data, at)
    rows = S.judged_rows(te, cost=np.full((len(te), 2), 0.05))
    rd = S.read(tr, te, rows, spec=spec, boot=0)
    assert rd.keep_ensemble.shape == (len(rows),) and rd.keep_ensemble.dtype == bool
    assert len(rd.seeds) == 3 and len(rd.shas) == 3 and len(rd.s_by_seed) == 3
    assert rd.verdict in ("READ", "INCONCLUSIVE")
    assert rd.verdict == ("INCONCLUSIVE" if rd.seed.inconclusive else "READ")
    assert set(rd.per_tf) == {"1h"}
    assert S.seed_check([0.0, 0.2, -0.2]).inconclusive


def test_openmp_runtimes_imported_before_torch_are_named_in_import_order():
    assert S.omp_before_torch(["numpy", "sklearn", "lightgbm", "torch"]) == ["sklearn", "lightgbm"]
    assert S.omp_before_torch(["torch", "sklearn", "lightgbm"]) == []
    assert S.omp_before_torch(["sklearn"]) == []                 # torch not imported yet: nothing clashes


@needs_torch
def test_a_multi_threaded_fit_after_another_openmp_runtime_is_refused_not_a_segfault(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(S, "omp_before_torch", lambda modules=None: ["sklearn"])
    monkeypatch.setattr(S.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="import torch first"), S.TorchState(2):
        pass
    with S.TorchState(1):                                         # one thread never starts a second runtime's pool
        pass

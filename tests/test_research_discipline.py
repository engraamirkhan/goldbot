"""Research discipline (docs/proposals/2026-10-design-improvements.md, first batch P1-P3): cross-fitted calibration,
the cost hurdle, the design's gates, no deflated Sharpe on small samples, the quarterly trial budget, the holdout,
the paused label grid, side-aligned meta-model inputs and the declared per-family feature lists."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.costs import CONTRACT_OZ, prior_extra_cost_usd, settings_extra_cost_usd
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.registry import side_align, signed_neutral
from goldbot.research.director import quarter_usage
from goldbot.research.gates import MIN_CANDIDATES, MIN_TEST_FOLD, research_gates
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, expectancy, summarize
from goldbot.research.model import MAX_FEATURES, MetaLabelModel, PlattCalibrator, fit_calibrator
from goldbot.research.pipeline import (
    MIN_CALIBRATION_ROWS,
    _cross_fitted,
    _in_window,
    _zero_spread,
    build_decision_frame,
    model_inputs,
)
from goldbot.research.population import Member, Population
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry, quarter_of
from goldbot.research.walkforward import Fold
from goldbot.specialists import SPECIALISTS

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------- P1: cross-fitting
def _folds(sizes: list[int]) -> list[Fold]:
    out, start = [], 0
    for k, n in enumerate(sizes):
        idx = np.arange(start, start + n)
        out.append(Fold(k=k, train_idx=np.arange(0, start), test_idx=idx, test_start=pd.Timestamp("2020-01-01", tz="UTC"),
                        test_end=pd.Timestamp("2020-01-02", tz="UTC")))
        start += n
    return out


def test_cross_fitted_calibration_never_sees_the_fold_it_scores():
    rng = np.random.default_rng(0)
    folds = _folds([30, 25, 300, 200])
    n = sum(len(f.test_idx) for f in folds)
    p_raw, y = rng.random(n), (rng.random(n) < 0.4).astype(int)
    p = _cross_fitted(p_raw, y, folds)
    # the first two folds have fewer than MIN_CALIBRATION_ROWS earlier rows (0, then 30): never selected
    assert MIN_CALIBRATION_ROWS > 30 and np.isnan(p[:55]).all() and np.isfinite(p[55:]).all()
    # flipping every outcome inside fold 3 leaves fold 3's probabilities unchanged (but moves fold 4's)
    y2 = y.copy()
    y2[55:355] = 1 - y2[55:355]
    p2 = _cross_fitted(p_raw, y2, folds)
    assert np.array_equal(p[55:355], p2[55:355]) and not np.allclose(p[355:], p2[355:])


def test_in_sample_isotonic_selection_is_what_cross_fitting_removes():
    """Pure noise: isotonic fitted on the rows it then selects from finds 'winners'; cross-fitted selection does not."""
    rng = np.random.default_rng(7)
    folds = _folds([150] * 8)
    n = 1200
    p_raw, y = rng.random(n), (rng.random(n) < 0.4).astype(int)
    thr = 0.6
    in_sample = fit_calibrator(p_raw, y).predict(p_raw)
    hit_in_sample = y[in_sample > thr].mean() if (in_sample > thr).any() else np.nan
    crossed = _cross_fitted(p_raw, y, folds)
    taken = np.isfinite(crossed) & (crossed > thr)
    assert np.isfinite(hit_in_sample) and hit_in_sample > thr          # the selection "works" on noise in sample
    assert taken.sum() < 0.1 * n                                       # out of sample it rarely clears the bar


def test_small_calibration_sets_use_platt_scaling():
    rng = np.random.default_rng(1)
    p, y = rng.random(200), (rng.random(200) < 0.5).astype(int)
    assert isinstance(fit_calibrator(p, y), PlattCalibrator)
    assert not isinstance(fit_calibrator(np.tile(p, 3), np.tile(y, 3)), PlattCalibrator)
    one_class = PlattCalibrator().fit(p[:10], np.ones(10, dtype=int)).predict(p[:3])
    assert np.allclose(one_class, 1 - 1e-4, atol=1e-3)


def test_dsr_is_not_reported_on_small_samples():
    rng = np.random.default_rng(2)
    few = pd.DataFrame({"ret": rng.normal(0.001, 0.002, 16)})
    many = pd.DataFrame({"ret": rng.normal(0.001, 0.002, MIN_TRADES_FOR_DSR)})
    assert summarize(few, 50)["dsr"] is None
    assert 0.0 <= summarize(many, 50)["dsr"] <= 1.0


def test_expectancy_in_r_with_t_stat():
    ret = np.array([0.002, -0.001, 0.002, -0.001])
    risk = np.full(4, 0.001)
    s = expectancy(ret, risk)
    assert s["n"] == 4 and s["mean_r"] == pytest.approx(0.5) and s["hit_rate"] == 0.5
    assert s["t_stat"] == pytest.approx(0.5 / np.std([2, -1, 2, -1], ddof=1) * 2)
    assert expectancy(np.array([]), np.array([]))["n"] == 0


def test_cost_hurdle_is_slippage_and_commission_not_the_spread_again():
    # labels already pay the spread (ask in, bid out); the extra cost is slippage both ways + commission both sides
    assert prior_extra_cost_usd(0.15, 3.5) == pytest.approx(2 * 0.15 + 2 * 3.5 / CONTRACT_OZ)
    s = load_settings()
    assert settings_extra_cost_usd(s) == pytest.approx(2 * s.costs.slippage_prior_usd
                                                        + 2 * s.costs.commission_per_lot_side_usd["icm"] / CONTRACT_OZ)


def test_zero_spread_bars_are_mid_prices():
    b = pd.DataFrame({"bid_high": [10.0], "ask_high": [10.4], "bid_low": [9.0], "ask_low": [9.2],
                      "bid_close": [9.5], "ask_close": [9.9]})
    z = _zero_spread(b)
    assert z["bid_high"].iloc[0] == z["ask_high"].iloc[0] == pytest.approx(10.2)
    assert z["bid_close"].iloc[0] == z["ask_close"].iloc[0] == pytest.approx(9.7)


# ---------------------------------------------------------------------------------------------- P1: design gates
def _taken(years: dict[int, tuple[int, float]]) -> pd.DataFrame:
    rows = []
    for y, (n, r) in years.items():
        rows += [{"ts_utc": pd.Timestamp(f"{y}-03-01", tz="UTC") + pd.Timedelta(days=i), "ret": r} for i in range(n)]
    return pd.DataFrame(rows)


def test_research_gates_need_candidates_fold_size_and_three_positive_years():
    ok = research_gates(MIN_CANDIDATES, [MIN_TEST_FOLD] * 10, _taken({2019: (12, 0.001), 2021: (15, 0.001), 2024: (10, 0.002)}))
    assert ok["passed"] and [c["name"] for c in ok["checks"]] == ["candidates", "per_fold", "positive_years"]
    few = research_gates(MIN_CANDIDATES - 1, [MIN_TEST_FOLD] * 10, _taken({2019: (12, 0.001), 2021: (15, 0.001), 2024: (10, 0.002)}))
    assert not few["passed"] and not few["checks"][0]["passed"]
    thin_fold = research_gates(5000, [100, MIN_TEST_FOLD - 1], _taken({2019: (12, 0.001), 2021: (15, 0.001), 2024: (10, 0.002)}))
    assert not thin_fold["passed"] and not thin_fold["checks"][1]["passed"]
    no_chop = research_gates(5000, [100], _taken({2018: (12, 0.001), 2019: (15, 0.001), 2024: (10, 0.002)}))
    assert not no_chop["passed"]                                        # none of the three is 2021 or 2022
    lucky = research_gates(5000, [100], _taken({2019: (12, 0.001), 2021: (3, 0.01), 2024: (10, 0.002)}))
    assert not lucky["passed"]                                          # three trades are not a positive year
    assert not research_gates(5000, [], _taken({}))["passed"]


# ---------------------------------------------------------------------------------------------- P2: budget, holdout
def _row(reg: TrialRegistry, *, quarter: str | None = None, status: str = "evaluated", config: dict | None = None,
         gates: bool | None = None) -> None:
    results = {} if gates is None else {"gates": {"passed": gates, "checks": []}}
    reg.record(agent_id="a", family="session_open", config=config or {"x": 1}, feature_version="f", rationale="r",
               results=results, status=status, budget_quarter=quarter)


def test_quarterly_trial_budget_is_enforced_with_a_clear_message(tmp_path):
    import json
    reg = TrialRegistry(tmp_path / "r.jsonl")
    q = quarter_of(NOW)
    assert q == "2026Q4"
    stamps = ["2026-09-30T23:59:00+00:00"] + ["2026-10-02T10:00:00+00:00"] * 15 + ["2026-12-31T23:00:00+00:00"] * 3 \
        + ["2027-01-01T00:00:00+00:00", "not a time"]
    reg.path.write_text("".join(json.dumps({"ts": t, "status": st}) + "\n"
                                for t, st in zip(stamps, ["evaluated"] * 16 + ["holdout", "dry_run", "evaluated"] + ["x"] * 2)))
    # every row stamped in the quarter counts, whatever its status: the director plans from the same number
    assert reg.budget_used(q) == 18 and quarter_usage(reg._rows(), pd.Timestamp(NOW), 20) == (q, 18, 2)
    assert reg.check_budget(2, 20, NOW) == q
    with pytest.raises(TrialBudgetExceeded, match=r"2026Q4 allows 20 pre-registered trials, 18 already run, 3 requested"):
        reg.check_budget(3, 20, NOW)


def test_registry_lock_is_exclusive_and_recovers_from_a_stale_lock(tmp_path):
    import os
    import time
    reg = TrialRegistry(tmp_path / "r.jsonl")
    lock = tmp_path / "r.jsonl.lock"
    with reg.locked():
        assert lock.exists()
        with pytest.raises(TimeoutError):
            with reg.locked(wait_s=0.6):
                pass
    assert not lock.exists()
    lock.write_text("crashed writer")
    old = time.time() - 10
    os.utime(lock, (old, old))
    with reg.locked(stale_s=5):                                          # taken over: its writer is gone
        assert lock.read_text().split()[0] == str(os.getpid())
    assert not lock.exists()


def test_holdout_is_scored_once_and_gates_are_looked_up_by_exact_config(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    cfg = {"band_z": 2.0}
    assert not reg.holdout_scored("session_open", cfg)
    _row(reg, status="holdout", config=cfg)
    assert reg.holdout_scored("session_open", cfg) and not reg.holdout_scored("session_open", {"band_z": 2.5})
    assert not reg.passed_gates("session_open", cfg)                    # a holdout scoring is not a research pass
    _row(reg, config=cfg, gates=False)
    assert not reg.passed_gates("session_open", cfg)
    _row(reg, config={"band_z": 2.5}, gates=True)
    assert reg.passed_gates("session_open", {"band_z": 2.5}) and not reg.passed_gates("trend", {"band_z": 2.5})


def test_settings_pause_the_label_grid_and_hold_out_the_last_year():
    r = load_settings().research
    assert r.label_grid_paused and r.trial_budget_quarter == 20
    assert r.holdout_window() == (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC"))


def test_holdout_window_drops_every_label_whose_life_touches_it():
    labels = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-09-01", "2025-09-30 20:00", "2025-12-01", "2026-10-02"], utc=True, format="ISO8601"),
                           "ts_exit": pd.to_datetime(["2025-09-02", "2025-10-01 02:00", "2025-12-02", "2026-10-03"], utc=True, format="ISO8601")})
    w = (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC"))
    assert _in_window(labels, w).tolist() == [False, True, True, False]


def test_live_promotion_waits_for_a_passed_research_trial(tmp_path):
    def run(passed: bool) -> tuple[dict, Population]:
        pop, book = Population(tmp_path / f"p{passed}.json"), ShadowBook(tmp_path / f"b{passed}")
        cfg = dict(SPECIALISTS["session_open"].default_config, asia_range_max_atr_d=1.0)
        now = pd.Timestamp(NOW)
        pop.members["strong"] = Member(agent_id="strong", family="session_open", config=cfg, generation=1,
                                       created_utc=now - pd.Timedelta(days=200), status_since_utc=now - pd.Timedelta(days=200))
        from tests.test_population import _add_trades, _good
        _add_trades(book, "strong", _good(150))
        return pop.tournament(book, now, research_passed=lambda m: passed), pop
    out, pop = run(False)
    assert out["promoted"] == [] and out["awaiting_research"] == ["strong"] and pop.members["strong"].status == "shadow"
    out, pop = run(True)
    assert out["promoted"] == ["strong"] and pop.members["strong"].status == "live"


# ---------------------------------------------------------------------------------------------- P3: model inputs
def test_side_alignment_flips_signed_columns_around_their_neutral_value():
    X = pd.DataFrame({"side": [1, -1], "ret_4": [0.003, 0.003], "rsi14": [70.0, 70.0], "h4_slope_ema50": [0.2, 0.2],
                      "atr14": [5.0, 5.0], "donchian_pos_20": [0.9, 0.9]})
    Z = side_align(X)
    assert Z["ret_4"].tolist() == [0.003, -0.003]
    assert Z["rsi14"].tolist() == [20.0, -20.0]                         # centred at 50
    assert Z["h4_slope_ema50"].tolist() == [0.2, -0.2]                  # context columns too
    assert Z["donchian_pos_20"].tolist() == pytest.approx([0.4, -0.4])
    assert Z["atr14"].tolist() == [5.0, 5.0] and Z["side"].tolist() == [1, -1]
    assert signed_neutral("atr14") is None and signed_neutral("d1_dist_ema50_atr") == 0.0
    assert side_align(X.drop(columns=["side"])) is not None and "side" not in side_align(X.drop(columns=["side"]))


def test_side_aligned_model_learns_a_direction_dependent_outcome():
    """The outcome depends on side x ret_4 (momentum in the trade's favour wins). Side-aligned, one split finds it."""
    rng = np.random.default_rng(3)
    n = 1200
    X = pd.DataFrame({"side": rng.choice([-1, 1], n), "ret_4": rng.normal(0, 1, n), "atr14": rng.random(n)})
    y = pd.Series(((X["side"] * X["ret_4"]) + rng.normal(0, 0.5, n) > 0).astype(int))
    from sklearn.metrics import roc_auc_score
    m = MetaLabelModel(feature_names=["side", "ret_4", "atr14"], params={**MetaLabelModel(feature_names=[]).params,
                                                                         "n_estimators": 50}).fit(X[:800], y[:800])
    assert m.side_aligned and roc_auc_score(y[800:], m.predict_raw(X[800:])) > 0.85
    with pytest.raises(ValueError, match="capped at 40"):
        MetaLabelModel(feature_names=[f"f{i}" for i in range(MAX_FEATURES + 1)]).fit(X, y)


@pytest.fixture(scope="module")
def bars_1m() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2024-01-01", "2024-03-15", ticks_per_minute=1, seed=21))


@pytest.mark.parametrize("family", sorted(SPECIALISTS))
def test_declared_feature_lists_exist_and_fit_the_cap(family, bars_1m):
    cls = SPECIALISTS[family]
    dec = resample_bars(bars_1m, cls.timeframe).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(bars_1m, x) for x in context_tfs(cls.timeframe)}
    _, X = build_decision_frame(dec, context)
    declared = list(cls.model_features)
    assert declared and len(declared) == len(set(declared)) <= MAX_FEATURES - 1
    missing = [c for c in declared if c not in X.columns]
    assert missing == []
    assert any(c.startswith(("h1_", "h4_", "d1_")) for c in declared)    # higher-timeframe context is declared
    cols = model_inputs(cls(), [c for c in X.columns if c != "ts_utc"] + ["side"])
    assert cols[0] == "side" and cols[1:] == declared and len(cols) <= MAX_FEATURES
    clone = model_inputs(cls(feature_seed=5), [c for c in X.columns if c != "ts_utc"])
    assert clone[0] == "side" and len(clone) == MAX_FEATURES and clone[1:] != declared

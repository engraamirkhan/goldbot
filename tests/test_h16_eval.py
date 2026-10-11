"""H-16's evaluation path (E-4, docs/research/preregistration-2027Q2-draft.md): the opportunity score against the
HAR-only range forecast on synthetic bars, read by the pre-registered rule and recorded as exactly one trial. No real
data, no real registry, no holdout read."""
import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.macro import fred_frame
from goldbot.features import day_type as dt
from goldbot.features import registry as feat_registry
from goldbot.research import h16
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry, is_trial

ROOT = Path(__file__).resolve().parents[1]
START, END = "2018-01-01", "2020-07-01"
HOLDOUT = "2020-04-01"
MIN_ROWS = 120                   # the synthetic history is short; the CLI always uses dt.MIN_FIT_ROWS (500)
N_BOOT = 50


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _event_days(seed: int = 11) -> pd.DatetimeIndex:
    """About a third of the weekdays carry one tier-1 release (13:30 UTC)."""
    days = pd.bdate_range(START, END)
    return days[np.random.default_rng(seed).random(len(days)) < 0.33]


def _bars(beta: float, seed: int = 7) -> pd.DataFrame:
    """15m mid bars (weekend closure removed) whose daily volatility is persistent (log-vol AR(1)) and, with `beta`,
    raised by exp(beta) on the tier-1 event days: a planted range signal only the full model can see."""
    ts = pd.date_range(START, END, freq="15min", tz="UTC", inclusive="left")
    wd, hr = ts.dayofweek, ts.hour
    ts = ts[~((wd == 5) | ((wd == 4) & (hr >= 22)) | ((wd == 6) & (hr < 22)))]
    rng = np.random.default_rng(seed)
    labels, code = np.unique(np.asarray(dt.opportunity_day(ts)), return_inverse=True)
    lv = np.zeros(len(labels))
    for i in range(1, len(lv)):
        lv[i] = 0.8 * lv[i - 1] + 0.25 * rng.standard_normal()
    ev = np.isin(labels.astype("datetime64[D]"), _event_days().to_numpy().astype("datetime64[D]"))
    sig = 0.0008 * np.exp(lv + beta * ev)[code]
    close = 2000 * np.exp(np.cumsum(sig * rng.standard_normal(len(ts))))
    open_ = np.concatenate(([2000.0], close[:-1]))
    high = np.maximum(open_, close) * (1 + np.abs(sig * 0.5 * rng.standard_normal(len(ts))))
    low = np.minimum(open_, close) * (1 - np.abs(sig * 0.5 * rng.standard_normal(len(ts))))
    return pd.DataFrame({"ts_utc": ts, "open": open_, "high": high, "low": low, "close": close,
                         "spread": 0.2, "tick_count": 10})


def _macro(gap: tuple[str, str] | None = ("2019-06-03", "2019-06-21")) -> pd.DataFrame:
    """GVZ (noise around 16) on business days, with an optional gap that makes some days stale."""
    vd = pd.bdate_range("2017-12-01", END)
    if gap is not None:
        vd = vd[(vd < pd.Timestamp(gap[0])) | (vd > pd.Timestamp(gap[1]))]
    vals = 16 + np.random.default_rng(5).standard_normal(len(vd))
    return fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": vals}), pd.Timestamp("2021-01-01"))


def _calendar() -> pd.DataFrame:
    rows = []
    for d in _event_days():
        ev = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=13, minutes=30)
        rows.append({"event_id": f"cpi:{d.date()}", "event_type": "cpi", "tier": 1, "release_id": -1, "event_utc": ev,
                     "status": "scheduled", "available_utc": ev - pd.Timedelta(days=7),
                     "ts_utc": ev - pd.Timedelta(days=7), "vintage": pd.Timestamp("2021-01-01"), "source": "fred"})
    rows.append({"event_id": "cpi:2017-12-12", "event_type": "cpi", "tier": 1, "release_id": -1,
                 "event_utc": _utc("2017-12-12 13:30"), "status": "scheduled", "available_utc": _utc("2017-12-01"),
                 "ts_utc": _utc("2017-12-01"), "vintage": pd.Timestamp("2021-01-01"), "source": "fred"})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def planted() -> pd.DataFrame:
    return _bars(beta=0.8)


@pytest.fixture(scope="module")
def noise() -> pd.DataFrame:
    return _bars(beta=0.0)


def _evaluate(bars: pd.DataFrame, **kw):
    kw.setdefault("holdout_from", HOLDOUT)
    return h16.evaluate_h16(bars, _macro(), _calendar(), min_rows=MIN_ROWS, n_boot=N_BOOT, **kw)


# --------------------------------------------------------------------------- the reading rule
@pytest.mark.parametrize("rho_full, rho_har, top, bottom, verdict", [
    (0.35, 0.30, 1.5, 1.0, "continue"),          # both exactly at the bar (0.35 - 0.30 is 0.0499... in binary)
    (0.80, 0.10, 3.0, 1.0, "continue"),
    (0.3499, 0.30, 3.0, 1.0, "fail"),            # rho gain just below 0.05
    (0.80, 0.10, 1.4999, 1.0, "fail"),           # quintile ratio just below 1.5
    (0.20, 0.30, 3.0, 1.0, "fail"),              # the full score is worse
    (float("nan"), 0.30, 3.0, 1.0, "fail"),
    (0.80, 0.10, float("nan"), 1.0, "fail"),
    (0.80, 0.10, 3.0, 0.0, "fail"),              # no bottom-quintile range: no ratio
])
def test_the_reading_rule_needs_both_the_rho_gain_and_the_quintile_ratio_inclusive_at_the_bar(
        rho_full, rho_har, top, bottom, verdict):
    assert h16.read_rule(rho_full, rho_har, top, bottom) == verdict


# --------------------------------------------------------------------------- planted signal and noise
def test_a_planted_range_signal_in_the_full_inputs_reads_continue(planted):
    res, frame = _evaluate(planted)
    assert res.verdict == "continue"
    assert res.rho_gain >= h16.RHO_GAIN and res.quintile_ratio >= h16.QUINTILE_RATIO
    assert res.n == int(frame["in_eval"].sum()) > 300
    assert res.drop_one["tier1_count"] > 0.05            # the planted input carries the gain
    assert res.bootstrap_gain_ci95 is not None and res.bootstrap_gain_ci95[0] > 0


def test_noise_in_the_full_inputs_reads_fail(noise):
    res, _ = _evaluate(noise)
    assert res.verdict == "fail"
    assert res.rho_gain < h16.RHO_GAIN


def test_gvz_stale_days_are_dropped_from_both_models_and_counted(planted):
    res, frame = _evaluate(planted)
    stale = frame["gvz_stale"].to_numpy() == 1.0
    assert res.dropped_gvz_stale > 0 and not (frame["in_eval"].to_numpy() & stale).any()
    assert res.n_days_oos == res.n + res.dropped_gvz_stale + res.dropped_no_har + res.dropped_no_full
    oos = frame.loc[frame["in_eval"], "day"]
    assert str(pd.Timestamp(oos.min()).to_period("M")) == res.first_oos_month


def test_the_baseline_is_the_published_har_forecast_shifted_to_its_target_day(planted):
    _, frame = _evaluate(planted)
    fc = dt.har_forecast(frame, min_rows=MIN_ROWS)
    published = np.log(fc["hat"].to_numpy())
    assert np.allclose(frame["har_log"].to_numpy()[1:], published[:-1], equal_nan=True)
    assert np.isfinite(frame["har_log"]).sum() > 300


def test_the_first_oos_month_is_the_first_month_whose_fit_has_enough_rows_and_a_frozen_mismatch_is_refused(planted):
    res, frame = _evaluate(planted)
    refits = dt.monthly_refits(frame)
    months = h16.refit_months(refits)
    i = list(map(str, months)).index(res.first_oos_month)
    assert dt.fit_har(frame, refits[i], min_rows=MIN_ROWS).coef is not None
    assert dt.fit_har(frame, refits[i - 1], min_rows=MIN_ROWS).coef is None
    assert _evaluate(planted, frozen_first_oos_month=res.first_oos_month)[0] == res
    wrong = str(pd.Period(res.first_oos_month, "M") + 1)
    with pytest.raises(ValueError, match="frozen first out-of-sample month"):
        _evaluate(planted, frozen_first_oos_month=wrong)


# --------------------------------------------------------------------------- the holdout cut
def test_no_row_at_or_after_the_holdout_reaches_any_feature(planted, monkeypatch):
    at = _utc(HOLDOUT)
    seen: dict[str, int] = {}

    def guard(name, fn, pick):
        def wrapped(*a, **k):
            frame = pick(a)
            if frame is not None and len(frame):
                col = "available_utc" if "available_utc" in frame else "ts_utc"
                assert pd.DatetimeIndex(pd.to_datetime(frame[col], utc=True)).max() < at, f"{name} saw the holdout"
                if "event_utc" in frame:
                    assert pd.DatetimeIndex(pd.to_datetime(frame["event_utc"], utc=True)).max() < at
            seen[name] = seen.get(name, 0) + 1
            return fn(*a, **k)
        monkeypatch.setattr(dt, name, wrapped)
    guard("daily_bars", dt.daily_bars, lambda a: a[0])
    guard("reopen_gap", dt.reopen_gap, lambda a: a[0])
    guard("gvz_columns", dt.gvz_columns, lambda a: a[1])
    guard("tier1_columns", dt.tier1_columns, lambda a: a[1])
    assert pd.DatetimeIndex(planted["ts_utc"]).max() > at and _calendar()["event_utc"].max() > at
    res, frame = _evaluate(planted)
    assert set(seen) == {"daily_bars", "reopen_gap", "gvz_columns", "tier1_columns"}
    assert pd.DatetimeIndex(frame["close_utc"]).max() <= at
    assert pd.Timestamp(res.bars_to) < at


def test_prices_after_the_holdout_cannot_change_the_result(planted):
    poisoned = planted.copy()
    after = pd.DatetimeIndex(poisoned["ts_utc"]) >= _utc(HOLDOUT)
    poisoned.loc[after, ["open", "high", "low", "close"]] *= 50.0
    pre_cut = planted.loc[~after].reset_index(drop=True)
    assert _evaluate(poisoned)[0] == _evaluate(pre_cut)[0]


# --------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("cut", ["2019-05-31 22:00", "2019-09-18 15:15", "2019-12-30 03:00", "2020-02-03 06:45"])
def test_every_evaluation_row_closed_before_a_cut_is_unchanged_when_everything_after_it_is_removed(planted, cut):
    full = h16.evaluation_frame(planted, _macro(), _calendar(), min_rows=MIN_ROWS)
    c = _utc(cut)
    short = h16.evaluation_frame(h16.cut_before_holdout(planted, c),
                                 h16.cut_before_holdout(_macro(), c, ("available_utc",)),
                                 h16.cut_before_holdout(_calendar(), c, ("available_utc", "event_utc")),
                                 min_rows=MIN_ROWS)
    cols = ["day", "y", *h16.FULL_REGRESSORS, "gvz_stale", "har_log", "full_log"]
    a = full.loc[pd.DatetimeIndex(full["close_utc"]) <= c, cols].reset_index(drop=True)
    b = short.loc[pd.DatetimeIndex(short["close_utc"]) <= c, cols].reset_index(drop=True)
    assert len(a) > 200 and np.isfinite(a["full_log"]).sum() > 50
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)
    # the day in progress at the cut (when it is not a stub): its inputs and forecasts are already fixed
    known = ["day", *h16.FULL_REGRESSORS, "gvz_stale", "har_log", "full_log"]
    a = full.loc[pd.DatetimeIndex(full["decision_utc"]) <= c, known]
    b = short.loc[pd.DatetimeIndex(short["decision_utc"]) <= c, known]
    a = a[a["day"].isin(b["day"])].reset_index(drop=True)          # a cut before 12 h of the day makes it a stub
    assert len(a) == len(b) > 200
    b = b.reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize("day", ["2019-07-01", "2019-10-16", "2020-03-02"])
def test_no_full_score_input_or_forecast_moves_when_prices_after_0630_utc_on_the_day_change(planted, day):
    at = _utc(day) + h16.DECISION_UTC
    moved = planted.copy()
    after = np.asarray(pd.DatetimeIndex(moved["ts_utc"]) >= at)
    k = 1 + 0.5 * np.random.default_rng(1).random(int(after.sum()))
    moved.loc[after, "high"] = moved.loc[after, "high"] * k
    moved.loc[after, "low"] = moved.loc[after, "low"] / k
    cols = ["day", *h16.FULL_REGRESSORS, "gvz_stale", "har_log", "full_log"]
    a = h16.evaluation_frame(planted, _macro(), _calendar(), min_rows=MIN_ROWS)
    b = h16.evaluation_frame(moved, _macro(), _calendar(), min_rows=MIN_ROWS)
    a = a.loc[pd.DatetimeIndex(a["decision_utc"]) <= at, cols].reset_index(drop=True)
    b = b.loc[pd.DatetimeIndex(b["decision_utc"]) <= at, cols].reset_index(drop=True)
    assert a["day"].iloc[-1] == pd.Timestamp(day) and np.isfinite(a["full_log"].iloc[-1])
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)


def test_full_score_inputs_are_known_at_0630_utc_on_the_day(planted):
    _, frame = _evaluate(planted)
    dec = pd.DatetimeIndex(frame["decision_utc"])
    assert (dec == pd.DatetimeIndex(frame["day"]).tz_localize("UTC") + pd.Timedelta(hours=6, minutes=30)).all()
    assert (dec < pd.DatetimeIndex(frame["close_utc"])).all()
    ev = set(_event_days().date)
    known = frame["tier1_count"].notna()
    assert ((frame.loc[known, "tier1_count"] > 0) == frame.loc[known, "day"].dt.date.isin(ev)).all()


# --------------------------------------------------------------------------- one trial in the registry
@pytest.fixture(autouse=True)
def restore_features():
    """h16_feature_version registers the opt-in day_type feature: restore the registry after every test."""
    feats, signed = dict(feat_registry.FEATURES), dict(feat_registry.SIGNED)
    yield
    feat_registry.FEATURES.clear()
    feat_registry.FEATURES.update(feats)
    feat_registry.SIGNED.clear()
    feat_registry.SIGNED.update(signed)


@pytest.fixture(scope="module")
def frozen(planted) -> tuple[str, int]:
    """(first OOS month, evaluable days) as counted before the freeze on the synthetic bars."""
    res, _ = _evaluate(planted)
    return res.first_oos_month, res.n


def _config(frozen: tuple[str, int], **over) -> dict:
    month, n = frozen
    return h16.h16_config(over.pop("month", month), over.pop("expected", n), min_rows=MIN_ROWS)


def _prereg(reg: TrialRegistry, config: dict, fv: str | None = None) -> dict:
    return reg.preregister(agent_id=h16.AGENT_ID, family=h16.FAMILY, config=config,
                           feature_version=fv or h16.h16_feature_version(), rationale="H-16",
                           reading_rule=h16.READING_RULE)


def _run(reg: TrialRegistry, bars: pd.DataFrame, **kw):
    kw.setdefault("cap", 20)
    kw.setdefault("reserved", 0)
    return h16.run_trial(reg, bars, _macro(), _calendar(), holdout_from=HOLDOUT, retired=set(),
                         rationale="E-4 synthetic", min_rows=MIN_ROWS, n_boot=N_BOOT, **kw)


def _trials(reg: TrialRegistry) -> list[dict]:
    return [r for r in (json.loads(x) for x in reg.path.read_text().splitlines() if x.strip()) if is_trial(r)]


def test_the_frozen_month_and_day_count_are_part_of_the_preregistered_hash(frozen):
    from goldbot.research.registry import config_hash
    base = _config(frozen)
    assert base["first_oos_month"] == frozen[0] and base["expected_eval_days"] == frozen[1]
    assert base["gvz_series"] == "GVZCLS" and base["feature_names"] == ["day_type"]
    assert config_hash(base) != config_hash(_config(frozen, month="2012-05"))
    assert config_hash(base) != config_hash(_config(frozen, expected=frozen[1] + 1))


def test_the_run_writes_exactly_one_trial_row_with_the_reading_and_counts(planted, tmp_path, frozen):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    pre = _prereg(reg, _config(frozen))
    row, res = _run(reg, planted)
    trials = _trials(reg)
    assert len(trials) == 1 and reg.n_trials == 1
    r = trials[0]
    assert r["family"] == h16.FAMILY and r["status"] == "evaluated"
    assert r["feature_version"] == h16.h16_feature_version() == pre["feature_version"]
    assert r["config"] == _config(frozen) and r["config_hash"] == pre["config_hash"]
    assert r["preregistration"]["ts"] == pre["ts"] and "deviation" not in r["preregistration"]
    out = r["results"]
    assert out["kind"] == "range_test"
    for k in ("rho_full", "rho_har", "rho_gain", "quintile_ratio", "n", "first_oos_month", "dropped_gvz_stale",
              "dropped_no_har", "dropped_no_full", "dropped_no_full_by_reason", "verdict", "feature_version"):
        assert k in out
    assert out["verdict"] == res.verdict == "continue" and out["n"] == res.n == frozen[1]
    assert out["first_oos_month"] == frozen[0]


def test_the_trial_is_linked_to_its_queued_preregistration_and_charged_once(planted, tmp_path, queued_prereg,
                                                                              frozen):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    pre = queued_prereg(reg, agent_id=h16.AGENT_ID, family=h16.FAMILY, config=_config(frozen),
                        feature_version=h16.h16_feature_version(), rationale="H-16", reading_rule=h16.READING_RULE)
    row, _ = _run(reg, planted, reserved=1)
    assert row["preregistration"]["ts"] == pre["ts"] and reg.n_trials == 1


def test_the_run_is_refused_without_an_unused_preregistration(planted, tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(ValueError, match="no unused pre-registration"):
        _run(reg, planted)
    assert reg.path.read_text() == ""


def test_a_second_run_of_the_same_configuration_is_refused_even_with_a_fresh_preregistration(planted, tmp_path,
                                                                                              frozen):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    _prereg(reg, _config(frozen))
    _run(reg, planted)
    _prereg(reg, _config(frozen))
    with pytest.raises(ValueError, match="already run"):
        _run(reg, planted)
    assert len(_trials(reg)) == 1


def test_the_run_is_refused_before_reading_when_the_feature_version_differs_from_the_preregistration(
        planted, tmp_path, frozen, monkeypatch):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    _prereg(reg, _config(frozen), fv="f-0000000000")
    read = []
    monkeypatch.setattr(h16, "evaluation_inputs", lambda *a, **k: read.append(1))
    with pytest.raises(ValueError, match="feature version"):
        _run(reg, planted)
    assert not read and _trials(reg) == []


def test_a_config_that_is_not_h16s_rule_is_refused(planted, tmp_path, frozen):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    _prereg(reg, {**_config(frozen), "full": ["x1_t"]})
    with pytest.raises(ValueError, match="not H-16's configuration"):
        _run(reg, planted)
    assert _trials(reg) == []


def test_the_month_is_read_from_the_preregistered_config_and_a_mismatch_writes_no_row(planted, tmp_path, frozen):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    _prereg(reg, _config(frozen, month="2010-01"))
    with pytest.raises(ValueError, match="frozen first out-of-sample month"):
        _run(reg, planted)
    assert _trials(reg) == []


def test_too_few_evaluation_days_against_the_frozen_count_is_refused_before_any_statistic(
        planted, tmp_path, frozen, monkeypatch):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    need = int(np.ceil(frozen[1] / h16.MIN_EVAL_SHARE)) + 1           # n is just under 90% of this
    _prereg(reg, _config(frozen, expected=need))
    stats: list[int] = []
    real = h16.spearman

    def spy(a, b):
        stats.append(1)
        return real(a, b)
    monkeypatch.setattr(h16, "spearman", spy)
    with pytest.raises(ValueError, match="evaluation days"):
        _run(reg, planted)
    assert not stats and _trials(reg) == []
    monkeypatch.setattr(h16, "spearman", real)
    ok = int(np.floor(frozen[1] / h16.MIN_EVAL_SHARE))               # n is exactly at (or above) 90%
    assert h16.evaluate_h16(planted, _macro(), _calendar(), holdout_from=HOLDOUT, min_rows=MIN_ROWS, n_boot=0,
                            expected_eval_days=ok)[0].n == frozen[1]


def test_a_nan_statistic_is_refused_and_writes_no_row(planted, tmp_path, frozen, monkeypatch):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    _prereg(reg, _config(frozen))
    monkeypatch.setattr(h16, "spearman", lambda a, b: float("nan"))
    with pytest.raises(ValueError, match="NaN"):
        _run(reg, planted)
    assert _trials(reg) == []


def test_a_budget_refusal_writes_no_row(planted, tmp_path, frozen):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    _prereg(reg, _config(frozen))
    with pytest.raises(TrialBudgetExceeded):
        _run(reg, planted, cap=0)
    assert _trials(reg) == []


def test_days_without_a_full_forecast_are_counted_by_the_missing_input(planted):
    late = _calendar()
    late = late[pd.DatetimeIndex(late["event_utc"]) >= _utc("2019-01-01")]
    res, _ = h16.evaluate_h16(planted, _macro(), late, holdout_from=HOLDOUT, min_rows=MIN_ROWS, n_boot=0)
    why = res.dropped_no_full_by_reason
    assert res.dropped_no_full > 0 and why["tier1_count"] > 0
    assert set(why) == {*h16.EXTRA_REGRESSORS, "no_fit"}
    assert sum(why.values()) >= res.dropped_no_full


def test_h16_pins_what_it_reads_the_day_type_family_version():
    from goldbot.features.registry import feature_version
    assert not hasattr(h16, "PINNED_FEATURE_VERSION")
    assert h16.h16_feature_version() == feature_version(["day_type"])


# --------------------------------------------------------------------------- research_pass --h16
def _script():
    spec = importlib.util.spec_from_file_location("research_pass_h16", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**over) -> argparse.Namespace:
    base = dict(h16=True, discover=False, cpcv=None, from_year=2010, score_holdout=False, pooled="", day_type=False,
                positioning="", us_calendar_features=False, variants="[{}]", skip_screen=False)
    return argparse.Namespace(**{**base, **over})


@pytest.mark.parametrize("over, cal, macro, why", [
    ({"discover": True}, True, True, "--discover"),
    ({"cpcv": [3]}, True, True, "--cpcv"),
    ({}, False, True, "--us-calendar"),
    ({}, True, False, "--macro"),
    ({"from_year": 2011}, True, True, "--from-year"),
    ({"score_holdout": True}, True, True, "--score-holdout"),
    ({"pooled": "15m"}, True, True, "--pooled"),
])
def test_research_pass_h16_refuses_discover_cpcv_and_runs_without_calendar_or_macro(over, cal, macro, why):
    mod = _script()
    msg = mod.h16_refusal(_args(**over), _macro() if macro else None, _calendar() if cal else None)
    assert msg is not None and why in msg
    assert mod.h16_refusal(_args(), _macro(), _calendar()) is None
    no_gvz = _macro().assign(series="real_yield_10y")
    assert "GVZ" in (mod.h16_refusal(_args(), no_gvz, _calendar()) or "")


def test_research_pass_h16_runs_one_trial_on_the_settings_holdout_with_500_rows(monkeypatch, tmp_path):
    from goldbot.config import load_settings
    mod = _script()
    got: dict = {}

    def fake(reg, bars, macro, cal, **kw):
        got.update(kw, bars=bars)
        res, _ = h16.evaluate_h16(_bars(0.8), _macro(), _calendar(), holdout_from=HOLDOUT, min_rows=MIN_ROWS,
                                  n_boot=N_BOOT)
        return {"trial": 1, "status": "evaluated"}, res
    monkeypatch.setattr(mod.h16, "run_trial", fake)
    b1 = pd.DataFrame({"ts_utc": pd.date_range("2025-09-30 23:00", periods=120, freq="1min", tz="UTC")})
    for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"):
        b1[c] = 2000.0
    b1["visible_at"] = b1["ts_utc"] + pd.Timedelta(minutes=1)
    b1["tick_count"], b1["spread_mean"], b1["spread_max"] = 5, 0.2, 0.3
    args = _args(registry=str(tmp_path / "r.jsonl"), report=str(tmp_path / "h16.md"), rationale="E-4")
    settings = load_settings()
    assert mod._h16(args, b1, TrialRegistry(args.registry), settings, _macro(), _calendar(), {}, {}) == 0
    assert got["min_rows"] == dt.MIN_FIT_ROWS
    assert "feature_version" not in got and "frozen_first_oos_month" not in got     # both from the pre-registration
    assert got["holdout_from"] == settings.research.holdout_from
    assert pd.DatetimeIndex(got["bars"]["ts_utc"]).max() < _utc(str(settings.research.holdout_from))
    assert "CONTINUE" in Path(args.report).read_text()

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
def _run(reg: TrialRegistry, bars: pd.DataFrame, **kw):
    kw.setdefault("cap", 20)
    kw.setdefault("reserved", 0)
    return h16.run_trial(reg, bars, _macro(), _calendar(), holdout_from=HOLDOUT, retired=set(),
                         frozen_first_oos_month=kw.pop("frozen", None), rationale="E-4 synthetic",
                         feature_version=h16.PINNED_FEATURE_VERSION, min_rows=MIN_ROWS, n_boot=N_BOOT, **kw)


def test_the_run_writes_exactly_one_trial_row_with_the_reading_and_counts(planted, tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    row, res = _run(reg, planted)
    rows = [json.loads(x) for x in reg.path.read_text().splitlines() if x.strip()]
    assert len(rows) == 1 and is_trial(rows[0]) and reg.n_trials == 1
    r = rows[0]
    assert r["family"] == h16.FAMILY and r["status"] == "evaluated" and r["feature_version"] == "f-93fbe9386d"
    assert r["config"] == h16.h16_config(MIN_ROWS) and r["config_hash"] == row["config_hash"]
    out = r["results"]
    for k in ("rho_full", "rho_har", "rho_gain", "quintile_ratio", "n", "first_oos_month", "dropped_gvz_stale",
              "dropped_no_har", "dropped_no_full", "verdict", "feature_version"):
        assert k in out
    assert out["verdict"] == res.verdict == "continue" and out["n"] == res.n


def test_the_trial_is_linked_to_its_queued_preregistration_and_charged_once(planted, tmp_path, queued_prereg):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    pre = queued_prereg(reg, agent_id=h16.AGENT_ID, family=h16.FAMILY, config=h16.h16_config(MIN_ROWS),
                        feature_version=h16.PINNED_FEATURE_VERSION, rationale="H-16", reading_rule=h16.READING_RULE)
    row, _ = _run(reg, planted, reserved=1)
    assert row["preregistration"]["ts"] == pre["ts"] and "deviation" not in row["preregistration"]
    assert reg.n_trials == 1


def test_an_ad_hoc_preregistration_is_linked_when_no_queued_row_exists(planted, tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    pre = reg.preregister(agent_id=h16.AGENT_ID, family=h16.FAMILY, config=h16.h16_config(MIN_ROWS),
                          feature_version=h16.PINNED_FEATURE_VERSION, rationale="H-16", reading_rule=h16.READING_RULE)
    row, _ = _run(reg, planted)
    assert row["preregistration"]["ts"] == pre["ts"] and reg.n_trials == 1


def test_a_refusal_before_the_reading_writes_no_row(planted, tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(TrialBudgetExceeded):
        _run(reg, planted, cap=0)
    with pytest.raises(ValueError, match="frozen first out-of-sample month"):
        _run(reg, planted, frozen="2010-01")
    assert reg.path.read_text() == ""


@pytest.fixture
def restore_features():
    feats, signed = dict(feat_registry.FEATURES), dict(feat_registry.SIGNED)
    yield
    feat_registry.FEATURES.clear()
    feat_registry.FEATURES.update(feats)
    feat_registry.SIGNED.clear()
    feat_registry.SIGNED.update(signed)


def test_h16_runs_on_the_pinned_combined_feature_version(restore_features):
    assert h16.h16_feature_version() == h16.PINNED_FEATURE_VERSION == "f-93fbe9386d"


# --------------------------------------------------------------------------- research_pass --h16
def _script():
    spec = importlib.util.spec_from_file_location("research_pass_h16", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**over) -> argparse.Namespace:
    base = dict(h16=True, discover=False, cpcv=None, from_year=2010, score_holdout=False, pooled="", day_type=False,
                positioning="", us_calendar_features=False, variants="[{}]", skip_screen=False,
                first_oos_month="2012-04")
    return argparse.Namespace(**{**base, **over})


@pytest.mark.parametrize("over, cal, macro, why", [
    ({"discover": True}, True, True, "--discover"),
    ({"cpcv": [3]}, True, True, "--cpcv"),
    ({}, False, True, "--us-calendar"),
    ({}, True, False, "--macro"),
    ({"from_year": 2011}, True, True, "--from-year"),
    ({"score_holdout": True}, True, True, "--score-holdout"),
    ({"pooled": "15m"}, True, True, "--pooled"),
    ({"first_oos_month": None}, True, True, "--first-oos-month"),
])
def test_research_pass_h16_refuses_discover_cpcv_and_runs_without_calendar_or_macro(over, cal, macro, why):
    mod = _script()
    msg = mod.h16_refusal(_args(**over), _macro() if macro else None, _calendar() if cal else None)
    assert msg is not None and why in msg
    assert mod.h16_refusal(_args(), _macro(), _calendar()) is None
    no_gvz = _macro().assign(series="real_yield_10y")
    assert "GVZ" in (mod.h16_refusal(_args(), no_gvz, _calendar()) or "")


def test_research_pass_h16_runs_one_trial_on_the_settings_holdout_with_500_rows(monkeypatch, tmp_path, restore_features):
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
    assert got["min_rows"] == dt.MIN_FIT_ROWS and got["frozen_first_oos_month"] == "2012-04"
    assert got["holdout_from"] == settings.research.holdout_from
    assert got["feature_version"] == "f-93fbe9386d"
    assert pd.DatetimeIndex(got["bars"]["ts_utc"]).max() < _utc(str(settings.research.holdout_from))
    assert "CONTINUE" in Path(args.report).read_text()

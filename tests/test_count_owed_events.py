"""scripts/count_owed_events.py: the pre-registrations' owed event counts, counted on synthetic bars WITHOUT any outcome.
Guards: the script imports and calls no label, return or statistics function (AST), every count's events before an
instant are unchanged when every price after it is perturbed (nothing after the decision bar is read), H-16's count
is the evaluation set `h16.evaluate_h16` uses, and nothing at or after the holdout reaches a count."""
from __future__ import annotations

import ast
import importlib.util
import urllib.error
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data import release
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.macro import fred_frame
from goldbot.data.resample import BAR_COLUMNS, resample_bars
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.research import h16

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "count_owed_events.py"
START, END = "2023-01-02", "2023-09-02"
HOLDOUT = "2023-08-15"
CUT = pd.Timestamp("2023-07-10", tz="UTC")       # a Monday 00:00 UTC: prices after it are perturbed
MIN_ROWS = 30                                     # the synthetic history is short; the CLI uses day_type's 500
EXTRA = 0.37


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("count_owed_events", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


co = _load()


def _bars_1m(start: str = START, end: str = END, seed: int = 3) -> pd.DataFrame:
    """1m bars on the session calendar's open minutes: a random walk with a session-dependent volatility."""
    ts = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    ts = ts[DEFAULT_SESSIONS.is_open(ts)]
    rng = np.random.default_rng(seed)
    vol = np.where((ts.hour >= 7) & (ts.hour < 17), 0.00045, 0.00015)
    mid = 1900 * np.exp(np.cumsum(vol * rng.standard_normal(len(ts))))
    op = np.concatenate(([1900.0], mid[:-1]))
    wig = np.abs(vol * rng.standard_normal(len(ts)))
    hi, lo = np.maximum(op, mid) * (1 + wig), np.minimum(op, mid) * (1 - wig)
    sp = np.where((ts.hour >= 7) & (ts.hour < 17), 0.2, 0.5)
    b = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for side, sign in (("bid", -1), ("ask", 1)):
        for k, v in (("open", op), ("high", hi), ("low", lo), ("close", mid)):
            b[f"{side}_{k}"] = v + sign * sp / 2
    b["tick_count"], b["spread_mean"], b["spread_max"] = 10, sp, sp
    return b[BAR_COLUMNS]


def _perturbed(b1: pd.DataFrame, at: pd.Timestamp, seed: int = 99) -> pd.DataFrame:
    """Every price of every 1m bar at or after `at` replaced by another random path (timestamps unchanged)."""
    out = b1.copy()
    late = np.asarray(utc_index(out["ts_utc"]) >= at)
    rng = np.random.default_rng(seed)
    shock = np.exp(np.cumsum(0.002 * rng.standard_normal(int(late.sum())))) * 1.07
    for c in [c for c in BAR_COLUMNS if c.startswith(("bid_", "ask_"))]:
        out.loc[late, c] = out.loc[late, c].to_numpy() * shock * (1 + 0.001 * rng.random(int(late.sum())))
    for c in ("spread_mean", "spread_max"):
        out.loc[late, c] = out.loc[late, c].to_numpy() * 3.0
    return out


def _calendar() -> pd.DataFrame:
    """Tier-1 releases: NFP first Fridays and CPI mid-month at 08:30 New York, FOMC statements 14:00 New York, plus a
    second same-day event (PCE at 10:00) and a cancelled one; each stamped a week ahead."""
    rows: list[dict[str, Any]] = []

    def add(kind: str, local: str, status: str = "scheduled", stamp_days: float = 7) -> None:
        ev = pd.Timestamp(local, tz="America/New_York").tz_convert("UTC")
        rows.append({"event_id": f"{kind}:{local[:10]}", "event_type": kind, "tier": 1, "event_utc": ev,
                     "status": "scheduled", "available_utc": ev - pd.Timedelta(days=stamp_days), "source": "fred"})
        if status != "scheduled":
            rows.append({**rows[-1], "status": status, "available_utc": ev - pd.Timedelta(days=1)})

    for month in range(1, 9):
        fri = pd.date_range(f"2023-{month:02d}-01", periods=7, freq="D")
        add("nfp", f"{fri[fri.dayofweek == 4][0]:%Y-%m-%d} 08:30")
        add("cpi", f"2023-{month:02d}-14 08:30" if month != 5 else "2023-05-10 08:30")
    for d in ("2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26"):
        add("fomc_statement", f"{d} 14:00")
    add("pce", "2023-03-14 10:00")                                # same day as CPI: skipped (later event)
    add("pce", "2023-04-28 08:30", status="cancelled")            # cancelled a day before: never an event
    cal = pd.DataFrame(rows)
    cal["ts_utc"] = cal["available_utc"]
    return cal


def _macro() -> pd.DataFrame:
    vd = pd.bdate_range("2022-12-01", END)
    vd = vd[(vd < pd.Timestamp("2023-05-01")) | (vd > pd.Timestamp("2023-05-19"))]    # a gap: stale GVZ days
    vals = 18 + np.random.default_rng(5).standard_normal(len(vd))
    return fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": vals}), pd.Timestamp("2024-01-01"))


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return h16.cut_before_holdout(_bars_1m(), HOLDOUT)


def _before(events: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    if events.empty:
        return events
    return events[utc_index(events["decision_utc"]) < at].reset_index(drop=True)


# --------------------------------------------------------------------------- guard: no outcome functions
FORBIDDEN_MODULES = ("goldbot.labels", "goldbot.research.pipeline", "goldbot.research.metrics",
                     "goldbot.research.screen", "goldbot.research.walkforward", "goldbot.research.gates",
                     "goldbot.research.model", "goldbot.research.discovery", "goldbot.research.cpcv",
                     "goldbot.research.registry", "goldbot.engine")
FORBIDDEN_NAMES = {"triple_barrier", "one_at_a_time", "expectancy", "rule_only", "rule_only_split", "prepare",
                   "evaluate", "evaluate_h16", "run_trial", "spearman", "quintile_means", "read_rule",
                   "block_bootstrap_gain", "label_spec", "exit_spec", "exit_policy", "screen", "summarize",
                   "excursion", "TrialRegistry"}
FORBIDDEN_COLUMNS = {"ret", "pnl", "t_exit", "exit", "target_hit", "r_multiple", "net_ret", "gross_ret"}


def test_the_script_imports_and_calls_no_label_return_or_statistics_function():
    tree = ast.parse(SCRIPT.read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            assert not {a.name for a in node.names} & FORBIDDEN_NAMES, node.module
    bad = [m for m in imported if m.startswith(FORBIDDEN_MODULES)]
    assert not bad, f"count_owed_events imports outcome code: {bad}"
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not used & FORBIDDEN_NAMES, f"count_owed_events references {sorted(used & FORBIDDEN_NAMES)}"
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not strings & FORBIDDEN_COLUMNS, f"count_owed_events reads outcome columns {sorted(strings & FORBIDDEN_COLUMNS)}"


def test_h16_count_receives_timestamps_only():
    """count_h16 never sees a real price: its bar input is a DatetimeIndex, and its prices are the placebo path."""
    import inspect
    params = list(inspect.signature(co.count_h16).parameters)
    assert params[0] == "ts15"
    ts = pd.date_range("2023-01-02", periods=8, freq="15min", tz="UTC")
    p1, p2 = co.placebo_bars(ts), co.placebo_bars(ts)
    pd.testing.assert_frame_equal(p1, p2)                         # seeded: the same placebo every run
    assert (p1["high"] >= p1[["open", "close"]].max(axis=1)).all() and (p1["low"] > 0).all()


# --------------------------------------------------------------------------- guard: nothing after the decision bar
@pytest.mark.parametrize("name", ["H-04", "H-17", "H-18", "H-14", "H-01"])
def test_events_before_an_instant_do_not_depend_on_any_price_after_it(b1: pd.DataFrame, name: str):
    cal = co.cut_inputs(b1, None, _calendar(), HOLDOUT)[2]

    def events(bars: pd.DataFrame) -> pd.DataFrame:
        if name == "H-04":
            return co.count_h04(bars)[1]
        if name == "H-01":
            return co.count_h01(bars)[1]
        if name == "H-17":
            return co.count_h17(bars, cal, "2023-04", min_rows=MIN_ROWS)[1]
        if name == "H-18":
            return co.count_h18(bars, cal)[1]
        return co.count_h14(bars, cal, EXTRA)[1]

    base, moved = events(b1), events(_perturbed(b1, CUT))
    if name != "H-01":                      # 120 daily closes need more history than the fixture has
        assert len(_before(base, CUT)) > 0, f"{name}: no events before the cut; the test would prove nothing"
    pd.testing.assert_frame_equal(_before(base, CUT), _before(moved, CUT))


def test_h16_count_is_the_evaluation_set_evaluate_h16_uses(b1: pd.DataFrame):
    _, macro, cal = co.cut_inputs(b1, _macro(), _calendar(), HOLDOUT)
    ts15 = utc_index(resample_bars(b1, "15m")["ts_utc"])
    out, ev = co.count_h16(ts15, macro, cal, min_rows=MIN_ROWS)
    res, _ = h16.evaluate_h16(co.placebo_bars(ts15), macro, cal, holdout_from=HOLDOUT, min_rows=MIN_ROWS,
                              feature_version="test", n_boot=0)
    assert out["status"] == "counted"
    assert out["first_oos_month"] == res.first_oos_month
    assert out["expected_eval_days"] == res.n == len(ev)
    assert (out["dropped_gvz_stale"], out["dropped_no_har"], out["dropped_no_full"]) == (
        res.dropped_gvz_stale, res.dropped_no_har, res.dropped_no_full)
    assert out["dropped_gvz_stale"] > 0                           # the planted GVZ gap is counted, not silently kept
    # the frozen month passes evaluate_h16's own check, and the count clears its 90% floor
    h16.evaluate_h16(co.placebo_bars(ts15), macro, cal, holdout_from=HOLDOUT, min_rows=MIN_ROWS,
                     frozen_first_oos_month=out["first_oos_month"], expected_eval_days=out["expected_eval_days"],
                     feature_version="test", n_boot=0)


def test_h16_count_depends_on_data_gaps_not_prices(b1: pd.DataFrame):
    _, macro, cal = co.cut_inputs(b1, _macro(), _calendar(), HOLDOUT)
    ts15 = utc_index(resample_bars(b1, "15m")["ts_utc"])
    full = co.count_h16(ts15, macro, cal, min_rows=MIN_ROWS)[0]
    gap = (ts15 >= pd.Timestamp("2023-07-11 07:00", tz="UTC")) & (ts15 < pd.Timestamp("2023-07-11 21:00", tz="UTC"))
    holed = co.count_h16(ts15[~gap], macro, cal, min_rows=MIN_ROWS)[0]
    assert holed["expected_eval_days"] < full["expected_eval_days"]      # a gap day (stub) leaves the set


def test_h16_without_macro_still_freezes_the_month_and_says_what_is_missing(b1: pd.DataFrame):
    ts15 = utc_index(resample_bars(b1, "15m")["ts_utc"])
    out, _ = co.count_h16(ts15, None, None, min_rows=MIN_ROWS)
    assert out["status"] == "partly counted" and "GVZ" in out["reason"] and out["first_oos_month"]


# --------------------------------------------------------------------------- the individual counts
def test_h04_sample_rule_drops_an_entry_whose_window_has_a_gap(b1: pd.DataFrame):
    base, _ = co.count_h04(b1)
    ts = utc_index(b1["ts_utc"])
    hole = (ts >= pd.Timestamp("2023-03-08 03:00", tz="UTC")) & (ts < pd.Timestamp("2023-03-08 04:00", tz="UTC"))
    holed, _ = co.count_h04(b1[~hole].reset_index(drop=True))
    assert base["dropped_by_7h_sample_rule"] == 0
    assert holed["dropped_by_7h_sample_rule"] == 1 and holed["dropped_dates"] == ["2023-03-08"]
    assert holed["entries"] == base["entries"] - 1
    assert base["entries"] == base["candidates"] - base["crossing_the_holdout"]
    assert base["crossing_the_holdout"] == 1                     # 2023-08-15 00:00: its window runs into the holdout
    assert base["entries"] <= base["calendar_upper_bound"]
    assert base["calendar_upper_bound"] == (base["no_decision_bar"] + base["atr_warmup_or_unusable"]
                                            + base["candidates"])
    assert "asia_drift-g0-98dc13a518" in base["source"]          # the pre-registered agent id


def test_h18_counts_the_first_event_of_a_day_with_its_entry_bar_and_the_move_filter(b1: pd.DataFrame):
    cal = co.cut_inputs(b1, None, _calendar(), HOLDOUT)[2]
    out, ev = co.count_h18(b1, cal)
    assert out["skipped_later_same_day"] == 2     # PCE after CPI on 2023-03-14, FOMC after CPI on 2023-06-14
    assert "pce" not in {t for t, n in out["by_type_with_entry_bar"].items() if n}   # the cancelled PCE never counts
    assert 0 < out["pass_move_filter"] <= out["with_entry_bar"] <= out["calendar_events"]
    assert len(ev) == out["pass_move_filter"]
    # entry decision = release + 30 min, on the 15m grid
    known = co.h18_events(cal)
    assert set(utc_index(ev["decision_utc"]) - pd.Timedelta(minutes=30)) <= set(utc_index(known["event_utc"]))


def test_blackout_is_point_in_time_and_ignores_cancelled_events():
    ev = pd.Timestamp("2023-03-14 12:30", tz="UTC")
    cal = pd.DataFrame([
        {"event_id": "a", "event_type": "cpi", "tier": 1, "event_utc": ev, "status": "scheduled",
         "available_utc": ev - pd.Timedelta(days=7)},
        {"event_id": "b", "event_type": "nfp", "tier": 1, "event_utc": ev + pd.Timedelta(hours=3),
         "status": "scheduled", "available_utc": ev - pd.Timedelta(days=7)},
        {"event_id": "b", "event_type": "nfp", "tier": 1, "event_utc": ev + pd.Timedelta(hours=3),
         "status": "cancelled", "available_utc": ev},
        {"event_id": "c", "event_type": "cpi", "tier": 1, "event_utc": ev + pd.Timedelta(hours=6),
         "status": "scheduled", "available_utc": ev + pd.Timedelta(hours=6, minutes=10)},   # stamped late
        {"event_id": "d", "event_type": "ppi", "tier": 2, "event_utc": ev + pd.Timedelta(hours=8),
         "status": "scheduled", "available_utc": ev - pd.Timedelta(days=7)},                  # tier 2: no blackout
    ])
    t = pd.date_range(ev - pd.Timedelta(hours=1), ev + pd.Timedelta(hours=9), freq="5min")
    lo, hi = co.blackout_intervals(cal)
    hit = pd.Series(co.in_intervals(epoch_ns(t), lo, hi), index=t)
    assert hit[ev - pd.Timedelta(minutes=15)] and hit[ev + pd.Timedelta(minutes=25)]
    assert not hit[ev - pd.Timedelta(minutes=20)] and not hit[ev + pd.Timedelta(minutes=30)]   # [-15, +30)
    assert not hit[ev + pd.Timedelta(hours=3)]                                                # cancelled before
    assert not hit[ev + pd.Timedelta(hours=6, minutes=5)] and hit[ev + pd.Timedelta(hours=6, minutes=10)]
    assert not hit[ev + pd.Timedelta(hours=8)]


def test_h14_cost_gate_and_blackout_are_applied(b1: pd.DataFrame):
    cal = co.cut_inputs(b1, None, _calendar(), HOLDOUT)[2]
    cheap, _ = co.count_h14(b1, cal, 0.0)
    dear, _ = co.count_h14(b1, cal, 2.0)
    assert cheap["dropped_blackout"] > 0
    assert dear["candidate_bars"] < cheap["candidate_bars"] and dear["labelled_rows"] == 2 * dear["candidate_bars"]
    assert cheap["window_bars"] == (cheap["dropped_blackout"] + cheap["dropped_atr_unknown"]
                                    + cheap["dropped_cost_gate"] + cheap["candidate_bars"])


def test_h17_high_and_low_halves_are_disjoint_days_from_the_first_oos_month(b1: pd.DataFrame):
    cal = co.cut_inputs(b1, None, _calendar(), HOLDOUT)[2]
    out, ev = co.count_h17(b1, cal, "2023-06", min_rows=MIN_ROWS)   # the first fit with 30 rows is at May's end
    assert out["high_events"] > 0 and out["low_events"] > 0 and out["trial_events"] == out["high_events"]
    days = utc_index(ev["decision_utc"]).tz_convert("Europe/London").normalize()
    assert not days.duplicated().any()                           # at most one trade a day, and a day is high or low
    assert days.min() >= pd.Timestamp("2023-06-01", tz="Europe/London")
    later, _ = co.count_h17(b1, cal, "2023-07", min_rows=MIN_ROWS)
    assert later["high_events"] < out["high_events"]


# --------------------------------------------------------------------------- the whole run
def test_nothing_at_or_after_the_holdout_reaches_any_count():
    raw = _bars_1m()
    a = co.run(raw, _macro(), _calendar(), holdout_from=HOLDOUT, extra_cost_usd=EXTRA, min_rows=MIN_ROWS)
    b = co.run(_perturbed(raw, pd.Timestamp(HOLDOUT, tz="UTC")), _macro(), _calendar(), holdout_from=HOLDOUT,
               extra_cost_usd=EXTRA, min_rows=MIN_ROWS)
    assert a["counts"] == b["counts"]
    assert pd.Timestamp(a["meta"]["bars_to"], tz="UTC") < pd.Timestamp(HOLDOUT, tz="UTC")
    text = co.markdown(a)
    assert "| Q1 H-04 | asia_drift entries" in text and "expected_eval_days" in text
    assert "| H-04 entries |" in text


def test_missing_calendar_is_reported_not_counted():
    out = co.run(_bars_1m(end="2023-04-01"), None, None, holdout_from=HOLDOUT, extra_cost_usd=EXTRA,
                 min_rows=MIN_ROWS)
    for h in ("H-17", "H-18", "H-14"):
        assert out["counts"][h]["status"] == "not counted" and "calendar" in out["counts"][h]["reason"]
    assert "not counted" not in out["counts"]["H-04"]["status"]
    co.markdown(out)                                             # renders without the missing counts


# --------------------------------------------------------------------------- release helpers
def test_download_release_returns_nothing_when_the_release_is_missing(monkeypatch: pytest.MonkeyPatch,
                                                                      tmp_path: Path):
    def missing(*_a: Any, **_k: Any) -> list[dict]:
        raise urllib.error.HTTPError("u", 404, "Not Found", Message(), None)
    monkeypatch.setattr(release, "list_assets", missing)
    assert release.download_release("macro-v1", tmp_path) == []


def test_read_bar_files_drops_error_flagged_bars_and_refuses_an_empty_folder(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        release.read_bar_files(tmp_path, 2010, 2025)
    b = _bars_1m(end="2023-01-04")
    b["dq_flag"] = ""
    b.loc[5, "dq_flag"] = "error:spike"
    b.to_parquet(tmp_path / "xauusd_1m_dukascopy_2023.parquet")
    out = release.read_bar_files(tmp_path, 2010, 2025)
    assert len(out) == len(b) - 1 and list(out.columns) == BAR_COLUMNS

"""Cross-feed checks (D12 reconciliation, D20 spike confirmation, D23/F10 survival) on synthetic paired feeds."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data import crossfeed as cf
from goldbot.data.quality import check_bars, confirm_spikes, spike_matched
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.ops import health
from goldbot.ops.accounts import Account
from goldbot.ops.health import AccountRef, HealthContext
from goldbot.ops.jobs import JobContext, feed_reconcile
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

NOW = pd.Timestamp("2026-10-07 23:20", tz="UTC")       # Wednesday, after the daily break
ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)


def day_ticks(seed: int = 5) -> pd.DataFrame:
    t = synthetic_ticks("2026-10-06 23:00", "2026-10-07 23:20", ticks_per_minute=4, seed=seed)
    # one tick per millisecond: the store does not keep the arrival order of ticks sharing a stamp
    return t[["ts_utc", "bid", "ask"]].drop_duplicates("ts_utc", keep="last").reset_index(drop=True)


def as_broker_m1(bars: pd.DataFrame, point: float = 0.01) -> pd.DataFrame:
    """What the terminal's copy_rates returns for these bars: bid OHLC, tick volume, spread in points."""
    return pd.DataFrame({"ts_utc": bars["ts_utc"], "open": bars["bid_open"], "high": bars["bid_high"],
                         "low": bars["bid_low"], "close": bars["bid_close"], "tick_count": bars["tick_count"],
                         "spread": (bars["spread_mean"] / point).round()})


class FakeBroker:
    def __init__(self, m1: pd.DataFrame, point: float = 0.01) -> None:
        self.m1, self.point = m1, point
        self.calls: list[tuple[str, str, int]] = []

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        self.calls.append((symbol, tf, n))
        return self.m1.tail(n).reset_index(drop=True)

    def symbol_info(self, symbol: str) -> Any:
        return type("S", (), {"point": self.point})()



# ------------------------------------------------------------------------------------------- D12 reconcile
def test_agreeing_feeds_reconcile_with_no_divergence():
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    n = cf.reconcile(eb, bb, start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["broker_minutes"] > 1000 and n["matched"] == n["broker_minutes"] == n["engine_minutes"]
    assert (n["close_mismatch"], n["missing_engine"], n["missing_broker"]) == (0, 0, 0)
    assert n["severity"] == "ok" and n["divergence"] == 0.0


def test_diverging_closes_beyond_two_points_are_counted_and_graded():
    eb = ticks_to_1m(day_ticks())
    m1 = as_broker_m1(eb)
    m1.loc[m1.index[100:130], "close"] += 0.03                # 3 points away on 30 minutes
    m1.loc[m1.index[130:140], "close"] += 0.02              # exactly 2 points: within tolerance
    n = cf.reconcile(eb, cf.broker_m1_bars(m1), start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["close_mismatch"] == 30 and n["max_close_diff"] == pytest.approx(0.03)
    assert n["severity"] == "warning"                     # 30 / ~1,300 minutes: between 0.5% and 5%
    m1.loc[m1.index[100:300], "close"] += 1.0
    n = cf.reconcile(eb, cf.broker_m1_bars(m1), start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["severity"] == "error"


def test_missing_minutes_on_either_side_are_counted():
    eb = ticks_to_1m(day_ticks())
    m1 = as_broker_m1(eb)
    n = cf.reconcile(eb.drop(index=range(100, 105)), cf.broker_m1_bars(m1.drop(index=range(500, 503))),
                     start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["missing_engine"] == 5 and n["missing_broker"] == 3 and n["close_mismatch"] == 0
    assert n["first"]["missing_engine"] == eb["ts_utc"].iloc[100]


def test_reconcile_account_writes_events_broker_history_and_report(tmp_path):
    store = Store(tmp_path / "data")
    ticks = day_ticks()
    store.append("ticks", ticks, source="icm-demo", dedupe=False)
    m1 = as_broker_m1(ticks_to_1m(ticks))
    m1.loc[m1.index[100:140], "close"] -= 0.5
    rep = cf.reconcile_account(store, FakeBroker(m1), "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert rep.close_mismatch == 40 and rep.severity == "warning" and rep.tolerance == pytest.approx(0.02)
    ev = store.read("dq_events", source="icm-demo")
    assert list(ev["check"]) == ["reconcile_close_mismatch"] and set(ev["severity"]) == {"warning"}
    hist = store.read(cf.BROKER_TABLE, source="icm-demo")
    assert len(hist) == rep.broker_minutes and hist["ask_close"].ge(hist["bid_close"]).all()
    saved = json.loads(cf.reconcile_file(tmp_path, "icm-demo").read_text())
    assert saved["close_mismatch"] == 40 and saved["severity"] == "warning"


def test_engine_with_no_ticks_is_an_error_with_a_note(tmp_path):
    store = Store(tmp_path / "data")
    m1 = as_broker_m1(ticks_to_1m(day_ticks()))
    rep = cf.reconcile_account(store, FakeBroker(m1), "icm-demo", "XAUUSD", NOW)
    assert rep.severity == "error" and rep.missing_engine == rep.broker_minutes and "no ticks" in rep.note


def _health_ctx(tmp_path: Path, now: pd.Timestamp = NOW) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=now, settings=load_settings(), accounts=[AccountRef(account_id="icm-demo")],
                         get_secret=lambda k: None, disk_usage=lambda p: (100e9, 50e9, 50e9))


_CLOSES = {"ok": 0, "warning": 30, "error": 200}      # close mismatches of 1,300 minutes for each grade


def _report(severity: str, ts: pd.Timestamp = NOW) -> cf.ReconcileReport:
    """A report whose counts grade to `severity` (health grades the counts, not the stored word)."""
    return cf.ReconcileReport(account_id="icm-demo", ts_utc=ts, window_from=ts - pd.Timedelta(days=1), window_to=ts,
                              tolerance=0.02, broker_minutes=1300, engine_minutes=1300, matched=1300,
                              close_mismatch=_CLOSES[severity], missing_engine=0, missing_broker=0, max_close_diff=0.0,
                              divergence=_CLOSES[severity] / 1300, severity=severity)


@pytest.mark.parametrize("severity,status", [("ok", "ok"), ("warning", "warn"), ("error", "fail")])
def test_reconcile_health_check_follows_the_report_severity(tmp_path, severity, status):
    _report(severity).save(tmp_path)
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.name == "reconcile:icm-demo" and c.status == status


def test_reconcile_health_check_without_a_report_and_when_stale(tmp_path):
    assert cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo").status == "ok"
    _report("ok", NOW - pd.Timedelta(days=5)).save(tmp_path)
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.status == "warn" and "ago" in c.reason


def test_run_checks_includes_the_reconcile_check(tmp_path):
    _report("error").save(tmp_path)
    checks = {c.name: c.status for c in health.run_checks(_health_ctx(tmp_path)).checks}
    assert checks["reconcile:icm-demo"] == "fail"


# ---- an engine outage is a coverage gap, not a price fault (docs/proposals/2026-10-feed-reconcile-outage-minutes.md)
def _outage(eb: pd.DataFrame, runs: list[range]) -> pd.DataFrame:
    """The engine's bars for the given row ranges only (it was stopped in between)."""
    return eb.iloc[[i for r in runs for i in r]].reset_index(drop=True)


def test_minutes_the_engine_did_not_store_are_a_coverage_gap_not_divergence():
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    n = cf.reconcile(_outage(eb, [range(400, 700)]), bb, start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["matched"] == 300 and n["missing_engine"] == n["broker_minutes"] - 300 > 1000
    assert n["close_mismatch"] == 0 and n["missing_broker"] == 0 and n["edge_excluded"] == 2   # restart + stop
    assert n["divergence"] == 0.0 and n["severity"] == "warning"     # a gap warns; it never reads as a price error


def test_one_clean_stop_skips_exactly_one_minute():
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    mine = _outage(eb, [range(0, 500)])                             # up from the window's start, stopped once
    mine.loc[mine.index[-1], "bid_close"] += 0.5                    # the engine stopped inside its last minute
    n = cf.reconcile(mine, bb, start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["close_mismatch"] == 0 and n["edge_excluded"] == 1


def test_the_first_partial_minute_after_a_restart_is_not_compared():
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    mine = _outage(eb, [range(0, 300), range(400, len(eb))])
    mine.loc[[300], "bid_close"] += 0.5                              # row 300 of mine is eb row 400: the restart
    n = cf.reconcile(mine, bb, start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert n["close_mismatch"] == 0 and n["edge_excluded"] == 2


def test_a_flapping_feed_still_fails_on_price():
    """A feed dropping every other minute leaves one-minute gaps: no minute is an edge, every close is compared."""
    eb = ticks_to_1m(day_ticks())
    m1 = as_broker_m1(eb)
    m1.loc[m1.index[300:700], "close"] += 1.0
    n = cf.reconcile(eb.iloc[::2].reset_index(drop=True), cf.broker_m1_bars(m1), start=NOW - pd.Timedelta(days=1),
                     end=NOW, tolerance=0.02)
    assert n["edge_excluded"] == 0 and n["close_mismatch"] >= 150 and n["severity"] == "error"


@pytest.mark.parametrize("edges,severity", [(2, "ok"), (15, "warning"), (50, "error")])
def test_many_skipped_edge_minutes_are_graded(edges, severity):
    """Edges over matched minutes: above 5% warns, above 20% fails (a crash-looping engine)."""
    _, g = cf.grade(broker_minutes=200, engine_minutes=200, matched=200, edge_excluded=edges, close_mismatch=0,
                    missing_engine=0, missing_broker=0)
    assert g == severity


def test_a_friday_outage_with_four_engine_minutes_warns_instead_of_failing():
    """2026-10-16: the engine ran for 4 of 1,297 broker minutes (deploy, MT5 login); 3 edge closes mismatched."""
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    mine = _outage(eb, [range(400, 402), range(800, 802)])
    mine.loc[[0, 1, 3], "bid_close"] += 0.5          # every minute is a restart or a stop edge
    n = cf.reconcile(mine, bb, start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02)
    assert (n["close_mismatch"], n["edge_excluded"]) == (0, 4)
    assert n["severity"] == "warning"                # too few compared minutes to call a price error


def test_a_real_price_fault_while_the_engine_ran_still_fails():
    eb = ticks_to_1m(day_ticks())
    m1 = as_broker_m1(eb)
    m1.loc[m1.index[400:500], "close"] += 1.0
    n = cf.reconcile(_outage(eb, [range(300, 700)]), cf.broker_m1_bars(m1), start=NOW - pd.Timedelta(days=1), end=NOW,
                     tolerance=0.02)
    assert n["close_mismatch"] == 100 and n["severity"] == "error"


def test_an_engine_that_is_up_with_a_frozen_feed_fails():
    """A2: the engine's current run began before the window; its bars stop after two hours."""
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    n = cf.reconcile(_outage(eb, [range(0, 120)]), bb, start=NOW - pd.Timedelta(days=1), end=NOW, tolerance=0.02,
                     engine_up_from=NOW - pd.Timedelta(days=2))
    assert n["missing_engine_up"] == n["missing_engine"] == n["up_minutes"] - n["matched"] > 1000
    assert n["severity"] == "error"


def test_reconcile_account_reads_the_engine_run_start_from_the_loop_heartbeat(tmp_path):
    from goldbot.ops.watchdog import loop_heartbeat_path
    store = Store(tmp_path / "data")
    ticks = day_ticks()
    store.append("ticks", ticks[ticks["ts_utc"] < pd.Timestamp("2026-10-07 02:00", tz="UTC")], source="icm-demo",
                 dedupe=False)
    loop_heartbeat_path(tmp_path, "icm-demo").write_text(json.dumps(
        {"phase": "loop", "ts": NOW.timestamp() - 5, "started": (NOW - pd.Timedelta(days=3)).timestamp()}))
    rep = cf.reconcile_account(store, FakeBroker(as_broker_m1(ticks_to_1m(ticks))), "icm-demo", "XAUUSD", NOW,
                               state_dir=tmp_path)
    assert rep.severity == "error" and rep.missing_engine_up > 0
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "while the engine was up" in c.reason


def test_an_engine_started_after_the_outage_keeps_the_gap_a_warning():
    eb = ticks_to_1m(day_ticks())
    bb = cf.broker_m1_bars(as_broker_m1(eb))
    up = pd.Timestamp(eb["ts_utc"].iloc[1200])
    n = cf.reconcile(_outage(eb, [range(1200, len(eb))]), bb, start=NOW - pd.Timedelta(days=1), end=NOW,
                     tolerance=0.02, engine_up_from=up)
    assert n["missing_engine_up"] == 0 and n["severity"] == "warning"


def test_the_health_reason_separates_engine_coverage_from_divergence(tmp_path):
    rep = _report("warning").model_copy(update={"broker_minutes": 1297, "engine_minutes": 4, "matched": 4,
                                                "missing_engine": 1293, "edge_excluded": 2, "close_mismatch": 0})
    rep.save(tmp_path)
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.status == "warn"
    assert "engine bars missing for 1293 of 1297 broker minutes" in c.reason and "not a price fault" in c.reason
    assert "2 stop/restart-edge minutes not compared" in c.reason


def test_health_regrades_a_report_written_before_the_rule_change(tmp_path):
    """The 2026-10-16 report (old rule: severity error over broker minutes) reads as a WARN after the deploy, so a
    stale FAIL does not hold the outside dead man's switch on /fail until the next weekday run."""
    rep = _report("ok").model_copy(update={"broker_minutes": 1297, "engine_minutes": 4, "matched": 4, "close_mismatch": 3,
                                           "missing_engine": 1293, "divergence": 0.9992, "severity": "error"})
    rep.save(tmp_path)
    assert cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo").status == "warn"


def test_a_coverage_gap_writes_a_warning_event_and_a_price_fault_an_error(tmp_path):
    store = Store(tmp_path / "data")
    ticks = day_ticks()
    keep = ticks[(ticks["ts_utc"] >= pd.Timestamp("2026-10-07 10:00", tz="UTC"))
                 & (ticks["ts_utc"] < pd.Timestamp("2026-10-07 18:00", tz="UTC"))]
    store.append("ticks", keep, source="icm-demo", dedupe=False)
    m1 = as_broker_m1(ticks_to_1m(ticks))
    rep = cf.reconcile_account(store, FakeBroker(m1), "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert rep.severity == "warning" and rep.missing_engine > 0 and rep.close_mismatch == 0
    ev = store.read("dq_events", source="icm-demo")
    assert list(ev["check"]) == ["reconcile_missing_engine"] and set(ev["severity"]) == {"warning"}


def _job_ctx(tmp_path: Path, broker_for: Any) -> JobContext:
    return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                      accounts=[ACC], population=Population(tmp_path / "population.json"), broker_for=broker_for)


def test_feed_reconcile_job_runs_per_account_and_skips_without_a_broker(tmp_path):
    ticks = day_ticks()
    ctx = _job_ctx(tmp_path, lambda acc: FakeBroker(as_broker_m1(ticks_to_1m(ticks))))
    ctx.store.append("ticks", ticks, source="icm-demo", dedupe=False)
    out = feed_reconcile(ctx, NOW)
    assert out["icm-demo"]["severity"] == "ok" and cf.reconcile_file(tmp_path, "icm-demo").exists()
    assert feed_reconcile(_job_ctx(tmp_path, lambda acc: None), NOW)["icm-demo"]["skipped"]
    assert "skipped" in feed_reconcile(_job_ctx(tmp_path, None), NOW)


def test_feed_reconcile_job_reports_an_unreachable_terminal(tmp_path):
    def boom(acc: Account) -> Any:
        raise RuntimeError("bridge down")
    with pytest.raises(RuntimeError, match="icm-demo: RuntimeError: bridge down"):
        feed_reconcile(_job_ctx(tmp_path, boom), NOW)


# ------------------------------------------------------------------------------------------- D20 spikes
def _spiky(bars: pd.DataFrame, i: int, jump: float) -> pd.DataFrame:
    b = bars.copy()
    for c in ("bid_close", "ask_close"):
        b[c] = b[c] + np.where(b.index == i, jump, 0.0)
    return b


def test_one_sided_spike_stays_a_warning_and_a_matched_spike_is_dropped():
    eb = ticks_to_1m(day_ticks())
    i = 400
    spiked = _spiky(eb, i, 300.0)
    checked, ev = check_bars(spiked)
    assert any(e.check == "spike" and e.ts_utc == eb["ts_utc"].iloc[i] for e in ev)
    # the other feed did not move: the spike remains a warning, flags untouched
    out, kept = confirm_spikes(checked, ev, eb)
    assert [e for e in kept if e.check == "spike"] == [e for e in ev if e.check == "spike"]
    assert "warning:spike" in str(out.at[i, "dq_flag"])
    # the other feed shows the same move one minute later: a real market move, dropped from the warnings
    other = _spiky(eb, i + 1, 300.0)
    out, kept = confirm_spikes(checked, ev, other)
    assert not any(e.check == "spike" and e.ts_utc == eb["ts_utc"].iloc[i] for e in kept)
    assert "warning:spike" not in str(out.at[i, "dq_flag"])
    assert [e for e in kept if e.check != "spike"] == [e for e in ev if e.check != "spike"]


def test_spike_match_needs_the_same_direction_and_half_the_size():
    ts = pd.date_range("2026-10-07 10:00", periods=10, freq="1min", tz="UTC")
    mid = pd.Series(2400.0, index=ts.as_unit("ns"))
    t = ts[5]
    up = mid.copy()
    up[t:] += 10.0
    assert spike_matched(np.log(2410 / 2400), up, t)
    assert not spike_matched(-np.log(2410 / 2400), up, t)                  # opposite direction
    assert not spike_matched(np.log(2430 / 2400), up, t)                   # under half the spike's size
    late = mid.copy()
    late[ts[8]:] += 10.0
    assert not spike_matched(np.log(2410 / 2400), late, t)                 # three minutes later: outside +-1
    assert not spike_matched(np.log(2410 / 2400), mid.drop(index=ts[4:7].as_unit("ns")), t)  # missing minutes


def test_single_feed_spike_check_is_unchanged_without_another_feed():
    eb = _spiky(ticks_to_1m(day_ticks()), 400, 300.0)
    checked, ev = check_bars(eb)
    out, kept = confirm_spikes(checked, ev, eb.iloc[0:0])
    assert kept == ev and out["dq_flag"].equals(checked["dq_flag"])


def test_reconcile_records_only_unconfirmed_spikes(tmp_path):
    store = Store(tmp_path / "data")
    ticks = day_ticks()
    eb = ticks_to_1m(ticks)
    m1 = as_broker_m1(eb)
    m1["close"] += np.where(m1.index == 400, 300.0, 0.0)                         # broker-only spike
    m1["close"] += np.where(m1.index >= 800, 300.0, 0.0)                        # a level shift on the broker...
    late = ticks["ts_utc"] >= eb["ts_utc"].iloc[800]
    ticks.loc[late, ["bid", "ask"]] += 300.0               # ...that the engine's ticks show too
    store.append("ticks", ticks, source="icm-demo", dedupe=False)
    rep = cf.reconcile_account(store, FakeBroker(m1), "icm-demo", "XAUUSD", NOW)
    assert rep.spikes_flagged >= 2 and rep.spikes_unconfirmed >= 1
    ev = store.read("dq_events", source="icm-demo")
    spikes = ev[ev["check"] == "spike"]
    assert eb["ts_utc"].iloc[400] in set(spikes["ts_utc"])
    assert eb["ts_utc"].iloc[800] not in set(spikes["ts_utc"])          # confirmed on both feeds: no warning


# ------------------------------------------------------------------------------------------- D23/F10 survival
@pytest.mark.parametrize("duka,broker,days,verdict", [
    ({"n": 500, "mean_r": 0.1, "t": 2.5}, {"n": 300, "mean_r": 0.05, "t": 1.2}, 120, "survives"),
    ({"n": 500, "mean_r": 0.1, "t": 2.5}, {"n": 300, "mean_r": 0.05, "t": 0.8}, 120, "fails"),
    ({"n": 500, "mean_r": 0.1, "t": 2.5}, {"n": 300, "mean_r": -0.2, "t": 3.0}, 120, "fails"),
    ({"n": 500, "mean_r": 0.1, "t": 1.5}, {"n": 300, "mean_r": 0.2, "t": 2.4}, 120, "broker_only"),
    ({"n": 500, "mean_r": 0.0, "t": 0.1}, {"n": 300, "mean_r": 0.0, "t": 0.2}, 120, "no_signal"),
    ({"n": 500, "mean_r": 0.1, "t": 2.5}, {"n": 300, "mean_r": 0.05, "t": 1.2}, 30, "insufficient_overlap"),
    ({"n": 500, "mean_r": 0.1, "t": 2.5}, {"n": 50, "mean_r": 0.05, "t": 1.2}, 120, "insufficient_overlap"),
])
def test_survival_verdict_bands(duka, broker, days, verdict):
    assert cf.survival_verdict(duka, broker, days)[0] == verdict


def test_overlap_cuts_out_the_holdout():
    d = pd.DataFrame({"ts_utc": pd.date_range("2025-06-01", "2026-12-01", freq="1D", tz="UTC")})
    b = pd.DataFrame({"ts_utc": pd.date_range("2025-08-01", "2026-11-01", freq="1D", tz="UTC")})
    ho = (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC"))
    segs = cf.overlap_segments(d, b, ho)
    assert segs == [(pd.Timestamp("2025-08-01", tz="UTC"), ho[0]),
                    (ho[1], pd.Timestamp("2026-11-01 00:01", tz="UTC"))]
    assert cf.overlap_segments(d, b.iloc[0:0], ho) == []


def test_survival_reports_insufficient_overlap_without_broker_history():
    from goldbot.specialists import SPECIALISTS
    spec = SPECIALISTS["session_open"]()
    duka = ticks_to_1m(day_ticks())
    r = cf.survival_check(spec, duka, duka.iloc[0:0], holdout=None)
    assert r.verdict == "insufficient_overlap" and not r.passed
    r = cf.survival_check(spec, duka, duka.tail(300), holdout=None)
    assert r.verdict == "insufficient_overlap" and not r.passed and r.overlap_days < 1


def test_crossfeed_script_exits_nonzero_on_insufficient_overlap(tmp_path, capsys, monkeypatch):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "crossfeed_check.py"
    spec = importlib.util.spec_from_file_location("crossfeed_check", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out_json = tmp_path / "r.json"
    rc = mod.main(["--specialist", "session_open", "--account", "icm-demo", "--store", str(tmp_path / "data"),
                   "--json", str(out_json)])
    assert rc == 1 and "INSUFFICIENT OVERLAP" in capsys.readouterr().out
    assert json.loads(out_json.read_text())["verdict"] == "insufficient_overlap"


@pytest.mark.integration
def test_identical_feeds_agree_over_a_long_overlap():
    """Paired synthetic feeds over five months: the same bars on both feeds give the same rule-only result, so the
    verdict is either survives or no_signal, never fails or broker_only."""
    from goldbot.specialists import SPECIALISTS
    b = ticks_to_1m(synthetic_ticks("2026-10-01", "2027-03-01", ticks_per_minute=1, seed=11))
    r = cf.survival_check(SPECIALISTS["session_open"](), b, b.copy(), holdout=None)
    assert r.overlap_days > cf.MIN_OVERLAP_DAYS
    assert r.dukascopy == r.broker
    assert r.verdict in {"survives", "no_signal", "insufficient_overlap"}
    if r.dukascopy["n"] >= cf.MIN_OVERLAP_EVENTS:
        assert r.verdict in {"survives", "no_signal"}

"""ADR 0058 phase 3: the confirm stage (design 3.3) and the holdout stage (design 3.5) of a discovered spec.

A survivor of the explore window advances to a confirm trial only by the 2026-10-30 rule (DSR >= 0.95 at the cumulative
K_search on 200+ events); it is pre-registered (one registry row, frozen reading rule, at most 8 a month), read ONCE on
2018-01 .. 2025-09 in its month's frozen batch (budget checked under the registry lock, Holm across the batch), every
read journalled (hash-chained) before it happens, and never read again. Only a confirm passer whose verdict row exists
is read on the holdout, once, through the registry's holdout guard. Reads here are synthetic StageRead values: no
market data."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.research.autodisc import confirm as C
from goldbot.research.autodisc.ledger import LedgerError, SearchLedger
from goldbot.research.autodisc.spec import ExitTemplate, Leaf, StrategySpec, spec_hash
from goldbot.research.autodisc.stages import StageJournal, StageRefused
from goldbot.research.director import verdict_of
from goldbot.research.registry import PREREGISTERED, TrialBudgetExceeded, TrialRegistry, is_trial, quarter_of

SURVIVOR = {"n_events": 420, "mean_r": 0.25, "t_stat": 10.0, "fitness": 2.4, "excess_r": 0.2, "stress_mean_r": 0.2,
            "floors": True, "generation": 1}
CAP = 50


def spec(i: int) -> StrategySpec:
    return StrategySpec(timeframe="1h", side_rule="long", entry=Leaf(kind="time", name="london", params={"i": i}),
                        exit=ExitTemplate(stop="atr_1.0", target="2R"))


def H(i: int) -> str:
    return spec_hash(spec(i))


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    led = SearchLedger(tmp_path / "discovery_real.db", mode="real")
    for i in range(12):
        led.log_spec(spec(i), "G2", "1")
        led.record_result(H(i), {**SURVIVOR, **({"t_stat": None} if i == 9 else {}),
                                 **({"n_events": 150} if i == 10 else {})})
    led.record_survivors("2026-11-10", [(H(i), 0.97) for i in range(10)] + [(H(10), 0.99), (H(11), 0.90)],
                         k_search=led.k_search())
    return {"ledger": led, "reg": TrialRegistry(tmp_path / "registry.jsonl"), "tmp": tmp_path}


def read(mean: float = 0.25, n: int = 600, stress: float = 0.1, sd: float = 1.0) -> C.StageRead:
    """A deterministic confirm-window read: n events on distinct days 2018-01 .. 2025-09, R = mean +- sd alternating."""
    days = pd.date_range("2018-01-02", "2025-09-29", freq="D", tz="UTC")
    ts = days[np.linspace(0, len(days) - 1, n).astype(int)] + pd.Timedelta(hours=9)
    r = mean + sd * np.tile([1.0, -1.0], n // 2)
    risk = np.full(n, 0.002)
    return C.StageRead.of(ret=r * risk, risk=risk, ts_utc=pd.DatetimeIndex(ts), stress_mean_r=stress,
                          input_digest="synthetic", cost_line="synthetic")


def prereg(env: dict[str, Any], i: int) -> dict[str, Any]:
    return C.preregister_confirm(env["reg"], env["ledger"], H(i))


def run(env: dict[str, Any], reads: dict[str, C.StageRead], batch: str | None = None, cap: int = CAP
        ) -> list[C.ConfirmVerdict]:
    b = batch or pd.Timestamp.now("UTC").strftime("%Y-%m")
    return C.run_confirm_batch(env["reg"], env["ledger"], b, lambda s: reads[spec_hash(s)], cap=cap, reserved=0)


# ------------------------------------------------------------------------------------------------ pre-registration
def test_a_survivor_meeting_the_confirm_dsr_rule_is_preregistered_as_a_promise_with_its_frozen_reading_rule(env):
    row = prereg(env, 0)
    reg = env["reg"]
    assert row["status"] == PREREGISTERED and not is_trial(row) and reg.budget_used(quarter_of()) == 0
    assert row["family"] == C.FAMILY and row["rule_ref"] == f"autodisc:{H(0)}" and row["queue"] is False
    plan = row["plan"]
    assert plan["spec_hash"] == H(0) and plan["explore_n"] == 420 and plan["explore_se"] == pytest.approx(0.025)
    assert plan["survivor_dsr"] == 0.97 and plan["batch"] == row["ts"][:7]
    for words in ("2018-01-01", "2025-09-30", "DSR >= 0.95", "200", "Holm", "1.5x", "90% prediction interval",
                  "2021", "holdout"):
        assert words in row["reading_rule"], words
    # F3 precondition: the registry's discovery K covers the ledger before any confirm trial is pre-registered
    assert reg.n_trials_discovery >= env["ledger"].k_search() == 12


@pytest.mark.parametrize("i,why", [(11, "DSR 0.900"), (10, "150 events"), (9, "t_stat")])
def test_a_survivor_below_the_confirm_rule_stays_a_recorded_fact(env, i, why):
    with pytest.raises(StageRefused, match=why):
        prereg(env, i)
    assert env["reg"]._rows() == []


def test_a_spec_that_is_not_a_recorded_explore_survivor_is_refused(env, tmp_path):
    other = spec(77)
    env["ledger"].log_spec(other, "G2", "1")
    env["ledger"].record_result(spec_hash(other), SURVIVOR)
    with pytest.raises(StageRefused, match="not a recorded explore survivor"):
        C.preregister_confirm(env["reg"], env["ledger"], spec_hash(other))
    syn = SearchLedger(tmp_path / "syn.db", mode="synthetic")
    syn.log_spec(spec(0), "G2", "1")
    with pytest.raises((StageRefused, LedgerError)):
        C.preregister_confirm(env["reg"], syn, H(0))


def test_at_most_eight_confirm_preregistrations_a_month_and_never_the_same_spec_twice(env):
    for i in range(8):
        prereg(env, i)
    with pytest.raises(StageRefused, match="8"):
        prereg(env, 8)
    with pytest.raises(StageRefused, match="pre-registered already"):
        prereg(env, 0)


# ------------------------------------------------------------------------------------------------ the confirm read
def test_a_confirm_run_without_its_preregistered_row_is_refused(env):
    with pytest.raises(StageRefused, match="no pre-registered"):
        run(env, {})
    assert StageJournal(env["ledger"].path).entries() == []


def test_the_budget_is_checked_under_the_registry_lock_and_a_refused_batch_opens_nothing(env, monkeypatch):
    prereg(env, 0)
    reg: TrialRegistry = env["reg"]
    lock = reg.path.with_suffix(reg.path.suffix + ".lock")
    seen = []
    real = TrialRegistry.check_budget_reserved

    def spy(self, *a, **k):
        seen.append(lock.exists())
        return real(self, *a, **k)
    monkeypatch.setattr(TrialRegistry, "check_budget_reserved", spy)
    with pytest.raises(TrialBudgetExceeded):
        run(env, {H(0): read()}, cap=0)
    assert seen == [True]
    assert StageJournal(env["ledger"].path).entries() == [] and reg.n_trials == 0


def test_a_passing_read_is_journalled_counted_and_read_by_its_verdict_before_any_follow_up(env):
    prereg(env, 0)
    k_before = env["reg"].n_trials_discovery
    [v] = run(env, {H(0): read()})
    assert v.passed, v.checks
    assert v.n_trials == k_before + 1                                     # DSR at the registry K + batches + this trial
    reg: TrialRegistry = env["reg"]
    [trial] = [r for r in reg._rows() if is_trial(r) and r.get("status") == C.STATUS_CONFIRM]
    assert trial["rule_ref"] == f"autodisc:{H(0)}" and trial["preregistration"]["config_hash"] == trial["config_hash"]
    assert trial["results"]["passed"] is True and trial["results"]["window"] == ["2018-01-01", "2025-09-30"]
    assert reg.budget_used(quarter_of()) == 1
    verdict = verdict_of(reg._rows(), trial)
    assert verdict == "continue"
    j = StageJournal(env["ledger"].path)
    assert [e.kind for e in j.entries()] == ["confirm_open", "confirm_result"] and j.verify() == 2
    assert j.state(H(0)).confirm == "passed"


def test_a_read_that_fails_any_part_of_the_rule_is_a_stop(env):
    prereg(env, 0)
    prereg(env, 1)
    prereg(env, 2)
    vs = {v.spec_hash: v for v in run(env, {H(0): read(stress=-0.01), H(1): read(mean=0.6),
                                            H(2): read(n=150)})}
    assert not vs[H(0)].passed and "stress" in " ".join(vs[H(0)].failed)
    assert not vs[H(1)].passed and "prediction interval" in " ".join(vs[H(1)].failed)
    assert not vs[H(2)].passed and "DSR" in " ".join(vs[H(2)].failed)
    reg = env["reg"]
    for t in (r for r in reg._rows() if r.get("status") == C.STATUS_CONFIRM):
        assert verdict_of(reg._rows(), t) == "stop"


def test_holm_is_computed_over_the_months_frozen_batch():
    assert C.holm([0.04, 0.2]) == [False, False]                           # 0.04 alone would pass at 5%
    assert C.holm([0.01, 0.04]) == [True, True]
    assert C.holm([0.04]) == [True]
    assert C.holm([0.001, 0.03, 0.2, 0.01]) == [True, False, False, True]


def test_positive_years_need_three_including_2021_or_2022():
    days = pd.DatetimeIndex([pd.Timestamp(f"{y}-03-{d:02d} 09:00", tz="UTC") for y in (2018, 2019, 2020)
                             for d in range(1, 21)])
    ok, detail = C.positive_years(np.full(len(days), 0.3), days)
    assert not ok and "2021" in detail


def test_a_spec_is_read_once_on_the_confirm_window_whatever_happens(env):
    prereg(env, 0)
    run(env, {H(0): read(stress=-0.01)})
    with pytest.raises(StageRefused):
        run(env, {H(0): read()})                                          # the batch is closed
    with pytest.raises(StageRefused, match="read on the confirm window already"):
        prereg(env, 0)                                                     # a failed hash is never re-confirmed


def test_a_crash_during_the_read_voids_it_counts_it_and_never_allows_a_second_look(env):
    prereg(env, 0)

    def boom(s: StrategySpec) -> C.StageRead:
        raise RuntimeError("bars unreadable")
    with pytest.raises(RuntimeError):
        C.run_confirm_batch(env["reg"], env["ledger"], pd.Timestamp.now("UTC").strftime("%Y-%m"), boom, cap=CAP,
                            reserved=0)
    reg = env["reg"]
    [trial] = [r for r in reg._rows() if r.get("status") == C.STATUS_CONFIRM]
    assert trial["results"]["void"] and verdict_of(reg._rows(), trial) == "stop"
    assert StageJournal(env["ledger"].path).state(H(0)).confirm == "void"
    with pytest.raises(StageRefused):
        run(env, {H(0): read()})


# ------------------------------------------------------------------------------------------------ the holdout read
def _passer(env: dict[str, Any], i: int = 0) -> None:
    prereg(env, i)
    [v] = run(env, {H(i): read()})
    assert v.passed


def hold(env: dict[str, Any], i: int, r: C.StageRead, cap: int = CAP) -> C.HoldoutVerdict:
    return C.holdout_read(env["reg"], env["ledger"], H(i), lambda s: r, cap=cap, reserved=0)


def test_only_a_confirm_passer_is_read_on_the_holdout_once_through_the_registry_guard(env):
    with pytest.raises(StageRefused, match="confirm"):
        hold(env, 0, read())
    _passer(env)
    v = hold(env, 0, read(mean=0.3))
    assert v.passed and v.verdict["rule"].startswith("model-filtered trades in the held-out window")
    reg: TrialRegistry = env["reg"]
    assert reg.holdout_scored(C.FAMILY, C.config_for(spec(0)))
    [row] = [r for r in reg._rows() if r.get("status") == "holdout"]
    assert verdict_of(reg._rows(), row) == "continue"
    with pytest.raises(StageRefused, match="holdout"):
        hold(env, 0, read(mean=0.3))                                       # once: the result is final
    assert StageJournal(env["ledger"].path).state(H(0)).holdout == "passed"


def test_a_failed_holdout_read_is_a_one_way_stop(env):
    _passer(env)
    v = hold(env, 0, read(mean=-0.1))
    assert not v.passed
    assert StageJournal(env["ledger"].path).state(H(0)).holdout == "failed"
    with pytest.raises(StageRefused):
        hold(env, 0, read(mean=0.3))


def test_a_crashed_holdout_read_is_void_charged_stopped_and_never_repeated(env):
    _passer(env)

    def boom(s: StrategySpec) -> C.StageRead:
        raise RuntimeError("bars unreadable")
    with pytest.raises(RuntimeError):
        C.holdout_read(env["reg"], env["ledger"], H(0), boom, cap=CAP, reserved=0)
    reg = env["reg"]
    [row] = [r for r in reg._rows() if r.get("status") == "holdout"]
    assert row["results"]["void"] and verdict_of(reg._rows(), row) == "stop"
    assert StageJournal(env["ledger"].path).state(H(0)).holdout == "void"
    with pytest.raises(StageRefused):
        hold(env, 0, read(mean=0.3))


def test_a_holdout_contaminated_spec_cannot_be_holdout_scored(env):
    s = spec(40)
    env["ledger"].log_spec(s, "G5", "1", flags={"holdout_contaminated": True})
    assert env["ledger"].spec_flags(spec_hash(s)) == {"holdout_contaminated": True}
    with pytest.raises(StageRefused, match="contaminated"):
        C.holdout_read(env["reg"], env["ledger"], spec_hash(s), lambda x: read(), cap=CAP, reserved=0)


def test_the_holdout_read_is_charged_and_checked_under_the_lock(env):
    _passer(env)
    with pytest.raises(TrialBudgetExceeded):
        hold(env, 0, read(), cap=1)                                       # the confirm trial used the one slot
    assert StageJournal(env["ledger"].path).state(H(0)).holdout is None


# ------------------------------------------------------------------------------------------------ windows
def test_the_stage_windows_match_the_settings_and_refuse_the_next_windows_bars():
    from goldbot.research.autodisc.windows import StageFrame, WindowViolation
    r = load_settings().research
    assert r.holdout_from is not None
    assert C.WINDOWS["confirm"] == (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp(r.holdout_from, tz="UTC"))
    assert C.WINDOWS["holdout"] == r.holdout_window()
    ok = pd.DataFrame({"ts_utc": pd.to_datetime(["2017-06-01 00:00", "2025-09-30 23:59"], utc=True)})
    StageFrame(ok, "confirm")
    bad = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-09-30", "2025-10-01"], utc=True)})
    with pytest.raises(WindowViolation):
        StageFrame(bad, "confirm")
    with pytest.raises(WindowViolation):
        StageFrame(pd.DataFrame({"ts_utc": pd.to_datetime(["2026-10-01"], utc=True)}), "holdout")


def test_the_confirm_reader_opens_no_holdout_year_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for y in (2026,):
        (raw / f"xauusd_1m_dukascopy_{y}.parquet").write_bytes(b"")      # unreadable: opening it would raise
    assert C.stage_years("confirm") == (2017, 2025)
    with pytest.raises(FileNotFoundError):
        C.stage_bars(raw, "confirm")


def _write_year_files(raw: Path, start: str, end: str) -> None:
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    b = ticks_to_1m(synthetic_ticks(start, end, ticks_per_minute=2, seed=3))
    for y, g in b.groupby(pd.DatetimeIndex(b["ts_utc"]).year):
        g.reset_index(drop=True).to_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet")


def test_the_real_reader_scores_only_the_stage_windows_bars_with_the_warm_up_dropped(tmp_path):
    """Synthetic 1m bars written as year files in a temp dir (no market data): the warm-up month before 2018 feeds the
    indicators but no event; the confirm reader never sees a bar at or after the holdout's start."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_year_files(raw, "2017-12-01", "2018-01-31")
    t, prov, cost = C.stage_backends(load_settings(), "confirm", "1h", raw=raw, cost_table="")
    assert t.ts_utc.min() >= pd.Timestamp("2018-01-01", tz="UTC") and t.n_bars > 100 and cost
    s = StrategySpec(timeframe="1h", side_rule="long", entry=Leaf(kind="time", name="london"),
                     exit=ExitTemplate(stop="atr_1.0", target="1R"))
    rd = C.read_on(t, prov, cost)(s)
    assert rd.n_events > 0 and min(rd.ts_ns) >= pd.Timestamp("2018-01-01", tz="UTC").value
    edge = tmp_path / "edge"
    edge.mkdir()
    _write_year_files(edge, "2025-09-24", "2025-10-03")
    frame = C.stage_bars(edge, "confirm").frame
    assert pd.DatetimeIndex(frame["ts_utc"]).max() < pd.Timestamp("2025-10-01", tz="UTC") and len(frame)


def test_the_confirm_module_imports_nothing_that_trades():
    src = Path(C.__file__).read_text()
    for banned in ("goldbot.engine", "goldbot.risk", "goldbot.execution.broker", "accounts.yaml", "RiskGate"):
        assert banned not in src, banned
    assert os.path.basename(C.__file__) == "confirm.py"

"""autodisc_nightly (ADR 0058 phase 2): a bounded G2 search on the explore window, judged by the honesty set, a
survivors report under state/ and a one-line health summary. It never creates arms or touches the engine."""
import re
from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.research.autodisc import SearchLedger
from goldbot.research.autodisc import nightly as NT
from goldbot.research.autodisc.gen_gp import GPConfig
from goldbot.research.autodisc.nulls import SyntheticProvider, inject_edge, make_noise_tensor

FAMS = ("tsmom", "nr4_break")
N = 3000
CONFIRM = re.compile(r"(?<![0-9A-Za-z])20(1[89]|[2-9]\d)(?![0-9A-Za-z])|20(1[89]|[2-9]\d)-\d\d")
SLOT = pd.Timestamp("2026-11-10 01:30", tz="UTC")


def gp(**kw):
    return GPConfig(**{**dict(timeframe="15m", population=12, generations=3, seed=1, min_events=60, families=FAMS), **kw})


def edge_tensor():
    prov = SyntheticProvider(N, seed=0)
    mask, side = prov.family_entries("tsmom", "15m")
    return inject_edge(make_noise_tensor(N, seed=7), mask, side, edge_r=0.4), prov


def test_judge_applies_the_honesty_set_and_counts_every_evaluation_in_k(tmp_path):
    t, prov = edge_tensor()
    led = SearchLedger(tmp_path / "d.db", mode="synthetic")
    res = NT.search_and_judge(t, prov, led, gp(), reps=4)
    assert res.k_search == led.k_search() and res.k_null == led.k_null() > 0
    # neighbours are searched specs too: new ones add to K, one equal to an earlier spec adds nothing
    assert res.evaluated < res.k_search <= res.evaluated + res.neighbours_evaluated
    assert res.rc is not None and res.pbo is not None
    assert res.candidates >= len(res.survivors) >= 1
    for s in res.survivors:
        assert "tsmom" in s.spec_json and s.shift_p <= NT.CS_ALPHA and s.plateau.passed and s.fitness > res.null_threshold
    assert res.batch_ok and led.verify() == len(led.entries())


def test_judge_finds_no_survivor_on_pure_noise(tmp_path):
    led = SearchLedger(tmp_path / "n.db", mode="synthetic")
    res = NT.search_and_judge(make_noise_tensor(N, seed=11), SyntheticProvider(N, seed=3), led, gp(seed=3), reps=4)
    assert res.survivors == [] and res.evaluated > 0


def test_report_states_counts_and_never_a_confirm_window_number(tmp_path):
    t, prov = edge_tensor()
    res = NT.search_and_judge(t, prov, SearchLedger(tmp_path / "r.db", mode="synthetic"), gp(), reps=2)
    big = res.model_copy(update={"k_search": 2050, "evaluated": 2019, "k_null": 2024})
    text = NT.render(big)
    assert "K_search 2,050" in text and "SPA" in text and "PBO" in text and "ledger head" in text
    assert not CONFIRM.search(text)
    assert "not evidence of edge" in text


def test_job_is_off_unless_enabled_and_skips_without_bars(tmp_path):
    s = load_settings()
    assert s.research.autodisc.nightly_enabled is False                      # the brain never searches (ADR 0058 s5)
    assert "skipped" in NT.run_nightly_job(s, tmp_path, SLOT)
    on = s.model_copy(update={"research": s.research.model_copy(update={"autodisc": s.research.autodisc.model_copy(
        update={"nightly_enabled": True, "raw_dir": str(tmp_path / "empty")})})})
    out = NT.run_nightly_job(on, tmp_path, SLOT)
    assert "no 2010-2017 1m bars" in out["skipped"]
    assert not (tmp_path / "autodisc").exists()


def test_job_writes_the_survivors_report_and_a_health_line_and_nothing_else(tmp_path):
    s = load_settings()
    on = s.model_copy(update={"research": s.research.model_copy(update={"autodisc": s.research.autodisc.model_copy(
        update={"nightly_enabled": True, "population": 8, "generations": 2, "null_reps": 2})})})
    t, prov = edge_tensor()

    def backends(settings, timeframe):
        return NT.Backends(tensor=t, provider=prov, families=FAMS, cost_line="synthetic", mode="synthetic")

    before = set(Path(tmp_path).rglob("*"))
    out = NT.run_nightly_job(on, tmp_path, SLOT, backends=backends, min_events=60)
    report = tmp_path / "autodisc" / "survivors-2026-11-10.md"
    assert report.exists() and out["report"] == "autodisc/survivors-2026-11-10.md"
    assert out["k_search"] > 0 and isinstance(out["survivors"], int) and out["health"].startswith("autodisc ")
    assert not CONFIRM.search(report.read_text())
    written = {p.relative_to(tmp_path).as_posix() for p in set(Path(tmp_path).rglob("*")) - before if p.is_file()}
    assert written <= {"autodisc/survivors-2026-11-10.md", "discovery_synthetic.db", "discovery_synthetic.db-wal",
                       "discovery_synthetic.db-shm"}


def test_the_nightly_seed_moves_with_the_day_so_each_night_searches_new_ground():
    assert NT.night_seed(0, SLOT) != NT.night_seed(0, SLOT + pd.Timedelta(days=1))
    assert NT.night_seed(0, SLOT) == NT.night_seed(0, SLOT)


def test_nightly_imports_nothing_that_trades_or_writes_arms():
    src = Path(NT.__file__).read_text()
    for banned in ("goldbot.engine", "goldbot.risk", "goldbot.execution.broker", "exploration_arms.yaml",
                   "accounts.yaml", "RiskGate", "registry.preregister"):
        assert banned not in src, banned


def test_autodisc_nightly_is_a_scheduled_job_that_health_reports():
    from goldbot.ops.jobs import JOBS, autodisc_nightly
    s = load_settings()
    assert JOBS["autodisc_nightly"] is autodisc_nightly
    assert "autodisc_nightly" in type(s.scheduler).model_fields             # health: one job:<name> line per field
    assert s.scheduler.autodisc_nightly.kind == "daily"


@pytest.mark.parametrize("bad", [{"population": 2}, {"generations": 0}, {"null_reps": 0}])
def test_autodisc_settings_are_bounded(bad):
    from goldbot.config import AutodiscSettings
    with pytest.raises(ValueError):
        AutodiscSettings(**bad)


def _real_cost_line() -> str:
    from goldbot.execution.costs import CostTable, research_costs
    table = CostTable(account_id="icm-demo", built_utc=pd.Timestamp("2026-10-28", tz="UTC"), spread={}, slippage={},
                      commission_per_lot_side_usd=3.5, slippage_prior_usd=0.1)
    return research_costs(load_settings(), table)[2]


def test_report_strips_the_cost_table_build_date_so_a_measured_table_does_not_fail_the_night(tmp_path):
    line = _real_cost_line()
    assert "built 2026-10-28" in line                                        # the real line carries a confirm-era date
    t, prov = edge_tensor()
    res = NT.search_and_judge(t, prov, SearchLedger(tmp_path / "c.db", mode="synthetic"), gp(), reps=2, cost_line=line)
    text = NT.render(res)
    assert "costs: cost table icm-demo, commission" in text and not CONFIRM.search(text)


def test_real_backends_skip_a_raw_dir_without_the_explore_bar_files(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "xauusd_1m_dukascopy_2021.parquet").write_bytes(b"")   # only a confirm-era file: never opened
    assert NT.real_backends(load_settings(), "15m", raw=tmp_path / "raw") is None


def test_the_survivors_report_is_written_atomically(tmp_path, monkeypatch):
    s = load_settings()
    on = s.model_copy(update={"research": s.research.model_copy(update={"autodisc": s.research.autodisc.model_copy(
        update={"nightly_enabled": True, "population": 8, "generations": 1, "null_reps": 1})})})
    t, prov = edge_tensor()
    report = tmp_path / "autodisc" / "survivors-2026-11-10.md"
    report.parent.mkdir(parents=True)
    report.write_text("last good report\n")

    def boom(r):
        raise RuntimeError("render failed")

    monkeypatch.setattr(NT, "render", lambda r: "partial " + boom(r))
    with pytest.raises(RuntimeError):
        NT.run_nightly_job(on, tmp_path, SLOT, min_events=60, backends=lambda st, tf: NT.Backends(
            tensor=t, provider=prov, families=FAMS, cost_line="synthetic", mode="synthetic"))
    assert report.read_text() == "last good report\n"
    monkeypatch.undo()
    calls = []
    real_replace = NT.os.replace

    def spy(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        return real_replace(src, dst)

    monkeypatch.setattr(NT.os, "replace", spy)
    NT.run_nightly_job(on, tmp_path, SLOT, min_events=60, backends=lambda st, tf: NT.Backends(
        tensor=t, provider=prov, families=FAMS, cost_line="synthetic", mode="synthetic"))
    assert calls and calls[-1][1] == report.name and calls[-1][0] != report.name
    assert not [p for p in report.parent.iterdir() if p.name != report.name]       # no temp file left behind


def test_the_report_carries_cumulative_k_and_survivors_across_nights_and_the_confirm_dsr_rule(tmp_path):
    """Quant review F2: multiplicity across nights. K_search and the survivor count are cumulative over every night
    in the ledger, and each survivor states its DSR at that cumulative K against the 0.95 confirm bar."""
    t, prov = edge_tensor()
    led = SearchLedger(tmp_path / "m.db", mode="synthetic")
    first = NT.search_and_judge(t, prov, led, gp(seed=1), reps=2, night="night-1")
    second = NT.search_and_judge(t, prov, led, gp(seed=2), reps=2, night="night-2")
    assert second.k_search == led.k_search() > first.k_search
    assert 0 < second.k_search_tonight < second.k_search
    assert first.survivors_cumulative == len({s.spec_hash for s in first.survivors}) >= 1
    assert second.survivors_cumulative == led.n_survivors() >= first.survivors_cumulative
    text = NT.render(second)
    assert f"K_search {second.k_search:,} cumulative over all nights ({second.k_search_tonight:,} new tonight)" in text
    assert f"explore survivors, cumulative over all nights {second.survivors_cumulative:,}" in text
    assert "advancing to confirm requires DSR >= 0.95 at the cumulative K_search" in text
    for s in second.survivors:
        assert f"DSR at cumulative K_search {second.k_search:,}: {s.dsr:,.3f}" in text
        assert s.meets_confirm_dsr == NT.meets_confirm_bar(s.dsr, s.n_events)
    assert NT.DSR_CONFIRM_BAR == 0.95 and not hasattr(NT.S, "DSR_BAR")


def test_the_report_lists_every_judged_candidate_that_failed_with_the_test_it_failed(tmp_path):
    """Quant review F5: one line per judged candidate that is not a survivor, naming the failed check."""
    t, prov = edge_tensor()
    res = NT.search_and_judge(t, prov, SearchLedger(tmp_path / "f.db", mode="synthetic"), gp(), reps=2)
    assert res.judged == len(res.survivors) + len(res.rejected)
    rej = NT.Rejected(spec_hash="a" * 64, fitness=1.2, shift_p=0.31, plateau_share=0.25, plateau_ratio=0.1,
                      failed=["shift p 0.310 > 0.05", "plateau share 0.25 < 0.60", "plateau ratio 0.100 < 0.50"])
    text = NT.render(res.model_copy(update={"rejected": [rej], "batch_ok": False, "batch_why": ["SPA p 0.400 > 0.10"]}))
    assert "judged candidates that failed 1" in text
    assert "aaaaaaaaaaaa  fitness 1.200  failed: shift p 0.310 > 0.05; plateau share 0.25 < 0.60; plateau ratio " \
           "0.100 < 0.50" in text
    assert not CONFIRM.search(text)


def test_a_survivor_with_dsr_097_on_150_events_does_not_meet_the_confirm_bar():
    """Quant re-check: the gates' DSR check needs MIN_TRADES_FOR_DSR trades as well as the 0.95 bar."""
    assert NT.MIN_TRADES_FOR_DSR == 200
    assert not NT.meets_confirm_bar(0.97, 150)
    assert NT.meets_confirm_bar(0.97, 200) and not NT.meets_confirm_bar(0.94, 500)


def test_every_evaluated_spec_records_its_search_fitness_and_its_components_so_the_top_can_be_ranked_later(tmp_path):
    t, prov = edge_tensor()
    led = SearchLedger(tmp_path / "f.db", mode="synthetic")
    res = NT.search_and_judge(t, prov, led, gp(), reps=2)
    assert res.evaluated > 0
    for e in led.entries():
        if e.kind != "result" or e.is_null:
            continue
        r = led.result(e.spec_hash)
        assert r is not None
        for key in ("search_fitness", "fold_t", "excess_fold_t", "t_stat", "time_in_market", "floor_failures"):
            assert key in r, key
        assert len(r["fold_t"]) == len(r["excess_fold_t"]) == 4
        if r["floors"] and r["excess_fitness"] is not None:
            assert r["search_fitness"] == r["excess_fitness"]                 # gp_fitness: what the search ranked by
        else:
            assert r["search_fitness"] is None                                 # below the floors: -inf, stored as null


def test_a_candidate_that_beats_the_null_but_fails_an_explore_check_is_reported_with_the_check_and_its_value(
        tmp_path, monkeypatch):
    from goldbot.research.autodisc.nulls import NullDistribution
    monkeypatch.setattr(NT, "gp_null", lambda *a, **k: NullDistribution((float("-inf"),) * 4, 0))   # bar floored at 0
    from dataclasses import replace
    t, prov = edge_tensor()
    t = replace(t, cost=t.cost + 2.0 * t.risk)              # the edge survives the cost, never the 1.5x cost stress
    led = SearchLedger(tmp_path / "x.db", mode="synthetic")
    res = NT.search_and_judge(t, prov, led, gp(), reps=4)
    assert res.explore_failed and res.candidates == 0
    for x in res.explore_failed:
        assert x.fitness > res.null_threshold and x.failed
        assert all(re.search(r"-?\d+\.\d{3}", f) for f in x.failed)              # the check AND its value
    searched = [led.result(e.spec_hash) or {} for e in led.entries()
                if e.kind == "result" and not e.is_null]
    beat = [r for r in searched if r.get("generation", -1) >= 0 and r.get("search_fitness") is not None
            and r["search_fitness"] > res.null_threshold]
    assert res.candidates + len(res.explore_failed) == len(beat)
    text = NT.render(res)
    assert f"beat the null but failed an explore check {len(res.explore_failed):,}" in text
    assert f"{res.explore_failed[0].spec_hash[:12]}  fitness" in text and res.explore_failed[0].failed[0] in text
    assert not CONFIRM.search(text)


def test_explore_failures_name_each_failed_check_with_its_value():
    t, prov = edge_tensor()
    from goldbot.research.autodisc.evaluate import evaluate
    mask, side = prov.family_entries("tsmom", "15m")
    e = evaluate(t, mask, side, 0, timeframe="15m", min_events=60)
    bad = e.model_copy(update={"fitness": -0.25, "excess_r": -0.01, "stress_mean_r": -0.002, "stress_passed": False})
    assert NT.explore_failures(bad) == ["min-fold t -0.250 <= 0", "excess R -0.010 <= 0",
                                        "mean R at 1.5x cost -0.002 <= 0"]
    good = e.model_copy(update={"fitness": 1.0, "excess_r": 0.1, "stress_mean_r": 0.1, "stress_passed": True,
                                "floors_passed": True})
    assert NT.explore_failures(good) == []

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

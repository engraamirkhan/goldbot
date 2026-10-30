import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "autodisc.py"
spec = importlib.util.spec_from_file_location("autodisc_cli", SCRIPT)
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_report_refuses_confirm_window_numbers():
    for bad in ("sharpe on 2019-03 was 1", "year 2021 net R", "2025-10-01"):
        with pytest.raises(ValueError):
            cli.assert_no_confirm_numbers(bad)
    cli.assert_no_confirm_numbers("explore window 2010-2017, K 12, hash 2019abcd2021ef")


def test_run_report_has_counts_head_and_no_2018_plus(tmp_path, capsys):
    run_json = tmp_path / "run.json"
    rc = cli.main(["run", "--synthetic", "--generator", "combine", "--limit", "40", "--reps", "4", "--min-events", "60",
                   "--db", str(tmp_path / "d.db"), "--run-json", str(run_json)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "K_search 40" in out and "K_null 40" in out and "ledger head seq" in out
    assert "survivors" in out and "null" in out
    assert not re.search(r"(?<![0-9A-Za-z])20(1[89]|[2-9]\d)(?![0-9A-Za-z])|20(1[89]|[2-9]\d)-\d\d", out)
    assert cli.main(["report", "--run-json", str(run_json)]) == 0
    assert capsys.readouterr().out == out


def test_bench_refuses_without_synthetic(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["bench", "--db", str(tmp_path / "d.db")])


def test_variant_flagged_on_survivor(tmp_path, capsys):
    from goldbot.research.autodisc.gen_combine import exit_menu, family_spec
    from goldbot.research.autodisc.nulls import SyntheticProvider, make_noise_tensor

    t = make_noise_tensor(3000, seed=1)
    s = family_spec("tsmom", "15m", None, exit_menu("15m")[0])
    labels = cli.novelty_labels(t, [s], [cli.spec_hash(s)], SyntheticProvider(3000))
    assert labels[cli.spec_hash(s)] == "variant_of:tsmom"


def test_ledger_modes_never_mix(tmp_path):
    from goldbot.research.autodisc import LedgerError, SearchLedger

    db = tmp_path / "m.db"
    assert cli.main(["run", "--synthetic", "--limit", "5", "--reps", "2", "--min-events", "60", "--db", str(db)]) == 0
    with pytest.raises(LedgerError):
        SearchLedger(db, mode="real")
    SearchLedger(db, mode="synthetic")
    assert cli.DEFAULT_DB["real"] != cli.DEFAULT_DB["synthetic"] and cli.DEFAULT_REPS >= 100


def test_run_json_records_dropped_and_cost_line(tmp_path):
    import json

    rj = tmp_path / "r.json"
    cli.main(["run", "--synthetic", "--limit", "5", "--reps", "2", "--min-events", "60", "--db", str(tmp_path / "d.db"),
              "--run-json", str(rj)])
    assert json.loads(rj.read_text())["dropped_unsupported"] == 0


def test_report_prints_large_counts_with_separators_so_a_count_never_reads_as_a_confirm_year():
    from goldbot.research.autodisc.nulls import FunnelResult

    res = FunnelResult(k_search=2050, k_null=2019, evaluated=2024, passed_floors=2100, beat_null=0,
                       passed_explore_checks=0, null_threshold=0.5, real_best_null_percentile=0.5, real_best=0.1)
    text = cli.render_report(res, {}, (2077, "ab"), "G1 combine", 100, 1 / 101)
    assert "K_search 2,050" in text and "seq 2,077" in text


def test_nightly_command_runs_the_scheduler_job_on_this_machine(tmp_path, monkeypatch):
    seen = {}

    def fake(settings, state_dir, slot, **kw):
        seen.update(enabled=settings.research.autodisc.nightly_enabled, state_dir=state_dir)
        return {"health": "autodisc test"}

    monkeypatch.setattr(cli, "run_nightly_job", fake)
    assert cli.main(["nightly", "--state-dir", str(tmp_path)]) == 0
    assert seen == {"enabled": True, "state_dir": tmp_path}

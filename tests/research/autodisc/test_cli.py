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


def test_real_backends_refused_until_integration(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["run", "--db", str(tmp_path / "d.db")])


def test_variant_flagged_on_survivor(tmp_path, capsys):
    from goldbot.research.autodisc.gen_combine import exit_menu, family_spec
    from goldbot.research.autodisc.nulls import SyntheticProvider, make_noise_tensor

    t = make_noise_tensor(3000, seed=1)
    s = family_spec("tsmom", "15m", None, exit_menu("15m")[0])
    labels = cli.novelty_labels(t, [s], [cli.spec_hash(s)], SyntheticProvider(3000))
    assert labels[cli.spec_hash(s)] == "variant_of:tsmom"

"""research_pass.py end to end on synthetic bars laid out like the data-v1 release: report, per-year table and
a registry row whose trial counter grows run over run."""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.quality import check_bars
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks

pytestmark = pytest.mark.integration

_spec = importlib.util.spec_from_file_location("research_pass", Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py")
assert _spec is not None and _spec.loader is not None
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)


@pytest.fixture(scope="module")
def release_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("release")
    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2022-10-01", "2025-10-01", ticks_per_minute=1, seed=11)))
    b1["source"] = "synthetic"
    years = pd.DatetimeIndex(b1["ts_utc"]).year
    for y in sorted(set(years)):
        b1[years == y].to_parquet(d / f"xauusd_1m_dukascopy_{y}.parquet", index=False)
    return d


def test_research_pass_reports_and_records_trials(release_dir, tmp_path, monkeypatch, capsys):
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    argv = ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry), "--report", str(report)]
    monkeypatch.setattr(sys, "argv", argv)
    assert rp.main() == 0
    text = report.read_text()
    assert "session_open walk-forward on real bars (2022-2025)" in text
    assert "| all_candidates |" in text and "### Per year" in text
    rows = [json.loads(line) for line in registry.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["family"] == "session_open"
    assert rows[0]["results"]["n_folds"] >= 1
    assert rows[0]["results"]["lookahead"]["lookahead_columns"] == []     # every feature is as-of

    # a second run on a narrower window is a new trial; the counter feeds the deflated Sharpe
    monkeypatch.setattr(sys, "argv", argv + ["--from-year", "2023"])
    assert rp.main() == 0
    assert [json.loads(line)["trial"] for line in registry.read_text().splitlines()] == [1, 2]


def test_missing_files_fail_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(tmp_path)])
    with pytest.raises(SystemExit, match="no xauusd_1m_dukascopy"):
        rp.main()

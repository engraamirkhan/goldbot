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


def test_variants_are_separate_trials_and_typos_fail(release_dir, tmp_path, monkeypatch):
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    variants = '[{}, {"asia_range_max_atr_d": 1.2}]'
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry),
                                      "--report", str(report), "--variants", variants])
    assert rp.main() == 0
    rows = [json.loads(line) for line in registry.read_text().splitlines()]
    assert [r["trial"] for r in rows] == [1, 2] and rows[1]["config"]["asia_range_max_atr_d"] == 1.2
    assert "overrides" in rows[1]["rationale"] and "overrides" not in rows[0]["rationale"]
    text = report.read_text()
    assert "## session_open: 2 variants" in text and '`{"asia_range_max_atr_d": 1.2}`' in text
    with pytest.raises(SystemExit, match="unknown session_open settings"):
        rp.parse_variants("session_open", '[{"asia_range_max": 1.2}]')
    with pytest.raises(SystemExit, match="does not run on"):
        rp.parse_variants("trend", '[{"timeframe": "4h"}]')


def test_reports_carry_gates_rule_only_and_the_cost_line(release_dir, tmp_path, monkeypatch):
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry),
                                      "--report", str(report), "--extra-cost-usd", "0.3"])
    assert rp.main() == 0
    text = report.read_text()
    assert "### Design gates: **FAIL**" in text and "FAIL candidates" in text      # 3 synthetic years: far too few
    assert "### The rule alone" in text and "gross (mid prices, no costs)" in text
    assert "0.30 $/oz round trip" in text and "holdout 2025-10-01 .. 2026-10-01 excluded" in text
    row = json.loads(registry.read_text().splitlines()[0])
    assert row["results"]["gates"]["passed"] is False and row["results"]["evaluation"] == "cross-fitted"
    assert row["budget_quarter"] and row["status"] == "evaluated"


def test_trial_budget_refuses_and_the_holdout_is_scored_once(release_dir, tmp_path, monkeypatch):
    from goldbot.research.registry import TrialRegistry, quarter_of
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    reg = TrialRegistry(registry)
    for _ in range(20):                                                  # this quarter's budget is spent
        reg.record(agent_id="x", family="trend", config={}, feature_version="f", rationale="r", results={},
                   budget_quarter=quarter_of())
    argv = ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry), "--report", str(report)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="trial budget exceeded"):
        rp.main()
    # scoring the holdout is not a search trial: allowed once per configuration, then refused
    monkeypatch.setattr(sys, "argv", argv + ["--score-holdout"])
    assert rp.main() == 0
    last = json.loads(registry.read_text().splitlines()[-1])
    assert last["status"] == "holdout" and last["results"]["holdout"]["scored"] is True and "budget_quarter" not in last
    assert "**holdout scoring**" in report.read_text()
    with pytest.raises(SystemExit, match="already scored on the holdout"):
        rp.main()

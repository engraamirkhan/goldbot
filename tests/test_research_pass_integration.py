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
    argv = ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry), "--report", str(report),
            "--skip-screen"]
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
                                      "--report", str(report), "--variants", variants, "--skip-screen"])
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
                                      "--report", str(report), "--extra-cost-usd", "0.3", "--skip-screen"])
    assert rp.main() == 0
    text = report.read_text()
    assert "### Design gates: **FAIL**" in text and "FAIL candidates" in text      # 3 synthetic years: far too few
    assert "### The rule alone" in text and "gross (mid prices, no costs)" in text
    assert "0.30 $/oz round trip" in text and "holdout 2025-10-01 .. 2026-10-01 excluded" in text
    row = json.loads(registry.read_text().splitlines()[0])
    assert row["results"]["gates"]["passed"] is False and row["results"]["evaluation"] == "cross-fitted"
    assert row["budget_quarter"] and row["status"] == "evaluated"


def test_holdout_needs_passed_gates_is_charged_and_scored_once_and_the_budget_refuses(release_dir, tmp_path, monkeypatch):
    from goldbot.research.registry import TrialRegistry, quarter_of
    from goldbot.specialists import SPECIALISTS
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    argv = ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry), "--report", str(report)]
    monkeypatch.setattr(sys, "argv", argv + ["--score-holdout"])
    with pytest.raises(SystemExit, match="no research trial that passed the design's gates"):
        rp.main()                                                        # only a passing configuration is scored
    reg = TrialRegistry(registry)
    reg.record(agent_id="x", family="session_open", config=SPECIALISTS["session_open"]().config, feature_version="f",
               rationale="a passing walk-forward", results={"gates": {"passed": True, "checks": []}})
    assert rp.main() == 0
    last = json.loads(registry.read_text().splitlines()[-1])
    assert last["status"] == "holdout" and last["results"]["holdout"]["scored"] is True
    assert last["budget_quarter"] == quarter_of() and reg.budget_used(quarter_of()) == 2     # charged like any trial
    assert "gates" not in last["results"] and "holdout_verdict" in last["results"]           # judged by its own rule
    assert "### Held-out year:" in report.read_text() and "**holdout scoring**" in report.read_text()
    with pytest.raises(SystemExit, match="already scored on the holdout"):
        rp.main()
    for _ in range(18):                                                  # this quarter's budget is now spent
        reg.record(agent_id="x", family="trend", config={}, feature_version="f", rationale="r", results={})
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="trial budget exceeded"):
        rp.main()


def test_research_stops_at_the_holdout_and_ignores_truncated_folds(release_dir):
    """Bars run past the holdout start: nothing that exits at or after it is labelled, and the truncated last test
    fold does not count against the 60-per-fold gate."""
    from goldbot.data.resample import resample_bars
    from goldbot.features.mtf import TF_LABEL, context_tfs
    from goldbot.research.pipeline import run_specialist
    from goldbot.specialists import SPECIALISTS
    b1 = rp.load_bars(release_dir, 2022, 2025)
    dec = resample_bars(b1, "15m")
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs("15m")}
    start = pd.Timestamp("2025-04-01", tz="UTC")
    res = run_specialist(SPECIALISTS["session_open"](asia_range_max_atr_d=1.2), dec, context=context, extra_cost_usd=0.3,
                         holdout=(start, pd.Timestamp("2025-07-01", tz="UTC")))
    assert pd.to_datetime(res.oof["ts_exit"], utc=True).max() < start   # data runs to 2025-10: none of it is used
    m = res.metrics
    assert len(m["complete_fold_test_sizes"]) == len(m["fold_test_sizes"]) - 1   # the last fold is truncated
    per_fold = next(c for c in m["gates"]["checks"] if c["name"] == "per_fold")
    assert f"{len(m['complete_fold_test_sizes'])} test folds" in per_fold["detail"]


def _rows(registry: Path) -> list[dict]:
    return [json.loads(line) for line in registry.read_text().splitlines()]


def test_a_rule_that_fails_the_screen_gets_no_model_but_is_a_recorded_trial(release_dir, tmp_path, monkeypatch):
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    argv = ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry), "--report", str(report),
            "--specialist", "intraday_momentum", "--variants", '[{}, {"session": "london"}]']
    monkeypatch.setattr(sys, "argv", argv)
    assert rp.main() == 0                    # three synthetic years: far fewer than 1,000 events, so both fail
    rows = _rows(registry)
    assert [r["status"] for r in rows] == ["screened", "screened"] and [r["trial"] for r in rows] == [1, 2]
    scr = rows[0]["results"]["screen"]
    assert scr["passed"] is False and scr["n"] == rows[0]["results"]["n_candidates"] > 0
    assert {c["name"] for c in scr["checks"]} == {"events", "gross_mean_r", "gross_t"}
    assert "gates" not in rows[0]["results"] and "oof_auc" not in rows[0]["results"]
    assert rows[0]["results"]["lookahead"]["lookahead_columns"] == []
    assert rows[0]["budget_quarter"] and rows[1]["config"]["session"] == "london"
    text = report.read_text()
    assert "screen failed, no model fitted" in text and "| all_candidates |" not in text
    assert rows[0]["results"]["swap"]["server_tz"] == "Europe/Athens" and "- swap: long -60.00" in text
    assert "intraday_momentum: 2 variants" in text and "**fail**" in text

    # --skip-screen fits the model anyway and says so; the screen is still recorded
    monkeypatch.setattr(sys, "argv", argv[:-2] + ["--skip-screen"])
    assert rp.main() == 0
    last = _rows(registry)[-1]
    assert last["status"] == "evaluated" and last["results"]["screen_skipped"] is True
    assert last["results"]["screen"]["passed"] is False and "gates" in last["results"]
    assert "(skipped with --skip-screen)" in report.read_text()


def test_a_rule_that_passes_the_screen_goes_on_to_the_walk_forward(release_dir, tmp_path, monkeypatch):
    from goldbot.research import screen as screen_mod
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(screen_mod, "MIN_EVENTS", 1)
    monkeypatch.setattr(screen_mod, "MIN_T", -1e9)
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry),
                                      "--report", str(report), "--specialist", "tsmom"])
    real = screen_mod.screen_verdict

    def lenient(rule):                  # synthetic random walks have no edge: accept any sign for this test
        out = real({**rule, "gross": {**rule["gross"], "mean_r": abs(rule["gross"].get("mean_r", 0.0)) + 1e-9}})
        return {**out, "rule_only": rule}
    monkeypatch.setattr(screen_mod, "screen_verdict", lenient)
    assert rp.main() == 0
    row = _rows(registry)[0]
    assert row["status"] == "evaluated" and row["family"] == "tsmom" and row["results"]["screen"]["passed"] is True
    assert row["results"]["screen_skipped"] is False and "gates" in row["results"]
    assert row["results"]["walkforward"]["train_months"] == 36
    assert "### Primary-signal screen: **PASS**" in report.read_text()
    # swap from the settings prior is charged in the net labels and stated in the report
    sw = row["results"]["swap"]
    assert sw["long_usd_per_lot"] == -60.0 and sw["triple_weekday"] == 2 and sw["mean_nights"] > 0
    assert "- swap: long -60.00 / short +0.00 USD per lot per night, x3 on Wed" in report.read_text()
    # the same gates on the rule alone, next to the model's, labelled as informational
    rg = row["results"]["rule_only_gates"]
    assert [c["name"] for c in rg["checks"]] == ["candidates", "per_fold", "positive_years", "dsr"]
    assert "### Design gates, rule-only (informational; promotion still requires the model path):" in report.read_text()


def test_pooled_meta_model_is_one_trial_over_every_family_on_the_timeframe(release_dir, tmp_path, monkeypatch):
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    argv = ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry), "--report", str(report),
            "--pooled", "15m"]
    monkeypatch.setattr(sys, "argv", argv)
    assert rp.main() == 0                                                  # the union fails the screen too
    rows = _rows(registry)
    assert len(rows) == 1 and rows[0]["family"] == "pooled_15m" and rows[0]["status"] == "screened"
    assert set(rows[0]["results"]["screen"]["members"]) == {"intraday_momentum", "mean_reversion", "session_open"}

    monkeypatch.setattr(sys, "argv", argv + ["--skip-screen"])
    assert rp.main() == 0
    row = _rows(registry)[-1]
    res = row["results"]
    assert row["family"] == "pooled_15m" and row["status"] == "evaluated" and row["agent_id"].startswith("pooled_15m-")
    assert set(row["config"]["families"]) == {"intraday_momentum", "mean_reversion", "session_open"}
    assert res["families"] == ["intraday_momentum", "mean_reversion", "session_open"] and "gates" in res
    assert res["n_candidates"] == sum(b["n_candidates"] for b in res["by_family"].values())
    assert any("logloss" in b for b in res["by_family"].values())          # the pooled-vs-local comparison ran
    text = report.read_text()
    assert "## pooled_15m walk-forward" in text and "### Pooled model by family" in text

    monkeypatch.setattr(sys, "argv", argv + ["--variants", '[{"band_z": 1.5}]'])
    with pytest.raises(SystemExit, match="--variants does not apply"):
        rp.main()


def test_tsmom_daily_variant_is_screened_on_daily_bars(release_dir, tmp_path, monkeypatch):
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry),
                                      "--report", str(report), "--specialist", "tsmom",
                                      "--variants", '[{"timeframe": "1d"}]'])
    assert rp.main() == 0
    row = _rows(registry)[0]
    assert row["config"]["timeframe"] == "1d" and row["config"]["max_bars"] == 10 and row["config"]["lb_slow_h"] == 2880
    # three years of daily bars give a few dozen events: the screen's 1,000-event floor fails it, no model is fitted
    events = {c["name"]: c for c in row["results"]["screen"]["checks"]}["events"]
    assert row["status"] == "screened" and not events["passed"]
    assert row["results"]["lookahead"]["lookahead_columns"] == []
    text = report.read_text()
    assert "1d decision bars" in text and "- swap: long -60.00" in text

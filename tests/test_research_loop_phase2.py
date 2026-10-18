"""Continuous research loop, phase 2 (docs/plans/continuous-research-loop.md, ADR 0029): the loop's own budget path,
the per-timeframe survivor report, and the scheduled dispatch workflow. Loop trials are charged per run against
`research.loop_trials_per_run`, never against `trial_budget_quarter`, and every one is still a registry row in K."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.config import RetiredFamily, Settings, load_settings
from goldbot.research import cells
from goldbot.research.director import build_plan
from goldbot.research.generator import batch, generate, loop_refusal, survivors_by_timeframe
from goldbot.research.registry import config_hash, n_trials_effective, quarter_trials
from goldbot.specialists import SPECIALISTS

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-18", tz="UTC")
TFS = ("15m", "1h", "4h", "1d")
ATR_2024 = {"15m": 2.74, "1h": 5.91, "4h": 11.65, "1d": 28.50}      # docs/research/atr-measured-2026-10-18.md medians
SMALL_ATR = {tf: 0.5 for tf in TFS}                                   # every stop sizes at 0.01 lot
ATR_LAST_90D = {"15m": 8.01, "1h": 17.73, "4h": 36.61, "1d": 87.63}
TS = "2026-10-18T10:00:00+00:00"


def _queue_script():
    spec = importlib.util.spec_from_file_location("research_queue", ROOT / "scripts" / "research_queue.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gen(atr: dict[str, float], settings: Settings | None = None):
    plan = build_plan(NOW, sorted(SPECIALISTS), [], [], [], quarter_budget=20, monthly_total=48,
                      trial_budget_per_month=12, floor=2)
    return generate([], settings or load_settings(), plan, atr)


def _job(atr: dict[str, float] = ATR_2024) -> tuple[str, dict[str, Any]]:
    c = _gen(atr)[0]
    return c.family, SPECIALISTS[c.family](**c.overrides).config


def _with_research(settings: Settings, **update: Any) -> Settings:
    return settings.model_copy(update={"research": settings.research.model_copy(update=update)})


def test_survivors_per_timeframe_are_reported_and_a_quiet_market_sizes_at_least_as_many():
    busy = survivors_by_timeframe(_gen(ATR_LAST_90D), TFS)
    quiet = survivors_by_timeframe(_gen(ATR_2024), TFS)
    assert set(busy) == set(quiet) == set(TFS)
    assert busy["1d"] == 0                          # a 1.5 ATR stop at $88 cannot be sized at 0.01 lot on GBP 1,000
    assert sum(quiet.values()) >= sum(busy.values())
    assert sum(quiet.values()) > 0
    assert busy == survivors_by_timeframe(_gen(ATR_LAST_90D), TFS)


def test_queue_prints_per_timeframe_survivor_counts(capsys: pytest.CaptureFixture[str]):
    rc = _queue_script().main(["--registry", "/nonexistent/registry.jsonl", "--atr", json.dumps(ATR_2024)])
    err = capsys.readouterr().err
    assert rc == 0
    for tf in TFS:
        assert f"{tf}=" in err


def test_batch_entries_carry_the_loop_rule_ref():
    jobs = batch(_gen(SMALL_ATR), 3)
    assert len(jobs) == 3 and all(j["rule_ref"] == "loop" for j in jobs)


def test_quarter_budget_does_not_count_loop_rows_but_k_counts_every_row():
    base = {"ts": TS, "family": "a", "status": "evaluated"}
    rows = [{**base, "trial": 1, "config_hash": "1"},
            {**base, "trial": 2, "config_hash": "2", "rule_ref": "loop"},
            {**base, "trial": 3, "config_hash": "3", "rule_ref": "loop", "status": "screened"}]
    assert quarter_trials(rows, "2026Q4") == 1
    assert n_trials_effective(rows) == 3


def test_loop_run_within_its_per_run_budget_is_accepted():
    assert loop_refusal([], [_job()], load_settings()) is None


def test_loop_run_over_its_per_run_budget_is_refused_and_zero_turns_the_loop_off():
    jobs = [(c.family, SPECIALISTS[c.family](**c.overrides).config) for c in _gen(SMALL_ATR)[:3]]
    assert "loop_trials_per_run" in (loop_refusal([], jobs, _with_research(load_settings(), loop_trials_per_run=2)) or "")
    assert loop_refusal([], jobs[:1], _with_research(load_settings(), loop_trials_per_run=0))


def test_loop_run_refuses_a_config_hash_already_in_the_registry():
    family, cfg = _job()
    rows = [{"trial": 1, "ts": TS, "status": "evaluated", "family": family, "config_hash": config_hash(cfg)}]
    assert "already" in (loop_refusal(rows, [(family, cfg)], load_settings()) or "")


def test_loop_run_refuses_holdout_scoring_and_discovery():
    assert "holdout" in (loop_refusal([], [_job()], load_settings(), score_holdout=True) or "")
    assert "discover" in (loop_refusal([], [_job()], load_settings(), discover=True) or "")


def test_loop_run_refuses_a_retired_family():
    family, cfg = _job()
    settings = _with_research(load_settings(), retired_families=[RetiredFamily(
        family=family, hypothesis_id="R-99", retired=date(2026, 1, 1), reason="test")])
    assert "retired" in (loop_refusal([], [(family, cfg)], settings) or "")


def test_loop_run_refuses_a_config_a_pre_registration_queued():
    family, cfg = _job()
    rows = [{"trial": 1, "ts": TS, "status": "preregistered", "family": family, "config_hash": config_hash(cfg),
             "config": cfg, "target_quarter": "2027Q1"}]
    assert "pre-registered" in (loop_refusal(rows, [(family, cfg)], load_settings()) or "")


def test_dispatch_with_more_loop_cells_than_the_per_run_budget_is_refused():
    item = {"specialist": "tsmom", "variants": "[{}]", "rule_ref": "loop"}
    with pytest.raises(ValueError, match="loop_trials_per_run"):
        cells.plan({"BATCH": json.dumps([item] * 17)})
    assert len(cells.plan({"BATCH": json.dumps([item] * 16)})) == 16
    assert len(cells.plan({"BATCH": json.dumps([{**item, "rule_ref": "x"}] * 17)})) == 17     # hand-dispatched: not capped


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github/workflows/research-loop.yml").read_text())


def test_loop_workflow_shares_the_registry_concurrency_group():
    wf = _workflow()
    assert wf["concurrency"] == {"group": "research-registry", "cancel-in-progress": False}
    assert wf["permissions"] == {}


def test_loop_workflow_gives_actions_write_only_to_the_dispatch_job():
    jobs = _workflow()["jobs"]
    assert jobs["dispatch"]["permissions"] == {"contents": "read", "actions": "write"}
    assert all(j["permissions"] == {"contents": "read"} for n, j in jobs.items() if n != "dispatch")


def test_loop_workflow_runs_every_6_hours_and_keeps_the_schedule_alive_check():
    wf = _workflow()
    on: dict[str, Any] = wf.get("on") or wf[True]
    assert on["schedule"] == [{"cron": "23 */6 * * *"}] and "workflow_dispatch" in on
    assert "keepalive" in wf["jobs"]


def test_keepalive_check_fails_when_the_last_commit_is_older_than_50_days():
    spec = importlib.util.spec_from_file_location("loop_keepalive", ROOT / "scripts" / "loop_keepalive.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
    assert mod.main(["--last-commit", str(int((now - dt.timedelta(days=49)).timestamp())), "--now", str(int(now.timestamp()))]) == 0
    assert mod.main(["--last-commit", str(int((now - dt.timedelta(days=51)).timestamp())), "--now", str(int(now.timestamp()))]) == 1

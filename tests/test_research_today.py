"""Research card C (docs/ux/limits-and-tradingview.md): `ResearchView.today` counts the trials of the current UTC day
by verdict, server-side so the day boundary lives in one place. A recorded pass without its cost treatment is not
counted as a pass (registry.gate_pass_counts); a trial without a gate verdict is counted apart, never as a fail."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from goldbot.api import explain

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
COSTS = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0}, "cost_source": "ICM measured"}


def row(trial: int, ts: str, gates: dict | None, **results: object) -> dict:
    res: dict = dict(results)
    if gates is not None:
        res["gates"] = gates
    return {"trial": trial, "ts": ts, "agent_id": f"a{trial}", "family": "tsmom", "config": {}, "status": "evaluated",
            "rationale": f"trial {trial}", "results": res}


FAIL = {"passed": False, "checks": [{"name": "net_r", "passed": False, "detail": "net -0.03 R"}]}
PASS = {"passed": True, "checks": [{"name": "dsr", "passed": True, "detail": "0.97"}]}


@pytest.fixture
def state(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(explain, "_now", lambda: NOW)
    rows = [row(1, "2026-10-17T23:59:00+00:00", FAIL),                    # yesterday (UTC): not today
            row(2, "2026-10-18T00:00:00+00:00", FAIL),
            row(3, "2026-10-18T09:00:00+00:00", PASS, **COSTS),
            row(4, "2026-10-18T10:00:00+00:00", PASS),                     # pass without costs: not counted
            row(5, "2026-10-18T11:00:00+00:00", None),                     # no gate verdict recorded
            {"trial": 6, "ts": "2026-10-18T12:00:00+00:00", "status": "preregistered", "family": "tsmom"}]
    (tmp_path / "research_registry.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return tmp_path


def test_today_counts_the_utc_day_by_verdict(state, tmp_path):
    t = explain.research_view(state, None, tmp_path / "docs").today
    assert t.date_utc.isoformat() == "2026-10-18"
    assert (t.run, t.passed, t.not_counted, t.failed, t.no_verdict) == (4, 1, 1, 1, 1)


def test_the_latest_trial_of_the_day_is_named(state, tmp_path):
    t = explain.research_view(state, None, tmp_path / "docs").today
    assert t.latest is not None and t.latest.trial == 5


def test_the_quarter_end_is_the_last_day_of_the_quarter(state, tmp_path):
    t = explain.research_view(state, None, tmp_path / "docs").today
    assert t.quarter_end.isoformat() == "2026-12-31"


def test_no_trials_today_is_zero_with_no_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "_now", lambda: NOW)
    t = explain.research_view(tmp_path, None, tmp_path / "docs").today
    assert (t.run, t.latest) == (0, None)

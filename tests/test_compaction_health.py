"""BACKLOG 242: health WARNs when `journal_compact` keeps deferring partitions (3 consecutive runs with `deferred > 0`
for a table) or leaves its DuckDB spill directory (`<data_root>/.compact_tmp`) behind outside a running job for more
than 6 h. The job carries a per-table `deferred_streak` in its result (the previous run's result from the scheduler
status, plus one), so the check reads one document and needs no state file of its own."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.data import compact as compact_mod
from goldbot.data.compact import SPILL_DIR, CompactReport, deferred_streaks
from goldbot.ops import health
from goldbot.ops.checks import compaction
from tests.test_health import SETTINGS, make_ctx, write

NOW = pd.Timestamp("2026-11-14 09:00", tz="UTC")


def _settings(tmp: Path) -> Any:
    return SETTINGS.model_copy(update={"data_root": str(tmp / "data")})


def _job(*, started: pd.Timestamp, finished: pd.Timestamp | None, result: dict[str, Any] | None) -> dict[str, Any]:
    return {"last_started": started.isoformat(), "last_finished": finished.isoformat() if finished else None,
            "last_ok": True, "last_error": None, "last_result": result}


def _result(**streaks: tuple[int, int]) -> dict[str, Any]:
    """table=(deferred, deferred_streak)"""
    return {"deferred": sum(d for d, _ in streaks.values()),
            "tables": {t: {"table": t, "deferred": d, "deferred_streak": s} for t, (d, s) in streaks.items()}}


def _scheduler(tmp: Path, job: dict[str, Any] | None) -> None:
    write(tmp / "scheduler.json", {"ts": NOW.isoformat(), "jobs": {"journal_compact": job} if job else {}})


def _check(tmp: Path) -> health.Check:
    return compaction.check_compaction(make_ctx(tmp, now=NOW, settings=_settings(tmp)))


def _spill(tmp: Path, age_h: float) -> Path:
    d = tmp / "data" / SPILL_DIR
    d.mkdir(parents=True)
    f = d / "duckdb_temp_storage-0.tmp"
    f.write_bytes(b"x")
    t = (NOW - pd.Timedelta(hours=age_h)).timestamp()
    os.utime(f, (t, t))
    os.utime(d, (t, t))
    return d


# ---------------------------------------------------------------------------------------------- the streak
def test_streak_counts_consecutive_deferring_runs_per_table_and_resets_on_a_clean_run():
    prev = _result(decisions=(4, 2), fills=(1, 1))
    tables = {"decisions": {"deferred": 3}, "fills": {"deferred": 0}, "dq_events": {"deferred": 2}}
    assert deferred_streaks(prev, tables) == {"decisions": 3, "fills": 0, "dq_events": 1}
    assert deferred_streaks(None, tables) == {"decisions": 1, "fills": 0, "dq_events": 1}
    assert deferred_streaks({"tables": "junk"}, tables)["decisions"] == 1       # an unreadable previous: start over


def test_the_job_carries_the_streak_from_the_previous_result(tmp_path, monkeypatch):
    from goldbot.config import CompactSettings
    from goldbot.ops.jobs import journal_compact
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("1D"), finished=NOW - pd.Timedelta("23h"),
                              result=_result(decisions=(5, 2))))
    monkeypatch.setattr(compact_mod, "compact_table",
                        lambda store, table, **kw: CompactReport(table=table, deferred=7 if table == "decisions" else 0))
    ctx = SimpleNamespace(store=None, state_dir=tmp_path, settings=SimpleNamespace(
        compact=CompactSettings(tables=["decisions", "fills"])))
    out = journal_compact(ctx, NOW)  # type: ignore[arg-type]
    assert out["tables"]["decisions"]["deferred_streak"] == 3
    assert out["tables"]["fills"]["deferred_streak"] == 0


def test_a_run_that_raised_last_starts_the_streak_over_instead_of_counting_on_from_an_older_success(tmp_path,
                                                                                                   monkeypatch):
    """Code review 2026-11-14: the scheduler keeps the last successful result when a run raises, so the streak would
    count on from a success that is no longer the previous run. The previous run raised: the count starts over."""
    from goldbot.config import CompactSettings
    from goldbot.ops.jobs import journal_compact
    job = _job(started=NOW - pd.Timedelta("1D"), finished=NOW - pd.Timedelta("23h"), result=_result(decisions=(5, 2)))
    job.update(last_ok=False, last_error="RuntimeError: journal_compact failed")
    _scheduler(tmp_path, job)
    monkeypatch.setattr(compact_mod, "compact_table",
                        lambda store, table, **kw: CompactReport(table=table, deferred=7 if table == "decisions" else 0))
    ctx = SimpleNamespace(store=None, state_dir=tmp_path, settings=SimpleNamespace(
        compact=CompactSettings(tables=["decisions", "fills"])))
    out = journal_compact(ctx, NOW)  # type: ignore[arg-type]
    assert out["tables"]["decisions"]["deferred_streak"] == 1


def test_the_job_without_a_state_dir_starts_the_streak_at_one(monkeypatch):
    from goldbot.ops.jobs import journal_compact
    monkeypatch.setattr(compact_mod, "compact_table",
                        lambda store, table, **kw: CompactReport(table=table, deferred=1))
    out = journal_compact(SimpleNamespace(store=None), NOW)  # type: ignore[arg-type]
    assert out["tables"]["decisions"]["deferred_streak"] == 1


# ---------------------------------------------------------------------------------------------- the check
def test_three_consecutive_deferring_runs_warn_naming_the_table_and_the_count(tmp_path):
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("6h"), finished=NOW - pd.Timedelta("5h"),
                              result=_result(decisions=(0, 0), observe_ticks=(12, 3))))
    c = _check(tmp_path)
    assert c.name == "compaction" and c.status == "warn"
    assert "observe_ticks" in c.reason and "12" in c.reason and "3" in c.reason and "decisions" not in c.reason


def test_two_deferring_runs_are_ok(tmp_path):
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("6h"), finished=NOW - pd.Timedelta("5h"),
                              result=_result(observe_ticks=(12, 2))))
    c = _check(tmp_path)
    assert c.status == "ok" and "observe_ticks" in c.reason


def test_a_stale_spill_directory_outside_a_running_job_warns(tmp_path):
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("8h"), finished=NOW - pd.Timedelta("7h"),
                              result=_result(decisions=(0, 0))))
    _spill(tmp_path, 7)
    c = _check(tmp_path)
    assert c.status == "warn" and SPILL_DIR in c.reason and "7.0 h" in c.reason


def test_a_fresh_spill_directory_or_one_under_a_running_job_is_ok(tmp_path):
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("8h"), finished=NOW - pd.Timedelta("7h"),
                              result=_result(decisions=(0, 0))))
    d = _spill(tmp_path, 2)
    assert _check(tmp_path).status == "ok"
    t = (NOW - pd.Timedelta(hours=9)).timestamp()
    for p in (*d.iterdir(), d):
        os.utime(p, (t, t))
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("10h"), finished=None, result=None))   # still running
    assert _check(tmp_path).status == "ok"
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("1h"), finished=NOW - pd.Timedelta("11h"),
                              result=_result(decisions=(0, 0))))                           # a new run in flight
    assert _check(tmp_path).status == "ok"


def test_unreadable_scheduler_status_is_cant_check_not_a_pass(tmp_path):
    (tmp_path / "scheduler.json").write_text("{")
    c = _check(tmp_path)
    assert c.status == "warn" and "can't check" in c.reason


def test_the_check_runs_only_once_compaction_is_in_use_and_is_re_exported_from_health(tmp_path):
    assert health.check_compaction is compaction.check_compaction
    assert "check_compaction" in health.__all__
    ctx = make_ctx(tmp_path, now=NOW, settings=_settings(tmp_path))
    assert not compaction.compaction_in_use(ctx)                      # never ran, no spill: not judged (golden stays)
    _spill(tmp_path, 1)
    assert compaction.compaction_in_use(ctx)
    (tmp_path / "scheduler.json").write_text("{")
    assert compaction.compaction_in_use(ctx)                          # unreadable status: judged (can't check)


def test_in_use_from_a_finished_run_and_the_report_lists_it(tmp_path):
    _scheduler(tmp_path, _job(started=NOW - pd.Timedelta("6h"), finished=NOW - pd.Timedelta("5h"),
                              result=_result(decisions=(1, 3))))
    report = health.run_checks(make_ctx(tmp_path, now=NOW, settings=_settings(tmp_path)))
    row = [c for c in report.checks if c.name == "compaction"]
    assert row and row[0].status == "warn"
    assert json.dumps([c.name for c in report.checks]).count('"compaction"') == 1

"""The SRE's enable conditions for `discovery_nightly` (ADR 0090, SRE GO 2026-11-18): a failed discovery night reaches
the owner through the health -> Telegram alert path, and an enabled job whose last successful night is older than
26 h warns (and is told) too. A disabled job is not checked and never alerts."""
from __future__ import annotations

import json

import pandas as pd

from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.ops import health as Hm
from goldbot.ops.checks import discovery as D

NOW = pd.Timestamp("2026-11-24 09:00", tz="UTC")


def settings(enabled: bool = True):
    s = load_settings()
    return s.model_copy(update={"research": s.research.model_copy(update={
        "discovery": s.research.discovery.model_copy(update={"enabled": enabled})})})


def ctx(tmp_path, enabled: bool = True) -> Hm.HealthContext:
    return Hm.HealthContext(state_dir=tmp_path, now=NOW, settings=settings(enabled), get_secret=lambda k: None)


def add_run(state_dir, status: str, at: pd.Timestamp, **body) -> None:
    with Database.open(state_dir, "aux").write() as conn:
        conn.execute("INSERT INTO discovery_runs (night, run_ns, status, k_spent, survivors, body) VALUES (?,?,?,?,?,?)",
                     (f"{at:%Y-%m-%d}", int(at.value), status, 0, 0, json.dumps(body)))


def write_scheduler(state_dir, **job) -> None:
    doc = {"ts": NOW.isoformat(), "jobs": {"discovery_nightly": job}}
    (state_dir / "scheduler.json").write_text(json.dumps(doc))


def test_a_disabled_discovery_job_is_not_checked_and_never_alerts(tmp_path):
    add_run(tmp_path, "ok", NOW - pd.Timedelta(days=30))
    assert not D.discovery_enabled(ctx(tmp_path, enabled=False))
    names = {c.name for c in Hm.run_checks(ctx(tmp_path, enabled=False)).checks}
    assert D.NAME not in names


def test_an_enabled_job_with_a_successful_night_inside_26_hours_is_ok(tmp_path):
    add_run(tmp_path, "ok", NOW - pd.Timedelta(hours=8))
    c = D.check_discovery_last_run(ctx(tmp_path))
    assert c.status == "ok" and "8.0 h" in c.reason
    assert D.NAME in {c.name for c in Hm.run_checks(ctx(tmp_path)).checks}


def test_an_enabled_job_whose_last_success_is_older_than_26_hours_warns_and_is_told(tmp_path):
    add_run(tmp_path, "ok", NOW - pd.Timedelta(hours=27))
    add_run(tmp_path, "failed", NOW - pd.Timedelta(hours=3), error="MemoryError: ")   # a failure is not a success
    c = D.check_discovery_last_run(ctx(tmp_path))
    assert c.status == "warn" and "27.0 h" in c.reason and "26 h" in c.reason
    report = Hm.HealthReport(ts=NOW, status="warn", checks=[c])
    text = Hm.HealthWatch(tmp_path).alert(report)
    assert text is not None and D.NAME in text


def test_budget_and_over_budget_nights_count_as_successful_runs(tmp_path):
    for status in ("budget", "over_budget"):
        d = tmp_path / status
        d.mkdir()
        add_run(d, status, NOW - pd.Timedelta(hours=2))
        assert D.check_discovery_last_run(ctx(d)).status == "ok"


def test_an_enabled_job_that_never_succeeded_warns_26_hours_after_its_first_attempt(tmp_path):
    add_run(tmp_path, "failed", NOW - pd.Timedelta(hours=30))
    c = D.check_discovery_last_run(ctx(tmp_path))
    assert c.status == "warn" and "no successful" in c.reason


def test_a_job_disabled_at_its_last_slot_is_waiting_for_its_first_enabled_night(tmp_path):
    """Enabled after the scheduler's last slot ran it disabled (a skip): ok until that first enabled night, which the
    job:discovery_nightly check judges if it is missed or fails."""
    add_run(tmp_path, "ok", NOW - pd.Timedelta(days=40))                  # an old enabled period
    write_scheduler(tmp_path, last_ok=True, last_finished=(NOW - pd.Timedelta(hours=8)).isoformat(),
                    last_result={"skipped": "research.discovery.enabled is false (ADR 0090: on per host with bars_path)"},
                    next_slot=(NOW + pd.Timedelta(hours=16)).isoformat())
    c = D.check_discovery_last_run(ctx(tmp_path))
    assert c.status == "ok" and "first enabled night" in c.reason


def test_an_enabled_job_with_no_run_recorded_anywhere_warns(tmp_path):
    c = D.check_discovery_last_run(ctx(tmp_path))
    assert c.status == "warn" and "no discovery night" in c.reason


def test_a_failed_discovery_night_fails_its_job_check_and_reaches_the_telegram_alert(tmp_path):
    write_scheduler(tmp_path, last_ok=False, failures=1, last_error="discovery_nightly: MemoryError: ",
                    last_started=(NOW - pd.Timedelta(hours=8)).isoformat(),
                    last_finished=(NOW - pd.Timedelta(hours=7)).isoformat(),
                    next_slot=(NOW + pd.Timedelta(hours=16)).isoformat())
    checks = [c for c in Hm.check_scheduler(ctx(tmp_path)) if c.name == "job:discovery_nightly"]
    assert checks and checks[0].status == "fail" and "MemoryError" in checks[0].reason
    text = Hm.HealthWatch(tmp_path).alert(Hm.HealthReport(ts=NOW, status="fail", checks=checks))
    assert text is not None and "job:discovery_nightly" in text


def test_the_staleness_warning_is_on_the_owner_notified_list():
    assert D.NAME.startswith(Hm.NOTIFY_ON_WARN)

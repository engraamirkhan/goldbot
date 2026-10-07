"""Scheduler restart safety: one run per slot, bounded catch-up (max_late_hours), crash and failure handling."""
import json

import pandas as pd
import pytest

from goldbot.ops.scheduler import Schedule, Scheduler


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


class Clock:
    def __init__(self, start: str):
        self.now = ts(start)

    def __call__(self) -> pd.Timestamp:
        return self.now


NIGHTLY = Schedule(kind="daily", at="23:10", max_late_hours=12)


def test_a_multi_day_outage_catches_up_only_the_latest_slot_once(tmp_path):
    clock = Clock("2026-10-05 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)
    slots: list[pd.Timestamp] = []
    def record(slot: pd.Timestamp) -> None:
        slots.append(slot)
    sch.add("nightly", NIGHTLY, record)
    clock.now = ts("2026-10-09 01:00")          # down Mon..Thu: four slots missed, the last one 1 h 50 m ago
    assert sch.run_pending() == ["nightly"]
    assert slots == [ts("2026-10-08 23:10")]     # the job is handed its slot, not the wall clock
    assert sch.run_pending() == [] and sch.state["nightly"].runs == 1


@pytest.mark.parametrize("late, runs", [(pd.Timedelta(hours=12), True), (pd.Timedelta(hours=12, seconds=1), False)])
def test_max_late_hours_boundary(tmp_path, late, runs):
    clock = Clock("2026-10-07 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)
    sch.add("nightly", NIGHTLY, lambda slot: None)
    clock.now = ts("2026-10-07 23:10") + late
    assert sch.run_pending() == (["nightly"] if runs else [])
    st = sch.state["nightly"]
    assert st.last_slot == ts("2026-10-07 23:10")
    assert (st.last_skipped is None) == runs and st.runs == int(runs)


def test_lateness_is_judged_when_each_job_starts_not_when_the_round_starts(tmp_path):
    clock = Clock("2026-10-07 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)

    def long_job(slot: pd.Timestamp) -> None:
        clock.now = clock.now + pd.Timedelta(hours=3)     # takes three hours
    sch.add("a_long", Schedule(kind="daily", at="20:00", max_late_hours=12), long_job)
    sch.add("b_tight", Schedule(kind="daily", at="20:00", max_late_hours=2), lambda slot: None)
    clock.now = ts("2026-10-07 20:05")
    assert sch.run_pending() == ["a_long"]
    assert sch.state["b_tight"].last_skipped == ts("2026-10-07 23:05")


def test_a_job_that_kills_the_process_is_not_rerun_for_the_same_slot_after_restart(tmp_path):
    clock = Clock("2026-10-07 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)

    def crash(slot: pd.Timestamp) -> None:
        raise SystemExit("service killed mid-run")
    sch.add("retrain", NIGHTLY, crash)
    clock.now = ts("2026-10-07 23:15")
    with pytest.raises(SystemExit):
        sch.run_pending()
    restarted = Scheduler(tmp_path / "s.json", clock=clock)
    ran: list[pd.Timestamp] = []
    def record(slot: pd.Timestamp) -> None:
        ran.append(slot)
    restarted.add("retrain", NIGHTLY, record)
    assert restarted.run_pending() == [] and ran == []
    saved = restarted.state["retrain"]
    assert saved.last_started == ts("2026-10-07 23:15") and saved.last_finished is None
    clock.now = ts("2026-10-08 23:11")
    assert restarted.run_pending() == ["retrain"] and ran == [ts("2026-10-08 23:10")]


def test_failures_accumulate_errors_are_bounded_and_a_success_clears_the_error(tmp_path):
    clock = Clock("2026-10-05 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)
    outcomes = iter([ValueError("x" * 10_000), ValueError("again"), None])

    def flaky(slot: pd.Timestamp) -> dict:
        exc = next(outcomes)
        if exc is not None:
            raise exc
        return {"rows": 3}
    sch.add("costs", NIGHTLY, flaky)
    for day in ("05", "06", "07"):
        clock.now = ts(f"2026-10-{day} 23:30")
        assert sch.run_pending() == ["costs"]
        if day == "05":
            err = sch.state["costs"].last_error or ""
            assert len(err) <= 4000
    st = sch.state["costs"]
    assert st.runs == 3 and st.failures == 2 and st.last_ok and st.last_error is None and st.last_result == {"rows": 3}


def test_state_file_is_the_heartbeat_and_survives_jobs_no_longer_registered(tmp_path):
    clock = Clock("2026-10-07 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)
    sch.add("old_job", NIGHTLY, lambda slot: None)
    clock.now = ts("2026-10-07 23:11")
    sch.run_pending()
    later = Scheduler(tmp_path / "s.json", clock=clock)                  # old_job dropped from the config
    later.add("new_job", Schedule(kind="weekly", at="06:00", weekday=5), lambda slot: None)
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["ts"] == ts("2026-10-07 23:11").isoformat()
    assert saved["jobs"]["old_job"]["runs"] == 1 and saved["jobs"]["old_job"]["next_slot"] is None
    assert saved["jobs"]["new_job"]["next_slot"] == ts("2026-10-10 06:00").isoformat()
    assert set(later.status()["jobs"]) == {"new_job"}
    assert not list(tmp_path.glob("*.tmp"))


def test_first_weekday_monthly_slot_looks_back_across_month_boundaries():
    first_mon = Schedule(kind="monthly", at="02:00", weekday=0)
    assert first_mon.last_slot(ts("2026-10-31 23:00")) == ts("2026-10-05 02:00")
    assert first_mon.last_slot(ts("2026-10-05 01:59")) == ts("2026-09-07 02:00")
    assert first_mon.next_slot(ts("2026-10-05 02:00")) == ts("2026-11-02 02:00")     # strictly after now


def test_non_utc_clocks_are_read_in_utc():
    s = Schedule(kind="daily", at="23:10")
    ny = pd.Timestamp("2026-10-07 19:15", tz="America/New_York")                     # 23:15 UTC
    assert s.last_slot(ny) == ts("2026-10-07 23:10")

import json

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.ops.scheduler import Schedule, Scheduler


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


class Clock:
    def __init__(self, start: str):
        self.now = ts(start)

    def __call__(self) -> pd.Timestamp:
        return self.now


# --------------------------------------------------------------------------------------- slot arithmetic
def test_daily_weekday_slots_skip_the_weekend():
    s = Schedule(kind="daily", at="23:10", weekdays=(0, 1, 2, 3, 4))
    assert s.last_slot(ts("2026-10-05 23:09")) == ts("2026-10-02 23:10")   # Monday before the slot -> Friday's
    assert s.last_slot(ts("2026-10-05 23:10")) == ts("2026-10-05 23:10")
    assert s.next_slot(ts("2026-10-02 23:10")) == ts("2026-10-05 23:10")   # Friday -> Monday


def test_weekly_and_monthly_slots():
    sat = Schedule(kind="weekly", at="06:00", weekday=5)
    assert sat.last_slot(ts("2026-10-07 12:00")) == ts("2026-10-03 06:00")
    first_sunday = Schedule(kind="monthly", at="08:00", weekday=6)
    assert first_sunday.next_slot(ts("2026-10-02 00:00")) == ts("2026-10-04 08:00")
    assert first_sunday.next_slot(ts("2026-10-04 08:00")) == ts("2026-11-01 08:00")
    fifth = Schedule(kind="monthly", at="00:30", day=5)
    assert fifth.last_slot(ts("2026-11-04 00:00")) == ts("2026-10-05 00:30")


def test_a_monthly_schedule_limited_to_quarter_months_runs_quarterly():
    quarterly = Schedule(kind="monthly", at="14:00", weekday=6, months=(1, 4, 7, 10))
    assert quarterly.next_slot(ts("2026-10-02 00:00")) == ts("2026-10-04 14:00")      # first Sunday of October
    assert quarterly.next_slot(ts("2026-10-04 14:00")) == ts("2027-01-03 14:00")      # skips November and December
    assert quarterly.last_slot(ts("2026-12-31 00:00")) == ts("2026-10-04 14:00")      # found 88 days back
    assert quarterly.last_slot(ts("2027-01-03 13:59")) == ts("2026-10-04 14:00")


@pytest.mark.parametrize("kw", [dict(kind="weekly", at="06:00"), dict(kind="monthly", at="06:00"),
                                dict(kind="monthly", at="06:00", weekday=1, day=3), dict(kind="daily", at="25:00"),
                                dict(kind="monthly", at="06:00", weekday=6, months=(0,)),
                                dict(kind="monthly", at="06:00", weekday=6, months=())])
def test_invalid_schedules_are_rejected(kw):
    with pytest.raises(ValidationError):
        Schedule(**kw)


# --------------------------------------------------------------------------------------- runner semantics
def test_new_job_waits_for_its_next_slot_then_runs_once(tmp_path):
    clock = Clock("2026-10-07 12:00")                      # Wednesday
    sch = Scheduler(tmp_path / "s.json", clock=clock)
    calls: list[pd.Timestamp] = []

    def retrain(slot: pd.Timestamp) -> dict:
        calls.append(slot)
        return {"ok": 1}
    sch.add("retrain", Schedule(kind="weekly", at="06:00", weekday=5), retrain)
    assert sch.run_pending() == []                         # last Saturday's slot is not run at first start
    clock.now = ts("2026-10-10 06:00:30")
    assert sch.run_pending() == ["retrain"] and calls == [ts("2026-10-10 06:00")]
    assert sch.run_pending() == []                         # never twice for the same slot
    assert sch.state["retrain"].last_ok and sch.state["retrain"].last_result == {"ok": 1}


def test_restart_catches_up_a_recent_slot_once_and_skips_a_stale_one(tmp_path):
    clock = Clock("2026-10-09 12:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)
    sch.add("retrain", Schedule(kind="weekly", at="06:00", weekday=5, max_late_hours=30), lambda slot: {"slot": str(slot)})
    sch.add("nightly", Schedule(kind="daily", at="23:10", max_late_hours=8), lambda slot: None)
    # the service was down over Saturday morning and comes back Saturday evening: retrain catches up once
    clock.now = ts("2026-10-10 20:00")
    restarted = Scheduler(tmp_path / "s.json", clock=clock)
    restarted.add("retrain", Schedule(kind="weekly", at="06:00", weekday=5, max_late_hours=30), lambda slot: {"slot": str(slot)})
    restarted.add("nightly", Schedule(kind="daily", at="23:10", max_late_hours=8), lambda slot: None)
    assert restarted.run_pending() == ["retrain"]
    # nightly's Friday 23:10 slot is 21 h late: recorded as skipped, not run
    st = restarted.state["nightly"]
    assert st.last_slot == ts("2026-10-09 23:10") and st.last_skipped is not None and st.runs == 0


def test_a_failing_job_is_recorded_and_does_not_stop_the_others(tmp_path):
    clock = Clock("2026-10-07 23:00")
    sch = Scheduler(tmp_path / "s.json", clock=clock)

    def boom(slot):
        raise RuntimeError("no ticks table")
    sch.add("costs", Schedule(kind="daily", at="23:10"), boom)
    sch.add("other", Schedule(kind="daily", at="23:10"), lambda slot: {"fine": True})
    clock.now = ts("2026-10-07 23:11")
    assert sorted(sch.run_pending()) == ["costs", "other"]
    assert sch.state["costs"].last_ok is False and "no ticks table" in (sch.state["costs"].last_error or "")
    assert sch.state["other"].last_ok is True
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["jobs"]["costs"]["failures"] == 1
    status = sch.status()
    assert status["jobs"]["costs"]["next_slot"] == ts("2026-10-08 23:10").isoformat()


def test_duplicate_job_names_are_refused(tmp_path):
    sch = Scheduler(tmp_path / "s.json", clock=Clock("2026-10-07 12:00"))
    sch.add("a", Schedule(kind="daily", at="01:00"), lambda slot: None)
    with pytest.raises(ValueError):
        sch.add("a", Schedule(kind="daily", at="02:00"), lambda slot: None)

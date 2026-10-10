"""Research scheduler: the VPS service that runs the nightly cost and quality jobs, the Saturday retrain and the
monthly bounded research loop in the daily break, overnight and at weekends (design: Deployment).

Rules that make it safe to restart at any time:
* every time is UTC; a schedule names the slot, the state file records the last slot each job ran for;
* a job runs at most once per slot, and a job with no history waits for its next slot instead of firing at start;
* after downtime a missed slot is caught up once if it is no older than `max_late`, otherwise it is skipped and
  the skip is recorded (a Saturday retrain should not start on a Wednesday afternoon);
* one job failing never stops the others; the error is recorded and the next slot runs normally;
* state/scheduler.json doubles as the heartbeat the dashboard reads (`/api/jobs`); the supervisor does not read it.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, Record, UtcTimestamp

log = logging.getLogger("goldbot.scheduler")

LOOK_DAYS = 370                 # slot search horizon: a schedule limited to one month a year still finds its slot
JobFn = Callable[[pd.Timestamp], dict[str, Any] | None]


class Schedule(FrozenRecord):
    """daily: every listed weekday at `at`; weekly: `weekday` at `at`; monthly: the first `weekday` of the month
    (or calendar `day` when weekday is None) at `at`. Weekdays are 0=Monday .. 6=Sunday. `months` (1..12) limits any
    kind to those calendar months: a monthly schedule on months 1, 4, 7, 10 runs quarterly."""

    kind: Literal["daily", "weekly", "monthly"]
    at: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    weekday: int | None = Field(None, ge=0, le=6)
    day: int | None = Field(None, ge=1, le=28)
    months: tuple[int, ...] = tuple(range(1, 13))
    max_late_hours: float = 12.0

    @model_validator(mode="after")
    def _check(self) -> "Schedule":
        if self.kind == "weekly" and self.weekday is None:
            raise ValueError("weekly schedule needs weekday")
        if self.kind == "monthly" and (self.weekday is None) == (self.day is None):
            raise ValueError("monthly schedule needs exactly one of weekday (first <weekday> of month) or day")
        if not self.months or any(m < 1 or m > 12 for m in self.months):
            raise ValueError("months must be calendar months 1..12")
        return self

    def _slot_on(self, d: pd.Timestamp) -> pd.Timestamp | None:
        """The slot on calendar day `d` (UTC midnight), if this schedule has one that day."""
        if d.month not in self.months:
            return None
        hh, mm = (int(x) for x in self.at.split(":"))
        slot = d + pd.Timedelta(hours=hh, minutes=mm)
        if self.kind == "daily":
            return slot if d.dayofweek in self.weekdays else None
        if self.kind == "weekly":
            return slot if d.dayofweek == self.weekday else None
        if self.day is not None:
            return slot if d.day == self.day else None
        return slot if d.dayofweek == self.weekday and d.day <= 7 else None

    def last_slot(self, now: pd.Timestamp) -> pd.Timestamp | None:
        """Most recent slot at or before `now` (looks back far enough for monthly and quarterly schedules)."""
        today = now.tz_convert("UTC").normalize()
        for back in range(0, LOOK_DAYS):
            s = self._slot_on(today - pd.Timedelta(days=back))
            if s is not None and s <= now:
                return s
        return None

    def next_slot(self, now: pd.Timestamp) -> pd.Timestamp:
        today = now.tz_convert("UTC").normalize()
        for ahead in range(0, LOOK_DAYS):
            s = self._slot_on(today + pd.Timedelta(days=ahead))
            if s is not None and s > now:
                return s
        raise ValueError(f"no slot within {LOOK_DAYS} days")  # unreachable for valid schedules


class JobState(Record):
    last_slot: UtcTimestamp | None = None      # the slot the job last ran (or was skipped) for
    last_started: UtcTimestamp | None = None
    last_finished: UtcTimestamp | None = None
    last_ok: bool | None = None
    last_error: str | None = None
    last_result: dict[str, Any] | None = None
    last_skipped: UtcTimestamp | None = None
    runs: int = 0
    failures: int = 0


class Job(Record):
    name: str
    schedule: Schedule
    fn: JobFn


class Scheduler:
    def __init__(self, state_path: str | Path, clock: Callable[[], pd.Timestamp] | None = None):
        self.path = Path(state_path)
        self.clock = clock or (lambda: pd.Timestamp.now("UTC"))
        self.jobs: dict[str, Job] = {}
        raw = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.state: dict[str, JobState] = {k: JobState.model_validate({f: x for f, x in v.items() if f != "next_slot"})
                                           for k, v in raw.get("jobs", {}).items()}

    def add(self, name: str, schedule: Schedule, fn: JobFn) -> None:
        if name in self.jobs:
            raise ValueError(f"job {name!r} already registered")
        self.jobs[name] = Job(name=name, schedule=schedule, fn=fn)
        if name not in self.state:
            # first sight of a job: anchor it to the current slot so it waits for the next one
            self.state[name] = JobState(last_slot=schedule.last_slot(self.clock()))
        self._save()

    def due(self, now: pd.Timestamp) -> list[tuple[Job, pd.Timestamp]]:
        out = []
        for name, job in self.jobs.items():
            slot = job.schedule.last_slot(now)
            st = self.state[name]
            if slot is not None and (st.last_slot is None or slot > st.last_slot):
                out.append((job, slot))
        return out

    def run_pending(self) -> list[str]:
        """Run every job whose slot has arrived; returns the names run (skips are recorded, not returned)."""
        ran = []
        for job, slot in self.due(self.clock()):
            st = self.state[job.name]
            now = self.clock()
            if now - slot > pd.Timedelta(hours=job.schedule.max_late_hours):
                log.warning("skipping %s: slot %s missed by %s", job.name, slot, now - slot)
                st.last_slot, st.last_skipped = slot, now
                self._save()
                continue
            st.last_slot, st.last_started = slot, now
            self._save()
            log.info("running %s for slot %s", job.name, slot)
            try:
                result = job.fn(slot)
                st.last_ok, st.last_error, st.last_result = True, None, result
            except Exception as exc:   # one job's failure must not take the scheduler down
                st.last_ok, st.failures = False, st.failures + 1
                st.last_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"[-4000:]
                log.exception("job %s failed", job.name)
            st.runs += 1
            st.last_finished = self.clock()
            self._save()
            ran.append(job.name)
        return ran

    def status(self) -> dict[str, Any]:
        now = self.clock()
        return {"ts": now.isoformat(), "jobs": {
            n: {**self.state[n].model_dump(mode="json"), "next_slot": self.jobs[n].schedule.next_slot(now).isoformat()}
            for n in self.jobs}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = self.clock()
        payload = {"ts": now.isoformat(), "jobs": {
            n: {**s.model_dump(mode="json"), "next_slot": self.jobs[n].schedule.next_slot(now).isoformat() if n in self.jobs else None}
            for n, s in self.state.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=str))
        tmp.replace(self.path)    # atomic: a crash mid-write never leaves a torn state file

    def run_forever(self, poll_s: float = 30.0) -> None:  # pragma: no cover - service loop
        while True:
            self.run_pending()
            self._save()          # heartbeat
            time.sleep(poll_s)

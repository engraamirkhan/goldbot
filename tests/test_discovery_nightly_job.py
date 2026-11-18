"""The brain's `discovery_nightly` scheduler job (ADR 0090; ADR 0083's reasons for retiring `autodisc_nightly`): the
search runs in a child process at a low CPU priority, under a memory cap and a wall-clock budget, in a quiet slot on
the single-threaded scheduler, and is killed when it overruns."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.config import DiscoverySettings, load_settings
from goldbot.ops import bounded as B
from goldbot.ops.jobs import JOBS, discovery_nightly
from goldbot.ops.scheduler import schedule_of

SLOT = pd.Timestamp("2026-11-18 01:00", tz="UTC")


def env_var(name: str) -> str | None:
    """Module level, so a spawned child can unpickle it."""
    return os.environ.get(name)


def test_the_job_is_registered_with_a_daily_slot_clear_of_the_other_nightly_jobs():
    s = load_settings()
    assert JOBS["discovery_nightly"] is discovery_nightly
    cfg = s.scheduler.discovery_nightly
    assert cfg.kind == "daily" and cfg.max_late_hours <= 2
    schedule_of(cfg)
    start = pd.Timedelta(cfg.at + ":00")
    end = start + pd.Timedelta(seconds=s.research.discovery.budget_s + s.research.discovery.kill_grace_s)
    for name in type(s.scheduler).model_fields:
        other = getattr(s.scheduler, name)
        if name == "discovery_nightly" or other.kind != "daily":
            continue
        at = pd.Timedelta(other.at + ":00")
        assert not (start <= at < end), f"{name} at {other.at} falls inside the discovery window"


def test_the_job_skips_without_starting_a_child_when_off():
    ctx = SimpleNamespace(settings=load_settings(), state_dir=None)
    assert load_settings().research.discovery.enabled is False
    assert "skipped" in discovery_nightly(ctx, SLOT)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [{"nice": 5}, {"budget_s": 9000}, {"memory_mb": 64}, {"kill_grace_s": 3600},
                                 {"timeframes": ["15m"]}, {"timeframes": []}])
def test_discovery_settings_can_only_stay_gentle_on_the_brain(bad):
    with pytest.raises(ValueError):
        DiscoverySettings(**bad)


def test_a_child_that_overruns_its_budget_is_killed_and_reported():
    t0 = time.monotonic()
    out = B.run_bounded(time.sleep, (60,), budget_s=1.0, nice=15, memory_mb=None)
    assert out.timed_out and not out.ok and time.monotonic() - t0 < 30


def test_the_child_runs_at_the_configured_nice_level_and_returns_its_value():
    out = B.run_bounded(os.nice, (0,), budget_s=60, nice=15, memory_mb=None)
    assert out.ok and out.value >= 15


def test_a_child_error_is_reported_not_raised():
    out = B.run_bounded(int, ("not a number",), budget_s=60, nice=15, memory_mb=None)
    assert not out.ok and not out.timed_out and "ValueError" in (out.error or "")


def test_the_memory_cap_sets_the_data_segment_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(B.resource, "setrlimit", lambda what, lim: calls.append((what, lim)))
    monkeypatch.setattr(B.sys, "platform", "linux")
    B.cap_memory(1024)
    assert calls == [(B.resource.RLIMIT_DATA, (1024 * 2**20, 1024 * 2**20))]


def test_the_child_starts_with_one_blas_thread_and_the_parent_env_is_restored(monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    out = B.run_bounded(env_var, ("OMP_NUM_THREADS",), budget_s=60, nice=15, memory_mb=None)
    assert out.value == "1" and "OMP_NUM_THREADS" not in os.environ


def test_an_enabled_job_without_bars_fails_through_its_child_instead_of_skipping(tmp_path):
    s = load_settings()
    on = s.model_copy(update={"research": s.research.model_copy(update={
        "discovery": s.research.discovery.model_copy(update={"enabled": True, "bars_path": None})})})
    ctx = SimpleNamespace(settings=on, state_dir=tmp_path)
    with pytest.raises(RuntimeError, match="bars_path"):
        discovery_nightly(ctx, SLOT)  # type: ignore[arg-type]
